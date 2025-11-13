import requests
from bs4 import BeautifulSoup
import re
import os
import traceback
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

# 目标URL列表
urls = [
    'https://ip.164746.xyz', 
    'https://cf.090227.xyz/ip.164746.xyz',
    'https://ip.haogege.xyz',
    'https://api.uouin.com/cloudflare.html',
    'https://addressesapi.090227.xyz/CloudFlareYes',
    'https://ipdb.api.030101.xyz/?type=cfv4;proxy',
    'https://ipdb.api.030101.xyz/?type=bestcf&country=true',
    'https://ipdb.api.030101.xyz/?type=bestproxy&country=true',
    'https://www.wetest.vip/page/edgeone/address_v4.html',
    'https://www.wetest.vip/page/cloudfront/address_v4.html',
    'https://www.wetest.vip/page/cloudflare/address_v4.html',
    'https://addressesapi.090227.xyz/ct', 
    'https://addressesapi.090227.xyz/cm', 
    'https://addressesapi.090227.xyz/cu', 
    'https://raw.githubusercontent.com/qwer-search/bestip/refs/heads/main/kejilandbestip.txt',
    'https://stock.hostmonit.com/CloudFlareYes',
    'https://cf.090227.xyz',
    'https://ct.090227.xyz',
    'https://cmcc.090227.xyz',
    'http://ip.flares.cloud',
    'https://vps789.com/cfip/?remarks=ip',
    'https://ipdb.030101.xyz/bestcfv4',
    'https://www.wetest.vip',
    'https://cf.vvhan.com'
]

# 正则表达式用于匹配IP地址
ip_pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'

# 检查ip.txt文件是否存在,如果存在则删除它
if os.path.exists('ip.txt'):
    os.remove('ip.txt')

# 使用集合存储IP地址实现自动去重
unique_ips = set()

# 统计成功和失败的URL数量
success_count = 0
fail_count = 0

# 初始化Selenium WebDriver（用于需要JS渲染的页面）
def init_webdriver():
    try:
        chrome_options = Options()
        chrome_options.add_argument('--headless')  # 无头模式
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
        
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(30)
        return driver
    except Exception as e:
        print(f"无法初始化WebDriver: {e}")
        return None

# 使用requests获取页面（适用于简单页面）
def get_with_requests(url):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.text
    except Exception as e:
        print(f"  × requests获取失败: {e}")
        return None

# 使用selenium获取页面（适用于需要JS的页面）
def get_with_selenium(url, driver):
    try:
        driver.get(url)
        # 等待页面加载完成
        WebDriverWait(driver, 12).until(
            lambda driver: driver.execute_script('return document.readyState') == 'complete'
        )
        # 额外等待5秒确保动态内容加载
        time.sleep(5)
        return driver.page_source
    except TimeoutException:
        print(f"  × Selenium加载超时，但尝试返回当前内容")
        return driver.page_source
    except Exception as e:
        print(f"  × Selenium获取失败: {e}")
        return None

# 处理需要特殊等待的URL
js_heavy_urls = [
    'https://stock.hostmonit.com/CloudFlareYes',
    'https://cf.090227.xyz',
    'https://ct.090227.xyz',
    'https://cmcc.090227.xyz',
    'http://ip.flares.cloud',
    'https://vps789.com/cfip/?remarks=ip',
    'https://ipdb.030101.xyz/bestcfv4',
    'https://www.wetest.vip',
    'https://cf.vvhan.com'
]

print("初始化WebDriver...")
driver = init_webdriver()

for url in urls:
    try:
        print(f"正在处理: {url}")
        html_content = None
        
        # 判断使用哪种方式获取页面
        if url in js_heavy_urls and driver:
            print("  使用Selenium获取（需要JS渲染）")
            html_content = get_with_selenium(url, driver)
        else:
            print("  使用Requests获取")
            html_content = get_with_requests(url)
        
        if html_content:
            # 使用正则表达式查找IP地址
            ip_matches = re.findall(ip_pattern, html_content, re.IGNORECASE)
            
            # 过滤有效的IP地址（排除本地和无效IP）
            valid_ips = []
            for ip in ip_matches:
                parts = ip.split('.')
                if len(parts) == 4 and all(0 <= int(part) <= 255 for part in parts):
                    # 排除本地IP段
                    if not (ip.startswith('127.') or ip.startswith('10.') or 
                           ip.startswith('192.168.') or ip.startswith('169.254.') or
                           (ip.startswith('172.') and 16 <= int(parts[1]) <= 31)):
                        valid_ips.append(ip)
            
            # 将找到的IP添加到集合中（自动去重）
            unique_ips.update(valid_ips)
            
            print(f"  √ 成功从 {url} 获取 {len(valid_ips)} 个有效IP地址")
            success_count += 1
        else:
            print(f"  × 无法获取 {url} 的内容")
            fail_count += 1
            
    except Exception as e:
        print(f"  × 处理 {url} 时发生错误: {e}")
        print(f"     详细错误: {traceback.format_exc()}")
        fail_count += 1

# 关闭WebDriver
if driver:
    try:
        driver.quit()
        print("WebDriver已关闭")
    except:
        pass

# 将去重后的IP地址按数字顺序排序后写入文件
if unique_ips:
    # 按IP地址的数字顺序排序（非字符串顺序）
    sorted_ips = sorted(unique_ips, key=lambda ip: [int(part) for part in ip.split('.')])
    
    # 确保ip目录存在
    os.makedirs('ip', exist_ok=True)
    
    with open('ip/ip.txt', 'w', encoding='utf-8') as file:
        for ip in sorted_ips:
            file.write(ip + '\n')
    
    print(f"\n处理完成!")
    print(f"成功处理的URL: {success_count} 个")
    print(f"失败的URL: {fail_count} 个")
    print(f"总共获取到 {len(unique_ips)} 个唯一IP地址，已保存到 ip/ip.txt 文件。")
    
    # 显示前10个IP作为示例
    print(f"\n前10个IP地址示例:")
    for ip in sorted_ips[:10]:
        print(f"  {ip}")
else:
    print("\n未找到有效的IP地址。")
    print(f"成功处理的URL: {success_count} 个")
    print(f"失败的URL: {fail_count} 个")
