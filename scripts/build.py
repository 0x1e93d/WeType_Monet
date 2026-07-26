#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
微信输入法 Monet Overlay 自动化适配与构建工作流 (GitHub Actions Linux 专用)

阶段划分:
  - Phase 1: 检查更新、下载 APK 并使用 Apktool 解包
  - Phase 2: 提取 Smali 与 public.xml 中的 Key-ID 映射，生成版本 JSON 配置
  - Phase 3: 生成 values/colors.xml 与 values-night/colors.xml，并编译 Overlay APK
  - Phase 4: 组装模块结构并生成 Magisk/KernelSU 刷机 ZIP 包
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

# 目录规划
CONFIG_DIR = PROJECT_ROOT / "config"
SRC_DIR = PROJECT_ROOT / "src"
OUT_DIR = PROJECT_ROOT / "out"
BUILD_TMP_DIR = OUT_DIR / "build_tmp"
TEMPLATE_DIR = PROJECT_ROOT / "module_template"
DECOMPILE_DIR = OUT_DIR / "decompiled_apk"

# 关键文件
BASE_COLORS_PATH = CONFIG_DIR / "base_colors.json"
DOWNLOAD_APK_PATH = OUT_DIR / "wetype_latest.apk"

# 远程源
APK_URL = "https://z.weixin.qq.com/android/download?channel=latest"
CHANGELOG_URL = "https://z.weixin.qq.com/web/changelog/android"

# 模块元数据
MODULE_ID = "Wetype_Monet"
MODULE_NAME = "微信输入法 Monet"
MODULE_AUTHOR = "酷安@1e93d"
MODULE_DESCRIPTION = "为微信输入法提供 Monet 动态色彩主题。"
BASE_VERSION = "v1.0.0"
UPDATE_JSON_URL = ""

# ==============================================================================
# 2. Linux 环境工具链定位与 Git 辅助函数
# ==============================================================================

def get_git_info() -> tuple[str, str]:
    """获取当前仓库提交次数与 Short Hash"""
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

def find_sdk_tools() -> tuple[str, str, str, str]:
    """
    自动查找 aapt2, zipalign, apksigner 与 android.jar 路径
    兼容 Windows 本地环境与 GitHub Actions (Ubuntu / macOS / Windows) 环境
    """
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

        # 1. 优先使用 SDK build-tools 内配套的高版本工具
        if latest_build_tool:
            for cand in [os.path.join(latest_build_tool, exe_name), os.path.join(latest_build_tool, bat_name)]:
                if os.path.exists(cand):
                    return cand

        # 2. 兜底使用系统 PATH 中的工具
        found = shutil.which(tool_name)
        if found:
            return found

        return None

    aapt2 = locate_tool(aapt2, "aapt2")
    zipalign = locate_tool(zipalign, "zipalign")
    apksigner = locate_tool(apksigner, "apksigner")

    if not android_jar and sdk_root and os.path.exists(os.path.join(sdk_root, "platforms")):
        platforms_dir = os.path.join(sdk_root, "platforms")
        platforms = sorted(os.listdir(platforms_dir), reverse=True)
        for plat in platforms:
            candidate = os.path.join(platforms_dir, plat, "android.jar")
            if os.path.exists(candidate):
                android_jar = candidate
                break

    if not aapt2 or not os.path.exists(aapt2):
        raise RuntimeError("未找到 aapt2！请确认已安装 Android SDK 或手动配置 AAPT2_PATH 环境变量。")
    if not zipalign or not os.path.exists(zipalign):
        raise RuntimeError("未找到 zipalign！请确认已安装 Android SDK 或手动配置 ZIPALIGN_PATH 环境变量。")
    if not apksigner or not os.path.exists(apksigner):
        raise RuntimeError("未找到 apksigner！请确认已安装 Android SDK 或手动配置 APKSIGNER_PATH 环境变量。")
    if not android_jar or not os.path.exists(android_jar):
        raise RuntimeError("未找到 android.jar！请确认已安装 Android SDK 或手动配置 ANDROID_JAR_PATH 环境变量。")

    print(f"[+] 成功定位工具链:\n    - AAPT2: {aapt2}\n    - ZIPALIGN: {zipalign}\n    - APKSIGNER: {apksigner}\n    - ANDROID_JAR: {android_jar}")
    return aapt2, zipalign, apksigner, android_jar

