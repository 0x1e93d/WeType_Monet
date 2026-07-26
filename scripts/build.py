#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
微信输入法 Monet Overlay 自动化适配与构建工作流 (深度映射修复版)
适配顺序: Color -> String -> Drawable 三阶段独立处理
解决问题: 解决 obfuscated_key 无法正确映射回混淆名称的问题
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

# ==============================================================================
# 1. 常量与路径配置
# ==============================================================================

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

CONFIG_DIR = PROJECT_ROOT / "config"
SRC_DIR = PROJECT_ROOT / "src"
OUT_DIR = PROJECT_ROOT / "out"
BUILD_TMP_DIR = OUT_DIR / "build_tmp"
TEMPLATE_DIR = PROJECT_ROOT / "module_template"
DECOMPILE_DIR = OUT_DIR / "decompiled_apk"

BASE_CONFIG_PATH = CONFIG_DIR / "base.json"
DOWNLOAD_APK_PATH = OUT_DIR / "wetype_latest.apk"

APK_URL = "https://z.weixin.qq.com/android/download?channel=latest"
CHANGELOG_URL = "https://z.weixin.qq.com/web/changelog/android"

MODULE_ID = "Wetype_Monet"
MODULE_NAME = "微信输入法 Monet"
MODULE_AUTHOR = "酷安@1e93d"
MODULE_DESCRIPTION = "为微信输入法提供 Monet 动态色彩主题与资源定制。"
BASE_VERSION = "v1.0.0"


# ==============================================================================
# 2. 通用底层辅助函数
# ==============================================================================

def get_git_info() -> tuple[str, str]:
    try:
        count = subprocess.check_output(
            ["git", "rev-list", "--count", "HEAD"], 
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        git_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], 
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        return count or "1", git_hash or "dev"
    except Exception:
        return "1", "dev"

