#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cloudflare IP优选测速脚本
从 TLS.txt + DIY 源读取节点，查询国家码并测速，生成 ip-ua.txt / ip-ua.csv
"""

import re
import os
import csv
import time
import socket
import requests
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple, Optional
import urllib3

# ================= 配置 =================
TLS_FILE = "ip/ip.txt"  # CloudflareST 转出来的文件

# 你的 DIY 源（两者都给时，优先使用 URL，其次本地文件；都缺则跳过DIY）
DIY_URL = "https://raw.githubusercontent.com/kexoub/CloudflareST_ip-ua/refs/heads/main/ip/diy.txt"
DIY_FILE = "diy.txt"  # 可选：仓库里的本地 diy 文件

OUTPUT_TXT = "ip-no.txt"
OUTPUT_CSV = "ip-no.csv"

# 并发与限速
MAX_WORKERS = 15
MAX_WORKERS_SPEEDTEST = 3  # 下载测速并发数
TIMEOUT = 5
RETRIES = 2
SLEEP_BETWEEN_REQ = 0.05

# 测速配置
MAX_OUTPUT_NODES = 15  # 最终只输出15个最强的节点
PING_COUNT = 4  # ping次数
SPEEDTEST_COUNT = 2  # 下载测速次数
SPEEDTEST_FILE_SIZE = 2 * 1024 * 1024  # 2MB 测试文件
MIN_DOWNLOAD_SPEED = 4.0  # 最低下载速度 MB/s
MAX_LATENCY = 300  # 最大延迟 ms
MAX_PACKET_LOSS = 1.0  # 最大丢包率 %

# 使用多个测试URL，增加成功率
TEST_URLS = [
    "https://speed.cloudflare.com/__down?bytes={}",
    "https://cf.xiu2.xyz/url",
    "https://cachefly.cachefly.net/{}mb.test",
    "http://speedtest.ftp.otenet.gr/files/test{}.db"
]

# 只用一个提供商，保证结果稳定
API_URL = "http://ip-api.com/json/{}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

# 正则
FULL_PATTERN = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3}):(\d+)")
IP_PATTERN = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")

# =============== 工具函数 ===============
def fetch_text(url: str, timeout: int = 10) -> str:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return ""

def read_text_file(path: str) -> str:
    if not os.path.exists(path):
        return ""
    try:
        return open(path, "r", encoding="utf-8", errors="ignore").read()
    except Exception:
        return ""

def parse_text_to_items(text: str) -> List[Dict[str, str]]:
    """解析任意文本为 [{'ip': ..., 'port': ...}]"""
    cleaned_lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if " #" in line:
            line = line.split(" #", 1)[0].strip()
        cleaned_lines.append(line)
    
    text = "\n".join(cleaned_lines)
    items = []
    seen = set()
    
    for ip, port in FULL_PATTERN.findall(text):
        if ip not in seen:
            items.append({"ip": ip, "port": port})
            seen.add(ip)
    
    for ip in IP_PATTERN.findall(text):
        if ip not in seen:
            items.append({"ip": ip, "port": "443"})
            seen.add(ip)
    
    return items

def get_cc_ipapi(ip: str) -> str:
    """固定使用 ip-api.com，带重试；失败返回 'XX'"""
    for _ in range(RETRIES + 1):
        try:
            r = requests.get(API_URL.format(ip), headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                cc = data.get("countryCode", "")
                if isinstance(cc, str) and len(cc) == 2 and cc.isalpha():
                    return cc.upper()
        except Exception:
            pass
        time.sleep(0.2)
    return "XX"

def tcp_ping(ip: str, port: int, timeout: float = 3.0) -> Tuple[bool, float]:
    """TCP ping 测试延迟和连通性"""
    try:
        start_time = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        sock.close()
        latency = (time.time() - start_time) * 1000  # 转换为毫秒
        if result == 0:
            return True, latency
        else:
            return False, latency
    except Exception:
        return False, 9999.0

def quick_ping_test(ip: str, port: int, count: int = 2) -> Tuple[float, float]:
    """快速ping测试，用于初步筛选"""
    success_count = 0
    total_latency = 0.0
    
    for i in range(count):
        success, latency = tcp_ping(ip, port, timeout=2.0)
        if success:
            success_count += 1
            total_latency += latency
        time.sleep(0.05)  # 短暂间隔
    
    if success_count > 0:
        avg_latency = total_latency / success_count
        packet_loss = ((count - success_count) / count) * 100
    else:
        avg_latency = 9999.0
        packet_loss = 100.0
    
    return avg_latency, packet_loss

def download_speed_test(ip: str, port: int, test_size: int = SPEEDTEST_FILE_SIZE, timeout: int = 10) -> float:
    """HTTP下载速度测试，返回MB/s"""
    # 禁用SSL警告
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    # 尝试多个测试URL
    for test_url_template in TEST_URLS:
        try:
            # 根据URL模板生成实际URL
            if "{}" in test_url_template:
                # 对于需要大小的URL
                size_param = test_size // (1024 * 1024)  # 转换为MB
                if size_param < 1:
                    size_param = 1
                test_url = test_url_template.format(size_param)
            else:
                test_url = test_url_template

            # 对于Cloudflare特定的测速URL，我们直接使用
            # 对于其他URL，我们尝试通过指定IP访问
            if "cloudflare.com" in test_url or "cf.xiu2.xyz" in test_url:
                # 使用原始URL
                final_url = test_url
            else:
                # 替换为指定IP
                url_parts = test_url.split("//", 1)
                if len(url_parts) > 1:
                    domain_path = url_parts[1].split("/", 1)
                    if len(domain_path) > 1:
                        path = domain_path[1]
                    else:
                        path = ""
                    final_url = f"{url_parts[0]}//{ip}:{port}/{path}"
                else:
                    final_url = test_url

            # 设置Host头，确保请求正确路由
            host_header = None
            if "cloudflare.com" in test_url:
                host_header = "speed.cloudflare.com"
            elif "cf.xiu2.xyz" in test_url:
                host_header = "cf.xiu2.xyz"
            
            headers = HEADERS.copy()
            if host_header:
                headers["Host"] = host_header

            start_time = time.time()
            response = requests.get(final_url, headers=headers, timeout=timeout, stream=True, verify=False)
            
            if response.status_code != 200:
                continue

            # 读取数据来计算速度
            downloaded = 0
            for chunk in response.iter_content(chunk_size=64*1024):  # 64KB chunks
                downloaded += len(chunk)
                if downloaded >= test_size:
                    break
            
            total_time = time.time() - start_time
            response.close()
            
            if total_time > 0:
                speed_mbps = (downloaded / total_time) / (1024 * 1024)  # MB/s
                return speed_mbps
                
        except Exception as e:
            continue
    
    return 0.0

def detailed_speed_test(ip: str, port: int) -> Dict[str, float]:
    """详细测速：延迟、丢包率、下载速度"""
    print(f"  测试 {ip}:{port}...")
    
    # 测试延迟和丢包率
    latency, packet_loss = quick_ping_test(ip, port, PING_COUNT)
    print(f"  {ip}:{port} 延迟: {latency:.1f}ms, 丢包: {packet_loss:.1f}%")
    
    # 如果延迟或丢包率不合格，直接返回
    if latency > MAX_LATENCY or packet_loss > MAX_PACKET_LOSS:
        print(f"  {ip}:{port} 延迟或丢包率不合格")
        return {
            "latency": latency,
            "packet_loss": packet_loss,
            "download_speed": 0.0,
            "qualified": False
        }
    
    # 测试下载速度（多次测试取平均值）
    total_speed = 0.0
    valid_tests = 0
    
    for i in range(SPEEDTEST_COUNT):
        speed = download_speed_test(ip, port)
        if speed > 0:
            total_speed += speed
            valid_tests += 1
        print(f"  {ip}:{port} 第{i+1}次下载速度: {speed:.2f} MB/s")
        time.sleep(1)  # 测试间隔
    
    avg_speed = total_speed / valid_tests if valid_tests > 0 else 0.0
    qualified = (latency <= MAX_LATENCY and packet_loss <= MAX_PACKET_LOSS and avg_speed >= MIN_DOWNLOAD_SPEED)
    
    if qualified:
        print(f"  {ip}:{port} ✅ 合格 - 平均速度: {avg_speed:.2f} MB/s")
    else:
        print(f"  {ip}:{port} ❌ 不合格 - 平均速度: {avg_speed:.2f} MB/s")
    
    return {
        "latency": latency,
        "packet_loss": packet_loss,
        "download_speed": avg_speed,
        "qualified": qualified
    }

def batch_quick_ping(ip_port_list: List[Tuple[str, int]]) -> List[Tuple[str, int, float, float]]:
    """批量快速ping测试，用于初步筛选"""
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_ip = {
            executor.submit(quick_ping_test, ip, port): (ip, port) 
            for ip, port in ip_port_list
        }
        
        for future in as_completed(future_to_ip):
            ip, port = future_to_ip[future]
            try:
                latency, packet_loss = future.result()
                results.append((ip, port, latency, packet_loss))
            except Exception:
                results.append((ip, port, 9999.0, 100.0))
    
    return results

def batch_detailed_speed_test(ip_port_list: List[Tuple[str, int]]) -> Dict[Tuple[str, int], Dict[str, float]]:
    """批量详细测速"""
    results = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS_SPEEDTEST) as executor:
        future_to_ip = {
            executor.submit(detailed_speed_test, ip, port): (ip, port) 
            for ip, port in ip_port_list
        }
        
        for future in as_completed(future_to_ip):
            ip_port = future_to_ip[future]
            try:
                results[ip_port] = future.result()
            except Exception:
                results[ip_port] = {
                    "latency": 9999.0,
                    "packet_loss": 100.0,
                    "download_speed": 0.0,
                    "qualified": False
                }
    
    return results

def batch_get_cc(ips: List[str]) -> Dict[str, str]:
    """并发批量查询"""
    results: Dict[str, str] = {}
    if not ips:
        return results
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        fut2ip = {ex.submit(get_cc_ipapi, ip): ip for ip in ips}
        for fut in as_completed(fut2ip):
            ip = fut2ip[fut]
            try:
                results[ip] = fut.result()
            except Exception:
                results[ip] = "XX"
            time.sleep(SLEEP_BETWEEN_REQ)
    
    return results

# =============== 解析入口 ===============
def parse_tls_file(filename: str) -> List[Dict[str, str]]:
    print(f"📄 读取 {filename} ...")
    text = read_text_file(filename)
    if not text:
        print(f"⚠️ 未找到或为空：{filename}")
        return []
    
    items = parse_text_to_items(text)
    print(f"✅ {filename} 解析到 {len(items)} 条")
    return items

def parse_diy_source() -> List[Dict[str, str]]:
    # 先尝试 URL
    if DIY_URL:
        print(f"🌐 获取 DIY URL：{DIY_URL}")
        text = fetch_text(DIY_URL, timeout=10)
        if text:
            items = parse_text_to_items(text)
            print(f"✅ DIY(URL) 解析到 {len(items)} 条")
            return items
        else:
            print("⚠️ DIY URL 获取失败或为空，尝试本地文件")
    
    # 再尝试本地文件
    if DIY_FILE:
        print(f"📄 读取 DIY 文件：{DIY_FILE}")
        text = read_text_file(DIY_FILE)
        if text:
            items = parse_text_to_items(text)
            print(f"✅ DIY(FILE) 解析到 {len(items)} 条")
            return items
        else:
            print("⚠️ DIY 文件不存在或为空")
    
    print("ℹ️ 未使用 DIY 源")
    return []

# =============== 主流程 ===============
def main():
    # 禁用SSL警告
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    # 1) 读取 TLS 和 DIY
    tls_items = parse_tls_file(TLS_FILE)
    diy_items = parse_diy_source()
    
    if not tls_items and not diy_items:
        print("❌ 没有可用的输入（TLS.txt 与 DIY 均为空）")
        return
    
    # 2) 合并 & 去重（按 IP）；若 DIY 指定了端口，优先覆盖
    by_ip: Dict[str, Dict[str, str]] = {}
    
    for it in tls_items:
        by_ip[it["ip"]] = {"ip": it["ip"], "port": it["port"]}
    
    for it in diy_items:
        ip, port = it["ip"], it["port"]
        if ip not in by_ip:
            by_ip[ip] = {"ip": ip, "port": port}
        else:
            # DIY 有端口则覆盖
            if port and port.isdigit():
                by_ip[ip]["port"] = port
    
    ips = list(by_ip.keys())
    print(f"🧮 合并后唯一 IP：{len(ips)} 个")
    
    # 3) 快速筛选：先测试所有节点的延迟和丢包率
    print(f"⚡ 快速筛选节点（测试延迟和丢包率）...")
    ip_port_list = [(info["ip"], int(info["port"])) for info in by_ip.values()]
    quick_results = batch_quick_ping(ip_port_list)
    
    # 4) 筛选合格节点并按延迟排序
    qualified_quick = []
    for ip, port, latency, packet_loss in quick_results:
        if latency <= MAX_LATENCY and packet_loss <= MAX_PACKET_LOSS:
            qualified_quick.append((ip, port, latency, packet_loss))
    
    # 按延迟排序，取前30个进行详细测速
    qualified_quick.sort(key=lambda x: x[2])  # 按延迟排序
    candidate_nodes = qualified_quick[:30]  # 取30个候选节点
    
    print(f"📊 快速筛选结果：{len(qualified_quick)}/{len(ip_port_list)} 个节点合格，详细测速前 {len(candidate_nodes)} 个候选节点")
    
    # 5) 对候选节点进行详细测速
    print(f"🚀 详细测速 {len(candidate_nodes)} 个候选节点...")
    candidate_ip_port_list = [(ip, port) for ip, port, _, _ in candidate_nodes]
    detailed_results = batch_detailed_speed_test(candidate_ip_port_list)
    
    # 6) 筛选最终合格节点并按下载速度排序
    final_nodes = []
    for (ip, port), speed_info in detailed_results.items():
        if speed_info["qualified"]:
            final_nodes.append({
                "ip": ip,
                "port": str(port),
                "latency": speed_info["latency"],
                "packet_loss": speed_info["packet_loss"],
                "download_speed": speed_info["download_speed"]
            })
    
    # 按下载速度排序，只取前15个最强的
    final_nodes.sort(key=lambda x: x["download_speed"], reverse=True)
    final_nodes = final_nodes[:MAX_OUTPUT_NODES]  # 只保留最强的15个
    
    print(f"📈 详细测速结果：{len(final_nodes)} 个最强节点")
    
    # 7) 如果下载测速都失败，则放宽标准使用延迟最低的节点
    if len(final_nodes) == 0:
        print("⚠️ 下载测速无合格节点，使用延迟最低的节点...")
        # 取延迟最低的15个节点
        qualified_quick.sort(key=lambda x: x[2])
        for i, (ip, port, latency, packet_loss) in enumerate(qualified_quick[:MAX_OUTPUT_NODES]):
            final_nodes.append({
                "ip": ip,
                "port": str(port),
                "latency": latency,
                "packet_loss": packet_loss,
                "download_speed": 0.0  # 标记下载速度未知
            })
        print(f"📊 使用延迟最低的 {len(final_nodes)} 个节点")
    
    # 8) 查询最终节点的国家码
    if final_nodes:
        final_ips = [node["ip"] for node in final_nodes]
        print(f"🌍 查询 {len(final_ips)} 个最终节点的国家码...")
        cc_map = batch_get_cc(final_ips)
        for node in final_nodes:
            node["country"] = cc_map.get(node["ip"], "XX")
    
    # 9) 输出 TXT
    with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
        f.write("# Cloudflare 优选节点 (TLS)\n")
        f.write(f"# 测速标准：延迟≤{MAX_LATENCY}ms，丢包率≤{MAX_PACKET_LOSS}%，下载速度≥{MIN_DOWNLOAD_SPEED}MB/s\n")
        f.write(f"# 输出最强的 {len(final_nodes)} 个节点\n")
        f.write("# 格式: IP:端口#国家代码\n\n")
        
        for node in final_nodes:
            line = f"{node['ip']}:{node['port']}#{node['country']}"
            f.write(line + "\n")
    
    # 10) 输出 CSV（包含详细测速信息）
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as csvfile:
        w = csv.writer(csvfile)
        w.writerow(["ip", "port", "country", "latency_ms", "packet_loss_percent", "download_speed_mbps"])
        for node in final_nodes:
            w.writerow([
                node["ip"],
                node["port"],
                node["country"],
                round(node["latency"], 2),
                round(node["packet_loss"], 2),
                round(node["download_speed"], 2)
            ])
    
    print(f"🎉 已生成 {OUTPUT_TXT} / {OUTPUT_CSV}（最强的 {len(final_nodes)} 个节点）")
    
    # 显示所有最终节点
    if final_nodes:
        print("\n🚀 最强节点排行榜:")
        for i, node in enumerate(final_nodes):
            speed_info = f"速度:{node['download_speed']:.2f}MB/s" if node['download_speed'] > 0 else "速度:未知"
            print(f"  {i+1}. {node['ip']}:{node['port']}#{node['country']} "
                  f"- 延迟:{node['latency']:.1f}ms "
                  f"丢包:{node['packet_loss']:.1f}% "
                  f"{speed_info}")
    else:
        print("❌ 没有找到符合条件的节点")

if __name__ == "__main__":
    main()
