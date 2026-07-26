#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import json
import hashlib
import subprocess
import urllib.request
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

# ----------------- 路径定义 -----------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")
BASE_COLORS_PATH = os.path.join(CONFIG_DIR, "base_colors.json")

DOWNLOAD_APK_PATH = os.path.join(PROJECT_ROOT, "wetype_latest.apk")
DECOMPILE_DIR = os.path.join(PROJECT_ROOT, "decompiled_apk")

APK_URL = "https://z.weixin.qq.com/android/download?channel=latest"
CHANGELOG_URL = "https://z.weixin.qq.com/web/changelog/android"

# ----------------- 工具函数 -----------------

def calculate_sha256(file_path):
    """计算文件的 SHA256 值"""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def get_latest_version_json_info():
    """获取 config 目录下最新的 [版本名称].json 中的 SHA256"""
    if not os.path.exists(CONFIG_DIR):
        os.makedirs(CONFIG_DIR)
        return None, None

    json_files = [f for f in os.listdir(CONFIG_DIR) if f.endswith(".json") and f != "base_colors.json"]
    if not json_files:
        return None, None

    latest_file = max(json_files, key=lambda x: os.path.getmtime(os.path.join(CONFIG_DIR, x)))
    latest_path = os.path.join(CONFIG_DIR, latest_file)
    
    try:
        with open(latest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return latest_file, data.get("sha256")
    except Exception:
        return None, None

def fetch_changelog_info():
    """从官网抓取更新日志、版本名称与发布日期"""
    req = urllib.request.Request(
        CHANGELOG_URL, 
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    )
    try:
        with urllib.request.urlopen(req) as response:
            html = response.read().decode('utf-8')
            
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. 抓取网页展示的版本名称
        meta_div = soup.find('div', class_='downloadMeta')
        web_version_name = ""
        if meta_div:
            text = meta_div.get_text()
            match = re.search(r'发布版本:\s*([\d\.]+)', text)
            if match:
                web_version_name = match.group(1).strip()

        # 2. 抓取发布日期
        date_div = soup.find('div', class_='meta')
        release_date = ""
        if date_div:
            text = date_div.get_text()
            match = re.search(r'发布日期:\s*([\d\-]+)', text)
            if match:
                release_date = match.group(1).strip()

        # 3. 抓取更新日志
        content_div = soup.find('div', class_='content')
        changelog = []
        if content_div:
            h2_tags = content_div.find_all('h2')
            changelog = [h2.get_text().strip() for h2 in h2_tags]

        return web_version_name, release_date, changelog
    except Exception as e:
        print(f"[!] 抓取日志网页失败: {e}")
        return "", "", []

def download_apk():
    """下载最新的 APK"""
    print(f"[*] 开始下载 APK: {APK_URL}")
    req = urllib.request.Request(APK_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response, open(DOWNLOAD_APK_PATH, 'wb') as out_file:
        out_file.write(response.read())
    print(f"[+] APK 下载完成，保存至: {DOWNLOAD_APK_PATH}")

def decompile_apk():
    """使用 apktool 进行解包"""
    print(f"[*] 开始解包 APK 到: {DECOMPILE_DIR}")
    if os.path.exists(DECOMPILE_DIR):
        subprocess.run(["rm", "-rf", DECOMPILE_DIR])
    
    cmd = ["apktool", "d", DOWNLOAD_APK_PATH, "-o", DECOMPILE_DIR, "-f"]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        print(f"[!] Apktool 解包失败:\n{res.stderr}")
        sys.exit(1)
    print("[+] Apktool 解包成功！")

def extract_apk_version_info():
    """从解包后的 apktool.yml 中读取真实的 versionCode 和 versionName"""
    yml_path = os.path.join(DECOMPILE_DIR, "apktool.yml")
    version_code = ""
    version_name = ""

    if not os.path.exists(yml_path):
        print(f"[!] 未找到 {yml_path}")
        return version_code, version_name

    with open(yml_path, "r", encoding="utf-8") as f:
        for line in f:
            if "versionCode:" in line:
                version_code = line.split("versionCode:")[1].strip().strip("'\"")
            elif "versionName:" in line:
                version_name = line.split("versionName:")[1].strip().strip("'\"")

    print(f"[+] 从 APK 内部提取到 -> 版本名称: {version_name}, 版本号: {version_code}")
    return version_code, version_name

def find_target_smali_file():
    """在 smali*/com/tencent/wetype/plugin/hld 搜索 Smali 类文件"""
    smali_dirs = [d for d in os.listdir(DECOMPILE_DIR) if d.startswith("smali")]
    target_rel_path = os.path.join("com", "tencent", "wetype", "plugin", "hld")
    
    for s_dir in smali_dirs:
        search_path = os.path.join(DECOMPILE_DIR, s_dir, target_rel_path)
        if os.path.exists(search_path):
            for root, _, files in os.walk(search_path):
                for file in files:
                    if file.endswith(".smali"):
                        full_file_path = os.path.join(root, file)
                        with open(full_file_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                            if "BW_0_Alpha_0_0_3" in content or "ime_skin_BW_0_Alpha_0_9" in content:
                                print(f"[+] 找到目标 Smali 类文件: {full_file_path}")
                                return full_file_path
    return None

def parse_smali_key_to_id(smali_path):
    """解析 Smali 文件中的 字段名 -> 十六进制 ID"""
    key_to_id = {}
    pattern = re.compile(r'\.field\s+public\s+static\s+final\s+([a-zA-Z0-9_]+):I\s*=\s*(0x7f[0-0a-fA-F]+)')
    
    with open(smali_path, "r", encoding="utf-8") as f:
        for line in f:
            match = pattern.search(line)
            if match:
                key_name = match.group(1)
                res_id = match.group(2).lower()
                key_to_id[key_name] = res_id
                
    print(f"[+] 从 Smali 中成功提取出 {len(key_to_id)} 个 Key-ID 映射项")
    return key_to_id

def parse_public_xml_id_to_name():
    """解析 res/values/public.xml 中的 十六进制 ID -> 混淆后的资源 Name"""
    public_xml_path = os.path.join(DECOMPILE_DIR, "res", "values", "public.xml")
    id_to_name = {}
    
    if not os.path.exists(public_xml_path):
        print(f"[!] 未能找到 public.xml: {public_xml_path}")
        return id_to_name
        
    tree = ET.parse(public_xml_path)
    root = tree.getroot()
    
    for child in root.findall("public"):
        if child.attrib.get("type") == "color":
            res_id = child.attrib.get("id", "").lower()
            res_name = child.attrib.get("name", "")
            if res_id and res_name:
                id_to_name[res_id] = res_name
                
    print(f"[+] 从 public.xml 中成功提取出 {len(id_to_name)} 个 ID-混淆Name 映射项")
    return id_to_name

# ----------------- 主执行流程 -----------------

def main():
    print("=== 微信输入法 Overlay 自动适配与更新脚本 ===")
    
    # 1. 抓取网页端日志与版本名称
    web_version_name, release_date, changelog = fetch_changelog_info()
    print(f"[*] 网页提取版本名称: '{web_version_name}' ({release_date})")

    # 2. 下载 APK 并计算 SHA256
    download_apk()
    new_sha256 = calculate_sha256(DOWNLOAD_APK_PATH)
    print(f"[*] 当前下载 APK 的 SHA256: {new_sha256}")

    # 3. 比对本地上一个版本的 SHA256
    latest_file, last_sha256 = get_latest_version_json_info()
    if last_sha256 and last_sha256.lower() == new_sha256.lower():
        print(f"[=] 本地已存在匹配 SHA256 的版本记录 ({latest_file})，软件未更新，终止操作。")
        sys.exit(0)

    print("[!] 检测到 APK 有更新，开始解包分析...")

    # 4. 执行 Apktool 解包
    decompile_apk()

    # 5. 从 APK 内部精准提取 versionCode 与 versionName
    apk_version_code, apk_version_name = extract_apk_version_info()
    if not apk_version_name:
        print("[!] 无法从 APK 内部获取 versionName，回退使用网页抓取或默认值")
        apk_version_name = web_version_name if web_version_name else "unknown_version"

    # 6. 比对网页版本名称与 APK 真实版本名称（校验网页延迟）
    final_changelog = changelog
    if web_version_name and web_version_name != apk_version_name:
        print(f"[!] 警告: 网页版本名称 ('{web_version_name}') 与 APK 真实版本名称 ('{apk_version_name}') 不匹配！")
        print("    可能网页存在更新延迟，清空本次日志以防记录错误日志。")
        final_changelog = []

    # 7. 定位 Smali 类并解析色彩 Key 与 ID 映射
    smali_file = find_target_smali_file()
    if not smali_file:
        print("[!] 错误: 未找到色彩 ID 定义 Smali 类！")
        sys.exit(1)

    unobfuscated_to_id = parse_smali_key_to_id(smali_file)
    id_to_obfuscated_name = parse_public_xml_id_to_name()

    # 8. 读取 base_colors.json 模板
    if not os.path.exists(BASE_COLORS_PATH):
        print(f"[!] 模板文件不存在: {BASE_COLORS_PATH}")
        sys.exit(1)

    with open(BASE_COLORS_PATH, "r", encoding="utf-8") as f:
        base_colors_data = json.load(f)

    # 9. 构建适配表
    updated_colors = []
    missing_keys = []

    for item in base_colors_data.get("theme_colors", []):
        unobfuscated_key = item.get("key")
        light_color = item.get("light")
        night_color = item.get("night")
        description = item.get("description", "")

        res_id = unobfuscated_to_id.get(unobfuscated_key)
        obfuscated_key = id_to_obfuscated_name.get(res_id) if res_id else None

        if not obfuscated_key:
            obfuscated_key = unobfuscated_key
            missing_keys.append(unobfuscated_key)

        updated_colors.append({
            "unobfuscated_key": unobfuscated_key,
            "obfuscated_key": obfuscated_key,
            "light": light_color,
            "night": night_color,
            "description": description
        })

    if missing_keys:
        print(f"[!] 警告: {len(missing_keys)} 个 Key 未在 Smali/public.xml 中找到对应混淆 ID。")

    # 10. 组装 JSON 保存结构
    output_json_data = {
        "version_name": apk_version_name,
        "version_code": apk_version_code,
        "release_date": release_date,
        "sha256": new_sha256,
        "changelog": final_changelog,
        "theme_colors": updated_colors
    }

    # 11. 保存为 config/[版本名称].json
    safe_version_name = re.sub(r'[\\/:*?"<>|\s]', '_', apk_version_name)
    output_filename = f"{safe_version_name}.json"
    output_json_path = os.path.join(CONFIG_DIR, output_filename)

    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(output_json_data, f, ensure_ascii=False, indent=4)

    print(f"[+] 成功保存版本配置文件: {output_json_path}")

    # 12. 自动触发构建打包脚本
    build_script_path = os.path.join(SCRIPT_DIR, "build.py")
    if os.path.exists(build_script_path):
        print(f"[*] 调用构建脚本: {build_script_path}")
        subprocess.run([sys.executable, build_script_path, output_json_path])

if __name__ == "__main__":
    main()