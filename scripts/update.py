#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import json
import shutil
import hashlib
import subprocess
import urllib.request
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

# ----------------- 路径定义 -----------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")
OUT_DIR = os.path.join(PROJECT_ROOT, "out")  # 统一产物输出目录
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
    """获取 config 目录下最新的配置文件 SHA256"""
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
    """根据真实 DOM 精准解析网页版本、日期与日志列表"""
    req = urllib.request.Request(
        CHANGELOG_URL, 
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    )
    try:
        with urllib.request.urlopen(req) as response:
            html = response.read().decode('utf-8')
            
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. 抓取版本名称 (从 "3.5.2 for Android" 精准截取 "3.5.2")
        download_meta = soup.find('div', class_='downloadMeta')
        web_version_name = ""
        if download_meta:
            text = download_meta.get_text()
            match = re.search(r'发布版本:\s*([\d\.]+)', text)
            if match:
                web_version_name = match.group(1).strip()

        # 2. 抓取发布日期 ("2026-07-22")
        meta_div = soup.find('div', class_='meta')
        release_date = ""
        if meta_div:
            text = meta_div.get_text()
            match = re.search(r'发布日期:\s*([\d\-]+)', text)
            if match:
                release_date = match.group(1).strip()

        # 3. 抓取更新日志并清洗前缀符号
        content_div = soup.find('div', class_='content')
        changelog = []
        if content_div:
            h2_tags = content_div.find_all('h2')
            for h2 in h2_tags:
                raw_text = h2.get_text().strip()
                cleaned_text = re.sub(r'^\s*[\-\–\—]\s*', '', raw_text)
                if cleaned_text:
                    changelog.append(cleaned_text)

        print(f"[+] 网页抓取结果 -> 版本: '{web_version_name}', 日期: '{release_date}', 日志数: {len(changelog)}")
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
        subprocess.run(["rm", "-rf", DECOMPILE_DIR], check=False)
    
    cmd = ["apktool", "d", DOWNLOAD_APK_PATH, "-o", DECOMPILE_DIR, "-f"]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        print(f"[!] Apktool 解包失败:\n{res.stderr}")
        sys.exit(1)
    print("[+] Apktool 解包成功！")

def extract_apk_version_info():
    """从 apktool.yml 中读取真实的 versionCode 和 versionName"""
    yml_path = os.path.join(DECOMPILE_DIR, "apktool.yml")
    version_code = ""
    version_name = ""

    if not os.path.exists(yml_path):
        return version_code, version_name

    with open(yml_path, "r", encoding="utf-8") as f:
        for line in f:
            if "versionCode:" in line:
                version_code = line.split("versionCode:")[1].strip().strip("'\"")
            elif "versionName:" in line:
                version_name = line.split("versionName:")[1].strip().strip("'\"")

    print(f"[+] 从 APK 内部提取到 -> 版本名称: {version_name}, 版本号: {version_code}")
    return version_code, version_name

def build_full_key_to_id_map():
    """全局解析所有 Smali 与 public.xml 映射表"""
    key_to_id = {}
    
    # 1. 扫描 public.xml
    public_xml_path = os.path.join(DECOMPILE_DIR, "res", "values", "public.xml")
    if os.path.exists(public_xml_path):
        tree = ET.parse(public_xml_path)
        root = tree.getroot()
        for child in root.findall("public"):
            if child.attrib.get("type") == "color":
                res_id = child.attrib.get("id", "").lower()
                res_name = child.attrib.get("name", "")
                if res_id and res_name:
                    key_to_id[res_name] = res_id

    # 2. 深度扫描 smali 字段
    smali_dirs = [d for d in os.listdir(DECOMPILE_DIR) if d.startswith("smali")]
    field_pattern = re.compile(r'\.field\s+.*?\s+([a-zA-Z0-9_]+):I\s*=\s*(0x7f[0-9a-fA-F]+)')
    
    for s_dir in smali_dirs:
        target_path = os.path.join(DECOMPILE_DIR, s_dir, "com", "tencent", "wetype")
        if not os.path.exists(target_path):
            continue
            
        for root, _, files in os.walk(target_path):
            for file in files:
                if file.endswith(".smali"):
                    full_path = os.path.join(root, file)
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            match = field_pattern.search(line)
                            if match:
                                k_name = match.group(1)
                                r_id = match.group(2).lower()
                                key_to_id[k_name] = r_id

    print(f"[+] 聚合建立全局 Key-ID 字典库，共匹配到 {len(key_to_id)} 个色彩项")
    return key_to_id