def ensure_debug_keystore() -> Path:
    """确保 ~/.android/debug.keystore 存在，不存在则自动生成"""
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

def calculate_sha256(file_path: Path) -> str:
    """计算指定文件的 SHA256"""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()

def get_latest_sha256() -> tuple[str | None, str | None]:
    """获取 config 目录下历史记录中最新的 JSON SHA256"""
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
    """正则抓取官网的版本号、更新日期和更新日志"""
    headers = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
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
            h2_items = re.findall(r'<h2[^>]*>([\s\S]*?)</h2>', content_match.group(1))
            for item in h2_items:
                clean = re.sub(r'<[^>]+>', '', item)
                clean = re.sub(r'^\s*[\-\–\—\•\*]\s*', '', clean).strip()
                if clean:
                    changelog.append(clean)

        print(f"[+] 官网版本: '{web_version}' | 日期: '{release_date}' | 日志: {len(changelog)} 条")
        return web_version, release_date, changelog
    except Exception as e:
        print(f"[!] 抓取官网日志失败: {e}")
        return "", "", []

def download_and_decompile_apk() -> tuple[str, str, str, str, list[str]]:
    """下载并解包 APK"""
    web_version, release_date, changelog = fetch_changelog_info()

    print(f"[*] 正在下载最新官方 APK: {APK_URL}")
    req = urllib.request.Request(APK_URL, headers={'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36'})
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

    res = subprocess.run(
        ["apktool", "d", str(DOWNLOAD_APK_PATH), "-o", str(DECOMPILE_DIR), "-f"],
        capture_output=True, text=True
    )
    if res.returncode != 0:
        raise RuntimeError(f"Apktool 解包失败:\n{res.stderr}")
    print("[+] Apktool 解包完成")

    yml_path = DECOMPILE_DIR / "apktool.yml"
    apk_code, apk_name = "", ""
    if yml_path.exists():
        with open(yml_path, "r", encoding="utf-8") as f:
            for line in f:
                if "versionCode:" in line:
                    apk_code = line.split("versionCode:")[1].strip(" '\"\r\n")
                elif "versionName:" in line:
                    apk_name = line.split("versionName:")[1].strip(" '\"\r\n")

    final_apk_name = apk_name or web_version or "unknown_version"
    final_changelog = changelog if (not web_version or web_version == final_apk_name) else []

    return new_sha256, apk_code, final_apk_name, release_date, final_changelog

# ==============================================================================
# 4. 阶段二：提取 Smali & public.xml 混淆 Key 映射与生成配置
# ==============================================================================

