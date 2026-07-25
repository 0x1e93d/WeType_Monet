#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import re
import json
import hashlib
import subprocess
import shutil
import urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

# ==================== 路径与配置区 (对齐项目结构) ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent

APK_DOWNLOAD_URL = "https://z.weixin.qq.com/android/download?channel=latest"
CHANGELOG_URL = "https://z.weixin.qq.com/web/changelog/android"

# 配置文件与资源生成路径
BASE_COLORS_PATH = PROJECT_ROOT / "config" / "base_colors.json"
RECORD_DIR = PROJECT_ROOT / "versions"
OUTPUT_RES_DIR = PROJECT_ROOT / "res"          # 生成 res/values 和 res/values-night
LAST_SHA256_FILE = PROJECT_ROOT / ".last_apk_sha256"
BUILD_SCRIPT_PATH = PROJECT_ROOT / "build.py"

LOCAL_APK_NAME = PROJECT_ROOT / "temp_latest.apk"
TEMP_DECOMPILE_DIR = PROJECT_ROOT / "temp_decompiled"

# 精确定位类路径: com.tencent.wetype.plugin.hld
TARGET_SMALI_RELATIVE_PATH = os.path.join("com", "tencent", "wetype", "plugin", "hld")
TARGET_SEARCH_KEY = "ime_skin_BW_0_Alpha_0_9"
# ===================================================================


def download_latest_apk(url: str, output_path: Path):
    """从指定链接下载最新版本的 APK"""
    print(f"[*] 正在从 {url} 下载最新安装包...")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    req = urllib.request.Request(url, headers=headers)
    
    with urllib.request.urlopen(req, timeout=60) as response, open(output_path, 'wb') as out_file:
        shutil.copyfileobj(response, out_file)
    print(f"[✓] 下载完成，已保存至: {output_path}")


def get_sha256(file_path: Path) -> str:
    """计算文件的 SHA256 哈希值"""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest().lower()


def check_sha256_and_stop_if_same(apk_path: Path) -> str:
    """校验 SHA256，如果一致则直接终止程序 (GitHub Actions 0消耗跳过)"""
    current_hash = get_sha256(apk_path)
    print(f"[*] 当前 APK SHA256: {current_hash}")

    if LAST_SHA256_FILE.exists():
        with open(LAST_SHA256_FILE, "r", encoding="utf-8") as f:
            last_hash = f.read().strip().lower()
        if current_hash == last_hash:
            print("[✓] SHA256 与上次处理记录一致，安装包未发生更新，终止自动化流程。")
            sys.exit(0)

    return current_hash