def calculate_sha256(file_path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()

def find_sdk_tools() -> tuple[str, str, str, str]:
    aapt2 = os.environ.get("AAPT2_PATH")
    zipalign = os.environ.get("ZIPALIGN_PATH")
    apksigner = os.environ.get("APKSIGNER_PATH")
    android_jar = os.environ.get("ANDROID_JAR_PATH")

    sdk_root = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not sdk_root and os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if local_appdata:
            sdk_root = os.path.join(local_appdata, "Android", "Sdk")

    build_tools_dir = os.path.join(sdk_root, "build-tools") if sdk_root and os.path.exists(os.path.join(sdk_root, "build-tools")) else None
    latest_build_tool = None
    if build_tools_dir:
        versions = sorted(os.listdir(build_tools_dir), reverse=True)
        if versions:
            latest_build_tool = os.path.join(build_tools_dir, versions[0])

    def locate_tool(env_val, tool_name):
        if env_val and os.path.exists(env_val):
            return env_val
        exe_name = f"{tool_name}.exe" if os.name == "nt" else tool_name
        bat_name = f"{tool_name}.bat" if os.name == "nt" else tool_name
        if latest_build_tool:
            for cand in [os.path.join(latest_build_tool, exe_name), os.path.join(latest_build_tool, bat_name)]:
                if os.path.exists(cand):
                    return cand
        return shutil.which(tool_name)

    aapt2 = locate_tool(aapt2, "aapt2")
    zipalign = locate_tool(zipalign, "zipalign")
    apksigner = locate_tool(apksigner, "apksigner")

    if not android_jar and sdk_root and os.path.exists(os.path.join(sdk_root, "platforms")):
        platforms_dir = os.path.join(sdk_root, "platforms")
        for plat in sorted(os.listdir(platforms_dir), reverse=True):
            candidate = os.path.join(platforms_dir, plat, "android.jar")
            if os.path.exists(candidate):
                android_jar = candidate
                break

    if not all([aapt2, zipalign, apksigner, android_jar]):
        raise RuntimeError("未完全定位到 Android SDK 工具链 (aapt2, zipalign, apksigner, android.jar)")

    return aapt2, zipalign, apksigner, android_jar

def ensure_debug_keystore() -> Path:
    keystore = Path.home() / ".android" / "debug.keystore"
    if keystore.exists():
        return keystore

    keystore.parent.mkdir(parents=True, exist_ok=True)
    keytool_bin = shutil.which("keytool")
    if not keytool_bin and os.environ.get("JAVA_HOME"):
        cand = Path(os.environ["JAVA_HOME"]) / "bin" / "keytool"
        if cand.exists():
            keytool_bin = str(cand)

    if not keytool_bin:
        raise RuntimeError("[!] 系统环境中缺少 keytool，无法自动生成签名证书。")

    cmd = [
        keytool_bin, "-genkeypair", "-v",
        "-keystore", str(keystore),
        "-storepass", "android", "-alias", "androiddebugkey", "-keypass", "android",
        "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
        "-dname", "CN=Android Debug,O=Android,C=US"
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return keystore


# ==============================================================================
# 3. 阶段一：网络获取、SHA256 校验与 Apktool 解包
# ==============================================================================

def get_latest_sha256() -> tuple[str | None, str | None]:
    json_files = [f for f in CONFIG_DIR.glob("*.json") if f.name != "base.json"]
    if not json_files:
        return None, None
    latest_file = max(json_files, key=lambda f: f.stat().st_mtime)
    try:
        with open(latest_file, "r", encoding="utf-8") as f:
            return latest_file.name, json.load(f).get("sha256")
    except Exception:
        return None, None

def fetch_changelog_info() -> tuple[str, str, list[str]]:
    headers = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'}
    req = urllib.request.Request(CHANGELOG_URL, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            html = resp.read().decode('utf-8')
        v_match = re.search(r'发布版本:[\s\S]*?([\d\.]+)', html)
        d_match = re.search(r'发布日期:[\s\S]*?(\d{4}-\d{2}-\d{2})', html)
        web_version = v_match.group(1).strip() if v_match else ""
        release_date = d_match.group(1).strip() if d_match else ""
        changelog = []
        if content_match := re.search(r'<div[^>]*class=["\']content["\'][^>]*>([\s\S]*?)</div>', html):
            for item in re.findall(r'<h2[^>]*>([\s\S]*?)</h2>', content_match.group(1)):
                clean = re.sub(r'<[^>]+>', '', item)
                clean = re.sub(r'^\s*[\-\–\—\•\*]\s*', '', clean).strip()
                if clean:
                    changelog.append(clean)
        return web_version, release_date, changelog
    except Exception:
        return "", "", []

def download_and_decompile_apk() -> tuple[str, str, str, str, list[str]]:
    web_version, release_date, changelog = fetch_changelog_info()
    print(f"[*] 正在下载最新官方 APK: {APK_URL}")
    req = urllib.request.Request(APK_URL, headers={'User-Agent': 'Mozilla/5.0 (Linux; Android 10)'})
    with urllib.request.urlopen(req) as resp, open(DOWNLOAD_APK_PATH, 'wb') as out_file:
        shutil.copyfileobj(resp, out_file)

    new_sha256 = calculate_sha256(DOWNLOAD_APK_PATH)
    print(f"[+] 下载完成，当前 APK SHA256: {new_sha256}")

    latest_file, last_sha256 = get_latest_sha256()
    if last_sha256 and last_sha256.lower() == new_sha256.lower():
        print(f"[=] 版本哈希与本地历史记录 ({latest_file}) 一致，无新更新，工作流终止。")
        sys.exit(0)

    print(f"[*] 开始使用 Apktool 解包 APK -> {DECOMPILE_DIR}")
    if DECOMPILE_DIR.exists():
        shutil.rmtree(DECOMPILE_DIR, ignore_errors=True)

    res = subprocess.run(["apktool", "d", str(DOWNLOAD_APK_PATH), "-o", str(DECOMPILE_DIR), "-f"], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Apktool 解包失败:\n{res.stderr}")

    yml_path = DECOMPILE_DIR / "apktool.yml"
    apk_code, apk_name = "", ""
    if yml_path.exists():
        with open(yml_path, "r", encoding="utf-8") as f:
            for line in f:
                if "versionCode:" in line:
                    apk_code = line.split("versionCode:")[1].strip(" '\"\r\n")
                elif "versionName:" in line:
                    apk_name = line.split("versionName:")[1].strip(" '\"\r\n")

    return new_sha256, apk_code, (apk_name or web_version or "unknown"), release_date, changelog


# ==============================================================================
# 4. 阶段二：增强型 Smali 反查引擎与三大资源独立适配
# ==============================================================================

def parse_public_xml_mappings(res_type: str) -> tuple[dict[str, str], dict[str, str]]:
    """
    解析 public.xml
    返回: (0x7f... ID -> 混淆名称, 原始未混淆 Key -> 0x7f... ID)
    """
    id_to_name = {}
    name_to_id = {}
    public_xml = DECOMPILE_DIR / "res" / "values" / "public.xml"
    if public_xml.exists():
        with open(public_xml, "r", encoding="utf-8", errors="ignore") as f:
            pattern = re.compile(rf'<public\s+type="{res_type}"\s+name="([^"]+)"\s+id="(0x7f[0-9a-fA-F]{6})"')
            for line in f:
                match = pattern.search(line)
                if match:
                    name, res_id = match.group(1), match.group(2).lower()
                    id_to_name[res_id] = name
                    name_to_id[name] = res_id
    return id_to_name, name_to_id

def parse_smali_mappings() -> dict[str, str]:
    """
    深度扫描 Smali，通过多重规则构建: 未混淆 key -> 0x7f... ID 映射
    """
    key_to_id = {}
    
    # 模式 1: 直接字段声明 `.field public static final key:I = 0x7f...`
    p_field = re.compile(r'\.field\s+.*?\s+([a-zA-Z0-9_$]+):I\s*=\s*(0x7f[0-9a-fA-F]{6})', re.IGNORECASE)
    
    # 模式 2: const + sput 组合 (常见于静态初始化块 clinit 中)
    p_const = re.compile(r'const[/\w]*\s+v\d+,\s*(0x7f[0-9a-fA-F]{6})', re.IGNORECASE)
    p_sput = re.compile(r'sput[/\w]*\s+v\d+,\s*L[^;]+;->([a-zA-Z0-9_$]+):I')

    # 模式 3: const-string "key" 紧跟着资源赋值
    p_str = re.compile(r'const-string\s+v\d+,\s*"([a-zA-Z0-9_$]+)"')

    for smali_dir in DECOMPILE_DIR.glob("smali*"):
        for smali_file in smali_dir.rglob("*.smali"):
            if not smali_file.is_file():
                continue
            
            with open(smali_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
                
                last_const_id = None
                last_str_key = None
                
                for line in lines:
                    # 规则 1
                    if m_field := p_field.search(line):
                        key_to_id[m_field.group(1)] = m_field.group(2).lower()
                        continue

                    # 规则 2
                    if m_const := p_const.search(line):
                        last_const_id = m_const.group(1).lower()
                    elif m_sput := p_sput.search(line):
                        if last_const_id:
                            key_to_id[m_sput.group(1)] = last_const_id
                            last_const_id = None

                    # 规则 3 (关联字符串与资源 ID)
                    if m_str := p_str.search(line):
                        last_str_key = m_str.group(1)
                    elif last_str_key and last_const_id:
                        key_to_id[last_str_key] = last_const_id
                        last_str_key = None

    return key_to_id


# --- PART A: COLOR 适配 ---
def process_theme_colors(colors_list: list, key_to_id: dict) -> list:
    print("[1/3] 开始处理 Theme Colors...")
    id_to_obf, name_to_id = parse_public_xml_mappings("color")
    processed = []
    matched_count = 0

    for item in colors_list:
        raw_key = item.get("key") or item.get("unobfuscated_key") or ""
        if not raw_key:
            continue

        # 优先通过 Smali 反查 ID，查不到则回退到 public.xml 尝试直连
        res_id = key_to_id.get(raw_key) or name_to_id.get(raw_key)
        obf_key = id_to_obf.get(res_id, "") if res_id else ""

        if obf_key and obf_key != raw_key:
            matched_count += 1

        new_item = {
            "unobfuscated_key": raw_key,
            "obfuscated_key": obf_key if obf_key else raw_key
        }
        if "light" in item:
            new_item["light"] = item["light"]
        if "night" in item:
            new_item["night"] = item["night"]

        processed.append(new_item)

    print(f"  └─ Theme Colors 完成: 共 {len(processed)} 项 (成功混淆映射: {matched_count} 项)")
    return processed


# --- PART B: STRING 适配 ---
def process_theme_strings(strings_list: list, key_to_id: dict) -> list:
    print("[2/3] 开始处理 Theme Strings...")
    id_to_obf, name_to_id = parse_public_xml_mappings("string")
    processed = []
    matched_count = 0

    for item in strings_list:
        raw_key = item.get("key") or item.get("unobfuscated_key") or ""
        if not raw_key:
            continue

        res_id = key_to_id.get(raw_key) or name_to_id.get(raw_key)
        obf_key = id_to_obf.get(res_id, "") if res_id else ""

        if obf_key and obf_key != raw_key:
            matched_count += 1

        new_item = {
            "unobfuscated_key": raw_key,
            "obfuscated_key": obf_key if obf_key else raw_key,
            "value": item.get("value", "")
        }
        processed.append(new_item)

    print(f"  └─ Theme Strings 完成: 共 {len(processed)} 项 (成功混淆映射: {matched_count} 项)")
    return processed


# --- PART C: DRAWABLE 适配 ---
def process_theme_drawables(drawables_list: list, key_to_id: dict) -> list:
    print("[3/3] 开始处理 Theme Drawables...")
    id_to_obf, name_to_id = parse_public_xml_mappings("drawable")
    processed = []
    matched_count = 0

    for item in drawables_list:
        raw_key = item.get("key") or item.get("unobfuscated_key") or ""
        if not raw_key:
            continue

        res_id = key_to_id.get(raw_key) or name_to_id.get(raw_key)
        obf_key = id_to_obf.get(res_id, "") if res_id else ""

        if obf_key and obf_key != raw_key:
            matched_count += 1

        file_path = item.get("file_path") or f"src/drawable/{raw_key}.png"

        new_item = {
            "unobfuscated_key": raw_key,
            "obfuscated_key": obf_key if obf_key else raw_key,
            "file_path": file_path
        }
        processed.append(new_item)

    print(f"  └─ Theme Drawables 完成: 共 {len(processed)} 项 (成功混淆映射: {matched_count} 项)")
    return processed


def generate_version_config(sha256_str: str, apk_code: str, apk_name: str, release_date: str, changelog: list) -> Path:
    if not BASE_CONFIG_PATH.exists():
        raise FileNotFoundError(f"[!] 找不到基础配置文件: {BASE_CONFIG_PATH}")

    print("[*] 正在扫描 Smali 映射表...")
    key_to_id = parse_smali_mappings()
    print(f"  └─ Smali 符号表中发现 {len(key_to_id)} 组 Key -> ID 映射")

    with open(BASE_CONFIG_PATH, "r", encoding="utf-8") as f:
        base_config = json.load(f)

    updated_colors = process_theme_colors(base_config.get("theme_colors", []), key_to_id)
    updated_strings = process_theme_strings(base_config.get("theme_strings", []), key_to_id)
    updated_drawables = process_theme_drawables(base_config.get("theme_drawables", []), key_to_id)

    safe_name = re.sub(r'[\\/:*?"<>|\s]', '_', apk_name)
    safe_code = re.sub(r'[\\/:*?"<>|\s]', '_', apk_code)
    filename = f"{safe_name}({safe_code}).json" if safe_code else f"{safe_name}.json"

    raw_payload = {
        "version_name": apk_name,
        "version_code": apk_code,
        "release_date": release_date,
        "sha256": sha256_str,
        "changelog": changelog,
        "theme_colors": updated_colors,
        "theme_strings": updated_strings,
        "theme_drawables": updated_drawables
    }

    config_path = CONFIG_DIR / filename
    out_path = OUT_DIR / filename

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in raw_payload.items() if v not in (None, "", [])}, f, ensure_ascii=False, indent=4)
    shutil.copy2(config_path, out_path)

    print(f"[+] 版本 JSON 配置文件生成完毕: {config_path}")
    return config_path


# ==============================================================================
# 5. 阶段三：同步资源与编译 Overlay
# ==============================================================================

def sync_src_resources(config_file: Path):
    with open(config_file, "r", encoding="utf-8") as f:
        config_data = json.load(f)

    res_dir = SRC_DIR / "res"
    values_day_dir = res_dir / "values"
    values_night_dir = res_dir / "values-night"
    drawable_target_dir = res_dir / "drawable"

    values_day_dir.mkdir(parents=True, exist_ok=True)
    values_night_dir.mkdir(parents=True, exist_ok=True)
    drawable_target_dir.mkdir(parents=True, exist_ok=True)

    # 1. Colors
    day_xml = ['<?xml version="1.0" encoding="utf-8"?>', '<resources>']
    night_xml = ['<?xml version="1.0" encoding="utf-8"?>', '<resources>']

    for item in config_data.get("theme_colors", []):
        key = item.get("obfuscated_key") or item.get("unobfuscated_key")
        if not key:
            continue
        if light := item.get("light"):
            day_xml.append(f'    <color name="{key}">{light}</color>')
        if night := item.get("night"):
            night_xml.append(f'    <color name="{key}">{night}</color>')

    day_xml.append('</resources>\n')
    night_xml.append('</resources>\n')

    (values_day_dir / "colors.xml").write_text("\n".join(day_xml), encoding="utf-8")
    (values_night_dir / "colors.xml").write_text("\n".join(night_xml), encoding="utf-8")

    # 2. Strings
    string_xml = ['<?xml version="1.0" encoding="utf-8"?>', '<resources>']
    for item in config_data.get("theme_strings", []):
        key = item.get("obfuscated_key") or item.get("unobfuscated_key")
        val = item.get("value")
        if key and val is not None:
            safe_val = str(val).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', '&quot;').replace("'", "\\'")
            string_xml.append(f'    <string name="{key}">{safe_val}</string>')
    string_xml.append('</resources>\n')

    (values_day_dir / "strings.xml").write_text("\n".join(string_xml), encoding="utf-8")

    # 3. Drawables
    for item in config_data.get("theme_drawables", []):
        raw_key = item.get("unobfuscated_key")
        obf_name = item.get("obfuscated_key") or raw_key
        file_path = item.get("file_path")

        if not obf_name:
            continue

        src_file = PROJECT_ROOT / file_path if file_path else None
        if not src_file or not src_file.exists():
            candidates = list((PROJECT_ROOT / "src").rglob(f"{raw_key}.*"))
            src_file = candidates[0] if candidates else None

        if src_file and src_file.exists():
            ext = src_file.suffix.lstrip('.')
            target_file = drawable_target_dir / f"{obf_name}.{ext}"
            shutil.copy2(src_file, target_file)

def build_overlay_apk():
    aapt2, zipalign, apksigner, android_jar = find_sdk_tools()
    target_apk_dir = BUILD_TMP_DIR / "files"
    target_apk_dir.mkdir(parents=True, exist_ok=True)

    compiled_zip = OUT_DIR / "compiled.zip"
    unsigned_apk = OUT_DIR / "unsigned.apk"
    aligned_apk = OUT_DIR / "aligned.apk"
    final_apk = target_apk_dir / "WetypeMonet.apk"

    subprocess.run([str(aapt2), "compile", "--dir", str(SRC_DIR / "res"), "-o", str(compiled_zip)], check=True)
    
    link_cmd = [
        str(aapt2), "link", "-I", str(android_jar),
        "--manifest", str(SRC_DIR / "AndroidManifest.xml"),
        "-o", str(unsigned_apk), str(compiled_zip),
        "--auto-add-overlay", "--min-sdk-version", "26", "--target-sdk-version", "35"
    ]
    subprocess.run(link_cmd, check=True)

    subprocess.run([str(zipalign), "-p", "-f", "4", str(unsigned_apk), str(aligned_apk)], check=True)
    keystore = ensure_debug_keystore()
    
    sign_cmd = [
        str(apksigner), "sign", "--ks", str(keystore),
        "--ks-pass", "pass:android", "--key-pass", "pass:android", "--ks-key-alias", "androiddebugkey",
        "--v1-signing-enabled", "false", "--v2-signing-enabled", "true",
        "--out", str(final_apk), str(aligned_apk)
    ]
    subprocess.run(sign_cmd, check=True)

    for tmp in [compiled_zip, unsigned_apk, aligned_apk, Path(f"{final_apk}.idsig")]:
        if tmp.exists():
            tmp.unlink()
    print(f"[+] Overlay APK 生成成功 -> {final_apk}")


# ==============================================================================
# 6. 阶段四：模块组装与打包
# ==============================================================================

def prepare_template():
    if BUILD_TMP_DIR.exists():
        shutil.rmtree(BUILD_TMP_DIR, ignore_errors=True)
    BUILD_TMP_DIR.mkdir(parents=True, exist_ok=True)
    if TEMPLATE_DIR.exists():
        shutil.copytree(TEMPLATE_DIR, BUILD_TMP_DIR, dirs_exist_ok=True)

def generate_module_prop(apk_name: str, apk_code: str) -> str:
    git_count, git_hash = get_git_info()
    v_code = apk_code or git_count
    v_name = f"{BASE_VERSION}-{apk_name}" if apk_name else f"{BASE_VERSION}-{git_hash}"
    description = f"{MODULE_DESCRIPTION} [构建时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}]"

    lines = [
        f"id={MODULE_ID}", f"name={MODULE_NAME}", f"version={v_name}",
        f"versionCode={v_code}", f"author={MODULE_AUTHOR}", f"description={description}"
    ]
    (BUILD_TMP_DIR / "module.prop").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return v_name

def create_module_zip(version_name: str):
    zip_path = OUT_DIR / f"{MODULE_ID}_{version_name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in BUILD_TMP_DIR.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(BUILD_TMP_DIR))
    print(f"[+] 刷机包构建成功: {zip_path}")


# ==============================================================================
# 7. 主流程执行控制
# ==============================================================================

def main():
    print("======================================================")
    print(" 微信输入法 Monet Overlay 自动化适配与构建工作流")
    print("======================================================\n")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        sha256_str, apk_code, apk_name, release_date, changelog = download_and_decompile_apk()
        config_path = generate_version_config(sha256_str, apk_code, apk_name, release_date, changelog)
        
        sync_src_resources(config_path)
        prepare_template()
        built_vname = generate_module_prop(apk_name, apk_code)
        build_overlay_apk()
        create_module_zip(built_vname)

        print("\n[✓] 所有步骤全流程顺利执行完毕！")
    except Exception as e:
        print(f"\n[!] 工作流执行异常中断: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()