#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 TLS.txt + DIY 源（可本地或URL）读取节点，查询国家码并生成 ip-ua.txt / ip-ua.csv
- 输入支持 "IP:端口" 与 纯 "IP"（自动补 443）
- 为了结果稳定：仅使用 ip-api.com 作为地理库
- 去重按 IP；若 DIY 指定了端口，优先使用 DIY 端口
"""

import re
import os
import csv
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple, Optional

# ================= 配置 =================
TLS_FILE = "TLS.txt"                 # CloudflareST 转出来的文件
# 你的 DIY 源（两者都给时，优先使用 URL，其次本地文件；都缺则跳过DIY）
DIY_URL = "https://raw.githubusercontent.com/kexoub/CloudflareST_ip-ua/refs/heads/main/ip-no.txt"
DIY_FILE = "diy.txt"                 # 可选：仓库里的本地 diy 文件

OUTPUT_TXT = "ip-ua.txt"
OUTPUT_CSV = "ip-ua.csv"

# 并发与限速
MAX_WORKERS = 15
TIMEOUT = 5
RETRIES = 2
SLEEP_BETWEEN_REQ = 0.05

# 只用一个提供商，保证结果稳定
API_URL = "http://ip-api.com/json/{}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

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
    """
    解析任意文本为 [{'ip': ..., 'port': ...}]
    - 允许行内注释，以 # 开头的整行或 'ip #comment' 的注释（处理时截断）
    - 优先匹配 IP:端口，再补充纯 IP（端口默认 443）
    - 基于 IP 去重（首次出现的端口先记录；后续可在合并阶段做覆盖策略）
    """
    # 去掉行内注释（# 后面的内容），但不影响 # 作为我们最终输出的 “#CC”
    cleaned_lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # 整行注释
        if line.startswith("#"):
            continue
        # 行内注释截断
        if " #" in line:
            line = line.split(" #", 1)[0].strip()
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    items = []
    seen = set()

    # 先抓 IP:端口
    for ip, port in FULL_PATTERN.findall(text):
        if ip not in seen:
            items.append({"ip": ip, "port": port})
            seen.add(ip)

    # 再抓纯 IP（避免重复）
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

    # 3) 查询国家码
    print(f"🌍 使用 ip-api.com 查询国家码（{len(ips)} 个 IP）...")
    cc_map = batch_get_cc(ips)

    # 4) 生成输出行
    lines = []
    for ip, info in by_ip.items():
        port = info.get("port") or "443"
        cc = cc_map.get(ip, "XX") or "XX"
        lines.append(f"{ip}:{port}#{cc}")

    # 5) 排序：按国家码，再按整行
    lines_sorted = sorted(lines, key=lambda x: (x.split("#")[-1], x))

    # 6) 输出 TXT
    with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
        f.write("# Cloudflare 优选节点 (TLS)\n")
        f.write("# 格式: IP:端口#国家代码\n\n")
        for line in lines_sorted:
            f.write(line + "\n")

    # 7) 输出 CSV（ip,port,country）
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as csvfile:
        w = csv.writer(csvfile)
        w.writerow(["ip", "port", "country"])
        for line in lines_sorted:
            ip, rest = line.split(":", 1)
            port, cc = rest.split("#", 1)
            w.writerow([ip, port, cc])

    print(f"🎉 已生成 {OUTPUT_TXT} / {OUTPUT_CSV}（共 {len(lines_sorted)} 条）")

if __name__ == "__main__":
    main()