def parse_public_xml_id_to_name():
    """解析 res/values/public.xml 中的 ID -> 混淆 Name"""
    public_xml_path = os.path.join(DECOMPILE_DIR, "res", "values", "public.xml")
    id_to_name = {}
    
    if not os.path.exists(public_xml_path):
        return id_to_name
        
    tree = ET.parse(public_xml_path)
    root = tree.getroot()
    
    for child in root.findall("public"):
        if child.attrib.get("type") == "color":
            res_id = child.attrib.get("id", "").lower()
            res_name = child.attrib.get("name", "")
            if res_id and res_name:
                id_to_name[res_id] = res_name
                
    return id_to_name

# ----------------- 主执行流程 -----------------

def main():
    print("=== 微信输入法 Overlay 自动适配与更新脚本 ===")
    
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    # 1. 抓取网页端日志与版本名称
    web_version_name, release_date, changelog = fetch_changelog_info()

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

    # 5. 读取真实元数据
    apk_version_code, apk_version_name = extract_apk_version_info()
    if not apk_version_name:
        apk_version_name = web_version_name if web_version_name else "unknown_version"

    # 6. 比对网页版本名称与 APK 真实版本名称
    final_changelog = changelog
    if web_version_name and web_version_name != apk_version_name:
        print(f"[!] 网页版本('{web_version_name}') 与 APK 版本('{apk_version_name}') 不一致，清空日志。")
        final_changelog = []

    # 7. 解析映射字典
    key_to_id_map = build_full_key_to_id_map()
    id_to_obfuscated_name = parse_public_xml_id_to_name()

    # 8. 读取模板
    if not os.path.exists(BASE_COLORS_PATH):
        print(f"[!] 模板文件不存在: {BASE_COLORS_PATH}")
        sys.exit(1)

    with open(BASE_COLORS_PATH, "r", encoding="utf-8") as f:
        base_colors_data = json.load(f)

    # 9. 构建主题颜色配置项
    updated_colors = []
    missing_keys = []

    for item in base_colors_data.get("theme_colors", []):
        unobfuscated_key = item.get("key")
        light_color = item.get("light")
        night_color = item.get("night")
        description = item.get("description", "")

        res_id = key_to_id_map.get(unobfuscated_key)
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

    # 10. 组装 JSON
    output_json_data = {
        "version_name": apk_version_name,
        "version_code": apk_version_code,
        "release_date": release_date,
        "sha256": new_sha256,
        "changelog": final_changelog,
        "theme_colors": updated_colors
    }

    # 11. 保存 JSON 到 config/ 目录，同时同步一份至 out/ 统一产物目录
    safe_version_name = re.sub(r'[\\/:*?"<>|\s]', '_', apk_version_name)
    safe_version_code = re.sub(r'[\\/:*?"<>|\s]', '_', apk_version_code)
    
    output_filename = f"{safe_version_name}（{safe_version_code}）.json" if safe_version_code else f"{safe_version_name}.json"
    
    config_json_path = os.path.join(CONFIG_DIR, output_filename)
    out_json_path = os.path.join(OUT_DIR, output_filename)

    # 写入 config/ 目录
    with open(config_json_path, "w", encoding="utf-8") as f:
        json.dump(output_json_data, f, ensure_ascii=False, indent=4)
        
    # 复制到 out/ 产物目录
    shutil.copy2(config_json_path, out_json_path)

    print(f"[+] 成功生成 JSON 配置文件:")
    print(f"    - 配置存档: {config_json_path}")
    print(f"    - 统一产物: {out_json_path}")

    # 12. 自动调用 build.py 进行打包构建
    build_script_path = os.path.join(SCRIPT_DIR, "build.py")
    if os.path.exists(build_script_path):
        print(f"[*] 启动构建脚本: {build_script_path}")
        subprocess.run([sys.executable, build_script_path, config_json_path], check=True)

if __name__ == "__main__":
    main()