def fetch_changelog_info():
    """爬取官网日志页面，解析最新版本号、发布日期和更新日志"""
    print(f"[*] 正在爬取官网更新日志: {CHANGELOG_URL}")
    info = {
        "web_version": "",
        "release_date": "",
        "changelog": []
    }
    try:
        req = urllib.request.Request(
            CHANGELOG_URL, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode('utf-8')
            soup = BeautifulSoup(html, 'html.parser')

            h2_tags = soup.find_all('h2')
            if h2_tags:
                first_h2_text = h2_tags[0].get_text(strip=True)
                version_match = re.search(r'(\d+\.\d+\.\d+)', first_h2_text)
                if version_match:
                    info["web_version"] = version_match.group(1)
                
                info["changelog"] = [h2.get_text(strip=True) for h2 in h2_tags if h2.get_text(strip=True)]

            date_match = re.search(r'\d{4}[-./]\d{1,2}[-./]\d{1,2}', html)
            if date_match:
                info["release_date"] = date_match.group(0)

    except Exception as e:
        print(f"[!] 警告：爬取更新日志失败 ({e})，将继续流程。")

    return info


def decompile_apk(apk_path: Path, out_dir: Path):
    """使用 apktool 解包 APK"""
    print(f"[*] 正在解包 APK 到目录: {out_dir} ...")
    if out_dir.exists():
        shutil.rmtree(out_dir)

    cmd = ["apktool", "d", "-f", str(apk_path), "-o", str(out_dir)]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        print(f"[x] apktool 解包失败:\n{result.stderr}")
        sys.exit(1)
    print("[✓] 解包完成。")


def get_version_info_from_apktool(decompiled_dir: Path):
    """从 apktool.yml 提取真实的 versionName 和 versionCode"""
    yml_path = decompiled_dir / "apktool.yml"
    version_name, version_code = "unknown", "0"

    if yml_path.exists():
        with open(yml_path, "r", encoding="utf-8") as f:
            content = f.read()
            vname_match = re.search(r'versionName:\s*[\'"]?([^\'"\n]+)[\'"]?', content)
            vcode_match = re.search(r'versionCode:\s*[\'"]?(\d+)[\'"]?', content)
            if vname_match:
                version_name = vname_match.group(1).strip()
            if vcode_match:
                version_code = vcode_match.group(1).strip()

    return version_name, version_code


def find_mappings_from_target_smali(decompiled_dir: Path, target_keys: list) -> dict:
    """
    限定在 com.tencent.wetype.plugin.hld 路径下搜索“ime_skin_BW_0_Alpha_0_9”及相关属性的 ID 映射
    """
    print(f"[*] 正在精准扫描目标类路径 ({TARGET_SMALI_RELATIVE_PATH}) 下的 Smali...")
    
    # 1. 读取 public.xml 解析 ID -> obfuscated_name
    public_xml_path = decompiled_dir / "res" / "values" / "public.xml"
    id_to_obf_name = {}

    if public_xml_path.exists():
        import xml.etree.ElementTree as ET
        tree = ET.parse(public_xml_path)
        root = tree.getroot()
        for elem in root.findall(".//public[@type='color']"):
            res_id = elem.get("id", "").lower()
            res_name = elem.get("name", "")
            if res_id and res_name:
                id_to_obf_name[res_id] = res_name

    # 2. 定位所有 smali*/com/tencent/wetype/plugin/hld 目录
    smali_dirs = [d for d in decompiled_dir.iterdir() if d.is_dir() and d.name.startswith("smali")]
    target_hld_dirs = []

    for s_dir in smali_dirs:
        hld_path = s_dir / TARGET_SMALI_RELATIVE_PATH
        if hld_path.exists():
            target_hld_dirs.append(hld_path)

    if not target_hld_dirs:
        print(f"[!] 警告：未能在解包目录中找到 {TARGET_SMALI_RELATIVE_PATH} 路径！扩大范围全局搜寻...")
        target_hld_dirs = smali_dirs

    # 3. 在目标 Smali 中匹配特征 key 及 ID
    key_to_id = {}
    id_pattern = re.compile(r'0x7f06[0-9a-fA-F]{4}')

    for target_dir in target_hld_dirs:
        for smali_file in target_dir.rglob("*.smali"):
            try:
                content = smali_file.read_text(encoding="utf-8", errors="ignore")
                # 检查是否包含核心标志键值 "ime_skin_BW_0_Alpha_0_9"
                if TARGET_SEARCH_KEY in content:
                    for key in target_keys:
                        if key in content:
                            key_pattern = re.compile(rf'"{re.escape(key)}"')
                            if key_pattern.search(content):
                                matches = id_pattern.findall(content)
                                if matches:
                                    key_to_id[key] = matches[0].lower()
            except Exception:
                continue

    # 4. 建立最终反查 Key -> 混淆后的资源 Name
    key_to_obf_name = {}
    for key, res_id in key_to_id.items():
        if res_id in id_to_obf_name:
            key_to_obf_name[key] = id_to_obf_name[res_id]

    print(f"[✓] Smali 查找完成，成功检索映射: {len(key_to_obf_name)} / {len(target_keys)} 项。")
    return key_to_obf_name


def generate_overlay_xmls(base_colors: dict, key_mapping: dict, output_res_root: Path):
    """生成 colors.xml 与 colors-night.xml 到 res/ 文件夹"""
    values_dir = output_res_root / "values"
    values_night_dir = output_res_root / "values-night"

    values_dir.mkdir(parents=True, exist_ok=True)
    values_night_dir.mkdir(parents=True, exist_ok=True)

    light_colors, night_colors = [], []

    for item in base_colors.get("theme_colors", []):
        unobf_key = item.get("key")
        light_val = item.get("light")
        night_val = item.get("night")

        obf_name = key_mapping.get(unobf_key)
        if not obf_name:
            continue

        if light_val:
            light_colors.append(f'    <color name="{obf_name}">{light_val}</color>')
        if night_val:
            night_colors.append(f'    <color name="{obf_name}">{night_val}</color>')

    with open(values_dir / "colors.xml", "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n<resources>\n')
        f.write("\n".join(light_colors))
        f.write('\n</resources>\n')

    if night_colors:
        with open(values_night_dir / "colors.xml", "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="utf-8"?>\n<resources>\n')
            f.write("\n".join(night_colors))
            f.write('\n</resources>\n')

    print(f"[✓] 资源文件已刷新更新至:\n - {values_dir / 'colors.xml'}\n - {values_night_dir / 'colors.xml'}")


def run_build_script():
    """升级处理完成后，联动执行根目录下的 build.py"""
    if BUILD_SCRIPT_PATH.exists():
        print("\n[*] 开始联动调用 build.py 进行编译和打包...")
        result = subprocess.run([sys.executable, str(BUILD_SCRIPT_PATH)], cwd=PROJECT_ROOT)
        if result.returncode == 0:
            print("[✓] build.py 打包完成！")
        else:
            print(f"[x] build.py 执行异常，退出码: {result.returncode}")
            sys.exit(result.returncode)
    else:
        print(f"[!] 警告：未在根目录找到 build.py ({BUILD_SCRIPT_PATH})，跳过打包步骤。")


def main():
    # 步骤 1: 下载 APK
    download_latest_apk(APK_DOWNLOAD_URL, LOCAL_APK_NAME)

    # 步骤 2: 校验 SHA256，相同直接中断退出
    sha256_val = check_sha256_and_stop_if_same(LOCAL_APK_NAME)

    # 步骤 3: 获取官网 Log
    web_info = fetch_changelog_info()

    # 步骤 4: 解包并提取真实 Version
    decompile_apk(LOCAL_APK_NAME, TEMP_DECOMPILE_DIR)
    apk_version_name, apk_version_code = get_version_info_from_apktool(TEMP_DECOMPILE_DIR)

    # 步骤 5: 比较版本号逻辑 (不一致则日志置空)
    print(f"[*] 网页提取版本: '{web_info['web_version']}', APK 内置版本: '{apk_version_name}'")
    if web_info["web_version"] and web_info["web_version"] != apk_version_name:
        print("[!] 网页版本与 APK 内置版本不匹配，清空日志内容。")
        final_changelog = []
    else:
        final_changelog = web_info["changelog"]

    # 步骤 6: 读取 config/base_colors.json
    if not BASE_COLORS_PATH.exists():
        print(f"[x] 缺失基础颜色配置文件: {BASE_COLORS_PATH}")
        sys.exit(1)

    with open(BASE_COLORS_PATH, "r", encoding="utf-8") as f:
        base_colors_data = json.load(f)

    target_keys = [item["key"] for item in base_colors_data.get("theme_colors", []) if "key" in item]

    # 步骤 7: 精确检索 Smali Mapping
    key_mapping = find_mappings_from_target_smali(TEMP_DECOMPILE_DIR, target_keys)

    # 步骤 8: 输出映射文件到 versions/
    version_file_name = f"{apk_version_name}({apk_version_code}).json"
    version_record = {
        "version_name": apk_version_name,
        "version_code": apk_version_code,
        "release_date": web_info["release_date"],
        "changelog": final_changelog,
        "sha256": sha256_val,
        "mappings": key_mapping
    }

    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    version_json_path = RECORD_DIR / version_file_name
    with open(version_json_path, "w", encoding="utf-8") as f:
        json.dump(version_record, f, ensure_ascii=False, indent=4)
    print(f"[✓] 已输出 Mapping 文件: {version_json_path}")

    # 步骤 9: 生成 XML 到 res/values/
    generate_overlay_xmls(base_colors_data, key_mapping, OUTPUT_RES_DIR)

    # 步骤 10: 记录 SHA256 缓存并清理清理垃圾
    with open(LAST_SHA256_FILE, "w", encoding="utf-8") as f:
        f.write(sha256_val)

    if TEMP_DECOMPILE_DIR.exists():
        shutil.rmtree(TEMP_DECOMPILE_DIR)
    if LOCAL_APK_NAME.exists():
        os.remove(LOCAL_APK_NAME)

    # 步骤 11: 自动运行 build.py
    run_build_script()


if __name__ == "__main__":
    main()