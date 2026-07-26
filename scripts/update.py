#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import sys
import json
import shutil
import hashlib
import subprocess
import urllib.request
from pathlib import Path
import xml.etree.ElementTree as ET

# ----------------- 路径定义 -----------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

CONFIG_DIR = PROJECT_ROOT / "config"
OUT_DIR = PROJECT_ROOT / "out"
BASE_COLORS_PATH = CONFIG_DIR / "base_colors.json"

DOWNLOAD_APK_PATH = PROJECT_ROOT / "wetype_latest.apk"
DECOMPILE_DIR = PROJECT_ROOT / "decompiled_apk"

APK_URL = "https://z.weixin.qq.com/android/download?channel=latest"
CHANGELOG_URL = "https://z.weixin.qq.com/web/changelog/android"

# ----------------- 工具函数 -----------------

def calculate_sha256(file_path: Path) -> str:
    """计算文件的 SHA256 哈希值"""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()

def get_latest_sha256() -> tuple[str | None, str | None]:
    """获取 config 目录下最新的配置文件及其 SHA256"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    json_files = [f for f in CONFIG_DIR.glob("*.json") if f.name != "base_colors.json"]
    if not json_files:
        return None, None

    latest_file = max(json_files, key=lambda f: f.stat().st_mtime)
    try:
        with open(latest_file, "r", encoding="utf-8") as f:
            return latest_file.name, json.load(f).get("sha256")
    except Exception:
        return None, None

def fetch_changelog_info() -> tuple[str, str, list[str]]:
    """正则提取官网的版本名称、日期与日志"""
    req = urllib.request.Request(
        CHANGELOG_URL, 
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            html = resp.read().decode('utf-8')
            
        # 匹配版本与日期
        v_match = re.search(r'发布版本:[\s\S]*?([\d\.]+)', html)
        d_match = re.search(r'发布日期:[\s\S]*?(\d{4}-\d{2}-\d{2})', html)
        
        web_version = v_match.group(1).strip() if v_match else ""
        release_date = d_match.group(1).strip() if d_match else ""

        # 匹配 h2 日志条目
        changelog = []
        if content_match := re.search(r'<div[^>]*class=["\']content["\'][^>]*>([\s\S]*?)</div>', html):
            h2_items = re.findall(r'<h2[^>]*>([\s\S]*?)</h2>', content_match.group(1))
            for item in h2_items:
                clean = re.sub(r'<[^>]+>', '', item)
                clean = re.sub(r'^\s*[\-\–\—\•\*]\s*', '', clean).strip()
                if clean:
                    changelog.append(clean)

        print(f"[+] 官网数据提取成功 -> 版本: '{web_version}', 日期: '{release_date}', 日志条数: {len(changelog)}")
        return web_version, release_date, changelog
    except Exception as e:
        print(f"[!] 抓取官网日志失败: {e}")
        return "", "", []

def download_apk():
    """下载最新的 APK"""
    print(f"[*] 开始下载 APK: {APK_URL}")
    req = urllib.request.Request(APK_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as resp, open(DOWNLOAD_APK_PATH, 'wb') as out_file:
        shutil.copyfileobj(resp, out_file)
    print(f"[+] APK 下载保存至: {DOWNLOAD_APK_PATH}")

def decompile_apk():
    """使用 apktool 进行解包"""
    print(f"[*] 解包 APK 到: {DECOMPILE_DIR}")
    if DECOMPILE_DIR.exists():
        shutil.rmtree(DECOMPILE_DIR, ignore_errors=True)
    
    res = subprocess.run(["apktool", "d", str(DOWNLOAD_APK_PATH), "-o", str(DECOMPILE_DIR), "-f"], 
                         capture_output=True, text=True)
    if res.returncode != 0:
        print(f"[!] Apktool 解包失败:\n{res.stderr}")
        sys.exit(1)
    print("[+] Apktool 解包成功！")

def extract_apk_version_info() -> tuple[str, str]:
    """从 apktool.yml 提取真实 versionCode 和 versionName"""
    yml_path = DECOMPILE_DIR / "apktool.yml"
    code, name = "", ""
    if yml_path.exists():
        with open(yml_path, "r", encoding="utf-8") as f:
            for line in f:
                if "versionCode:" in line:
                    code = line.split("versionCode:")[1].strip(" '\"")
                elif "versionName:" in line:
                    name = line.split("versionName:")[1].strip(" '\"")
    print(f"[+] APK 提取 -> 版本名称: {name}, 版本号: {code}")
    return code, name

def parse_color_mappings() -> tuple[dict[str, str], dict[str, str]]:
    """解析资源映射：提取 key_to_id 及 id_to_obfuscated_name"""
    id_to_name = {}
    key_to_id = {}

    # 1. 解析 public.xml (0x7fxxxxxx -> 混淆Name)
    public_xml = DECOMPILE_DIR / "res" / "values" / "public.xml"
    if public_xml.exists():
        tree = ET.parse(public_xml)
        for elem in tree.getroot().findall("public"):
            if elem.attrib.get("type") == "color":
                res_id = elem.attrib.get("id", "").lower()
                res_name = elem.attrib.get("name", "")
                if res_id and res_name:
                    id_to_name[res_id] = res_name
                    key_to_id[res_name] = res_id  # 预存兜底

    # 2. 深度扫描 Smali 文件中的静态资源 ID 定义
    smali_field_pattern = re.compile(r'\.field\s+.*?\s+([a-zA-Z0-9_]+):I\s*=\s*(0x7f[0-9a-fA-F]+)')
    for smali_dir in DECOMPILE_DIR.glob("smali*"):
        target_pkg = smali_dir / "com" / "tencent" / "wetype"
        if not target_pkg.exists():
            continue
        for smali_file in target_pkg.rglob("*.smali"):
            with open(smali_file, "r", encoding="utf-8", errors="ignore") as f:
                for match in smali_field_pattern.finditer(f.read()):
                    key_to_id[match.group(1)] = match.group(2).lower()

    print(f"[+] 全局构建 Key-ID 字典完成，共匹配到 {len(key_to_id)} 项")
    return key_to_id, id_to_name

# ----------------- 主执行流程 -----------------

def main():
    print("=== 微信输入法 Overlay 自动适配与更新脚本 ===")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 抓取官网数据
    web_version, release_date, changelog = fetch_changelog_info()

    # 2. 下载并比对 APK SHA256
    download_apk()
    new_sha256 = calculate_sha256(DOWNLOAD_APK_PATH)
    print(f"[*] 当前 APK SHA256: {new_sha256}")

    latest_file, last_sha256 = get_latest_sha256()
    if last_sha256 and last_sha256.lower() == new_sha256.lower():
        print(f"[=] 版本记录 ({latest_file}) 已是最新，退出。")
        sys.exit(0)

    # 3. 解包 APK 并获取元数据
    decompile_apk()
    apk_code, apk_name = extract_apk_version_info()
    apk_name = apk_name or web_version or "unknown_version"

    # 校验版本一致性，版本不一致时清空日志
    final_changelog = changelog if (not web_version or web_version == apk_name) else []
    if web_version and web_version != apk_name:
        print(f"[!] 官网版本('{web_version}') 与 APK 版本('{apk_name}') 不一致，清空更新日志。")

    # 4. 提取颜色混淆映射
    key_to_id, id_to_name = parse_color_mappings()

    # 5. 读取模板并构建主题配置
    if not BASE_COLORS_PATH.exists():
        print(f"[!] 模板文件不存在: {BASE_COLORS_PATH}")
        sys.exit(1)

    with open(BASE_COLORS_PATH, "r", encoding="utf-8") as f:
        base_colors = json.load(f).get("theme_colors", [])

    updated_colors, missing_keys = [], []
    for item in base_colors:
        raw_key = item.get("key")
        res_id = key_to_id.get(raw_key)
        obf_key = id_to_name.get(res_id) if res_id else raw_key

        if not res_id:
            missing_keys.append(raw_key)

        updated_colors.append({
            "unobfuscated_key": raw_key,
            "obfuscated_key": obf_key,
            "light": item.get("light"),
            "night": item.get("night"),
            "description": item.get("description", "")
        })

    if missing_keys:
        print(f"[!] 警告: {len(missing_keys)} 个 Key 未能在 Smali/public.xml 中找到对应混淆 ID。")

    # 6. 生成并保存产物（同步输出到 config/ 与 out/）
    safe_name = re.sub(r'[\\/:*?"<>|\s]', '_', apk_name)
    safe_code = re.sub(r'[\\/:*?"<>|\s]', '_', apk_code)
    filename = f"{safe_name}({safe_code}).json" if safe_code else f"{safe_name}.json"

    json_payload = {
        "version_name": apk_name,
        "version_code": apk_code,
        "release_date": release_date,
        "sha256": new_sha256,
        "changelog": final_changelog,
        "theme_colors": updated_colors
    }

    config_path = CONFIG_DIR / filename
    out_path = OUT_DIR / filename

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(json_payload, f, ensure_ascii=False, indent=4)
    shutil.copy2(config_path, out_path)

    print(f"[+] 配置保存完毕:\n    - Config: {config_path}\n    - Out: {out_path}")

    # 7. 调用构建脚本打包 Module
    build_script = SCRIPT_DIR / "build.py"
    if build_script.exists():
        print(f"[*] 启动构建脚本: {build_script}")
        subprocess.run([sys.executable, str(build_script), str(config_path)], check=True)

if __name__ == "__main__":
    main()