def parse_public_xml_color_mappings() -> dict[str, str]:
    """从 res/values/public.xml 解析出真实的 Resource ID 与未混淆 XML 资源名映射 (Key: 0x7f..., Value: Resource_Name)"""
    id_to_unobfuscated = {}
    public_xml = DECOMPILE_DIR / "res" / "values" / "public.xml"
    if public_xml.exists():
        with open(public_xml, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                match = re.search(r'<public\s+type="color"\s+name="([^"]+)"\s+id="(0x7f[0-9a-fA-F]{6})"', line)
                if match:
                    res_name, res_id = match.group(1), match.group(2).lower()
                    id_to_unobfuscated[res_id] = res_name
    print(f"[+] public.xml 解析完成，获取 {len(id_to_unobfuscated)} 个颜色资源 ID 定义")
    return id_to_unobfuscated

def parse_smali_color_mappings() -> dict[str, str]:
    """
    深度扫描所有 R$color.smali 及相关类文件中的混淆 Field 与 Resource ID 映射。
    返回字典: { Resource_ID (0x7f...) : Smali_Field_Name }
    """
    id_to_obfuscated = {}
    
    # 精确内联字段映射匹配
    field_inline_pattern = re.compile(
        r'\.field\s+.*?\s+([a-zA-Z0-9_$]+):I\s*=\s*(0x7f[0-9a-fA-F]{6})', re.IGNORECASE
    )
    
    # 静态代码块 <clinit> 赋值匹配
    const_pattern = re.compile(r'const[/\w]*\s+v\d+,\s*(0x7f[0-9a-fA-F]{6})', re.IGNORECASE)
    sput_pattern = re.compile(r'sput[/\w]*\s+v\d+,\s*L[^;]+;->([a-zA-Z0-9_$]+):I')

    smali_dirs = list(DECOMPILE_DIR.glob("smali*"))
    for smali_dir in smali_dirs:
        # 1. 查找包含 R$color 的类文件
        r_color_files = list(smali_dir.rglob("*R$color*.smali"))
        
        # 2. 回退机制：若找不到包含 R$color 的类，则扫描该 smali 文件夹下的所有 smali 文件
        files_to_scan = r_color_files if r_color_files else list(smali_dir.rglob("*.smali"))

        for smali_file in files_to_scan:
            if not smali_file.is_file():
                continue
            with open(smali_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                
                # 方式 A: 内联赋值匹配
                for match in field_inline_pattern.finditer(content):
                    field_name, res_id = match.group(1), match.group(2).lower()
                    id_to_obfuscated[res_id] = field_name

                # 方式 B: <clinit> 异步赋值流向匹配
                lines = content.splitlines()
                last_const_id = None
                for line in lines:
                    c_match = const_pattern.search(line)
                    if c_match:
                        last_const_id = c_match.group(1).lower()
                        continue
                    s_match = sput_pattern.search(line)
                    if s_match and last_const_id:
                        field_name = s_match.group(1)
                        id_to_obfuscated[last_const_id] = field_name
                        last_const_id = None

    print(f"[+] Smali 扫描完成，获取 {len(id_to_obfuscated)} 项 ID -> 混淆 Field 映射关系")
    return id_to_obfuscated

def generate_version_config(
    sha256_str: str, apk_code: str, apk_name: str, release_date: str, changelog: list[str]
) -> Path:
    """结合 base_colors.json 模板生成当前版本的 JSON 映射配置"""
    if not BASE_COLORS_PATH.exists():
        raise FileNotFoundError(f"[!] 找不到基础颜色模板: {BASE_COLORS_PATH}")

    # 1. 解析字典
    id_to_obfuscated = parse_smali_color_mappings()        # 0x7f060001 -> "a_a_a"
    id_to_unobfuscated = parse_public_xml_color_mappings()  # 0x7f060001 -> "White"

    # 2. 建立双向反查表: raw_key ("White") -> 0x7f060001
    unobf_name_to_id = {v: k for k, v in id_to_unobfuscated.items()}

    with open(BASE_COLORS_PATH, "r", encoding="utf-8") as f:
        base_colors = json.load(f).get("theme_colors", [])

    updated_colors, missing_keys = [], []

    for item in base_colors:
        raw_key = item.get("key")  # 如 "White"
        
        # 查找未混淆 key 对应的 Resource ID
        res_id = unobf_name_to_id.get(raw_key)
        
        obf_key = ""
        if res_id:
            # 根据 Resource ID 查找 Smali 混淆 Field 名
            obf_key = id_to_obfuscated.get(res_id, "")

        # 保留为空的情况：未查到混淆键，或混淆键与未混淆键一致
        if obf_key == raw_key:
            obf_key = ""

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
        print(f"[!] 警告: 共 {len(missing_keys)} 个 Key 未能在 public.xml 中找到 Resource ID: {missing_keys}")

    safe_name = re.sub(r'[\\/:*?"<>|\s]', '_', apk_name)
    safe_code = re.sub(r'[\\/:*?"<>|\s]', '_', apk_code)
    filename = f"{safe_name}({safe_code}).json" if safe_code else f"{safe_name}.json"

    raw_payload = {
        "version_name": apk_name,
        "version_code": apk_code,
        "release_date": release_date,
        "sha256": sha256_str,
        "changelog": changelog,
        "theme_colors": updated_colors
    }

    # 过滤空值属性
    json_payload = {
        k: v for k, v in raw_payload.items()
        if v not in (None, "", [])
    }

    config_path = CONFIG_DIR / filename
    out_path = OUT_DIR / filename

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(json_payload, f, ensure_ascii=False, indent=4)
    shutil.copy2(config_path, out_path)

    print(f"[+] 配置生成完毕:\n    - Config: {config_path}\n    - Out: {out_path}")
    return config_path

# ==============================================================================
# 5. 阶段三：补全 values / values-night 并编译 Overlay APK
# ==============================================================================

def sync_src_colors_xml(config_file: Path):
    """
    根据配置文件动态生成:
      - src/res/values/colors.xml (日间)
      - src/res/values-night/colors.xml (夜间)
    """
    with open(config_file, "r", encoding="utf-8") as f:
        theme_colors = json.load(f).get("theme_colors", [])

    res_dir = SRC_DIR / "res"
    values_day_dir = res_dir / "values"
    values_night_dir = res_dir / "values-night"

    values_day_dir.mkdir(parents=True, exist_ok=True)
    values_night_dir.mkdir(parents=True, exist_ok=True)

    day_xml_lines = ['<?xml version="1.0" encoding="utf-8"?>', '<resources>']
    night_xml_lines = ['<?xml version="1.0" encoding="utf-8"?>', '<resources>']

    for item in theme_colors:
        # 优先选择混淆 Key，否则降级使用未混淆 Key
        key_name = item.get("obfuscated_key") or item.get("unobfuscated_key")
        
        light_color = item.get("light")
        if light_color:
            day_xml_lines.append(f'    <color name="{key_name}">{light_color}</color>')

        night_color = item.get("night")
        if night_color:
            night_xml_lines.append(f'    <color name="{key_name}">{night_color}</color>')

    day_xml_lines.append('</resources>\n')
    night_xml_lines.append('</resources>\n')

    day_path = values_day_dir / "colors.xml"
    night_path = values_night_dir / "colors.xml"

    day_path.write_text("\n".join(day_xml_lines), encoding="utf-8")
    night_path.write_text("\n".join(night_xml_lines), encoding="utf-8")

    print(f"[+] 补全日间模式色彩: {day_path}")
    print(f"[+] 补全夜间模式色彩: {night_path}")

def build_overlay_apk():
    """调用 AAPT2 编译 Overlay，对齐并签名"""
    aapt2, zipalign, apksigner, android_jar = find_sdk_tools()

    target_apk_dir = BUILD_TMP_DIR / "files"
    target_apk_dir.mkdir(parents=True, exist_ok=True)

    compiled_zip = OUT_DIR / "compiled.zip"
    unsigned_apk = OUT_DIR / "unsigned.apk"
    aligned_apk = OUT_DIR / "aligned.apk"
    final_apk = target_apk_dir / "WetypeMonet.apk"

    res_dir = SRC_DIR / "res"
    manifest_xml = SRC_DIR / "AndroidManifest.xml"

    # 1. Compile
    print("[1/4] 编译资源 (aapt2 compile)...")
    res = subprocess.run(
        [str(aapt2), "compile", "--dir", str(res_dir), "-o", str(compiled_zip)],
        capture_output=True, text=True, encoding="utf-8", errors="ignore"
    )
    if res.returncode != 0:
        raise RuntimeError(f"aapt2 compile 失败:\n{res.stderr or res.stdout}")

    # 2. Link
    print("[2/4] 链接 APK (aapt2 link)...")
    link_cmd = [
        str(aapt2), "link",
        "-I", str(android_jar),
        "--manifest", str(manifest_xml),
        "-o", str(unsigned_apk),
        str(compiled_zip),
        "--auto-add-overlay",
        "--min-sdk-version", "26",
        "--target-sdk-version", "35"
    ]
    res = subprocess.run(link_cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if res.returncode != 0:
        raise RuntimeError(f"aapt2 link 失败:\n{res.stderr or res.stdout}")

    # 3. Zipalign
    print("[3/4] 4 字节对齐 (zipalign)...")
    if aligned_apk.exists():
        aligned_apk.unlink()
    subprocess.run([str(zipalign), "-p", "-f", "4", str(unsigned_apk), str(aligned_apk)], check=True)

    # 4. Apksigner
    print("[4/4] 使用 Debug Key 进行 V2 签名 (apksigner)...")
    keystore = ensure_debug_keystore()
    if final_apk.exists():
        final_apk.unlink()

    sign_cmd = [
        str(apksigner), "sign",
        "--ks", str(keystore),
        "--ks-pass", "pass:android",
        "--key-pass", "pass:android",
        "--ks-key-alias", "androiddebugkey",
        "--v1-signing-enabled", "false",
        "--v2-signing-enabled", "true",
        "--v3-signing-enabled", "false",
        "--v4-signing-enabled", "false",
        "--out", str(final_apk),
        str(aligned_apk)
    ]
    subprocess.run(sign_cmd, check=True)

    for tmp in [compiled_zip, unsigned_apk, aligned_apk, Path(f"{final_apk}.idsig")]:
        if tmp.exists():
            tmp.unlink()

    print(f"[+] Overlay APK 生成成功 -> {final_apk}")

# ==============================================================================
# 6. 阶段四：组装模块与制作 ZIP 刷机包
# ==============================================================================

def prepare_template():
    """同步模块模板结构"""
    if BUILD_TMP_DIR.exists():
        shutil.rmtree(BUILD_TMP_DIR, ignore_errors=True)
    BUILD_TMP_DIR.mkdir(parents=True, exist_ok=True)

    if TEMPLATE_DIR.exists():
        shutil.copytree(TEMPLATE_DIR, BUILD_TMP_DIR, dirs_exist_ok=True)

def generate_module_prop(version_name_override: str, version_code_override: str) -> str:
    """自动生成模块声明说明文件 module.prop"""
    git_count, git_hash = get_git_info()
    
    v_code = (version_code_override or os.environ.get("VERSION_CODE", git_count)).strip()
    v_name_raw = version_name_override.strip() if version_name_override else ""
    
    version_code = v_code
    version_name = f"{BASE_VERSION}-{v_name_raw}" if v_name_raw else f"{BASE_VERSION}-{git_hash}"

    build_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    description = f"{MODULE_DESCRIPTION} [构建时间: {build_time}]"

    lines = [
        f"id={MODULE_ID}",
        f"name={MODULE_NAME}",
        f"version={version_name}",
        f"versionCode={version_code}",
        f"author={MODULE_AUTHOR}",
        f"description={description}"
    ]
    if UPDATE_JSON_URL.strip():
        lines.append(f"updateJson={UPDATE_JSON_URL.strip()}")

    (BUILD_TMP_DIR / "module.prop").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[+] module.prop 生成完成 (Version: {version_name}, Code: {version_code})")
    return version_name

def create_module_zip(version_name: str):
    """打包输出 Magisk/KernelSU ZIP"""
    print("[*] 打包 Magisk/KernelSU 刷机 ZIP 包...")
    zip_filename = f"{MODULE_ID}_{version_name}.zip"
    zip_path = OUT_DIR / zip_filename

    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in BUILD_TMP_DIR.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(BUILD_TMP_DIR)
                zf.write(file_path, arcname)

    print(f"[+] 刷机包构建成功: {zip_path}")

# ==============================================================================
# 7. 主执行流程控制
# ==============================================================================

def main():
    print("======================================================")
    print("   微信输入法 Monet Overlay 自动化适配与构建工作流  ")
    print("======================================================\n")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        # Phase 1: 检查更新 & 检索/解包 APK
        print("[+] ===== 阶段 1: 检查更新 & 检索/解包 APK =====")
        sha256_str, apk_code, apk_name, release_date, changelog = download_and_decompile_apk()

        # Phase 2: 解析 ID 映射 & 生成配置
        print("\n[+] ===== 阶段 2: 解析 ID 映射 & 生成配置 =====")
        config_path = generate_version_config(sha256_str, apk_code, apk_name, release_date, changelog)

        # Phase 3: 准备双模式 XML 资源 & 编译 Overlay
        print("\n[+] ===== 阶段 3: 补全 values/values-night & 编译签名 Overlay APK =====")
        sync_src_colors_xml(config_path)
        prepare_template()
        built_vname = generate_module_prop(apk_name, apk_code)
        build_overlay_apk()

        # Phase 4: 打包 Magisk/KernelSU 模块 ZIP
        print("\n[+] ===== 阶段 4: 打包 Magisk/KernelSU 模块 ZIP =====")
        create_module_zip(built_vname)

        print("\n[✓] 所有步骤全流程顺利执行完毕！")

    except Exception as e:
        print(f"\n[!] 工作流执行异常中断: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()