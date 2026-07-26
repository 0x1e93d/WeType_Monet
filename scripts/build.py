#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
微信输入法 Monet Overlay 自动化适配与构建工作流 (GitHub Actions Linux 专用)

阶段划分:
  - Phase 1: 检查更新、下载 APK 并使用 Apktool 解包
  - Phase 2: 提取 Smali 中的 Key-ID 映射，生成版本 JSON 配置
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

def find_linux_sdk_tools() -> tuple[Path, Path, Path, Path]:
    """定位 Linux 容器内的 Android SDK 工具链 (aapt2, zipalign, apksigner, android.jar)"""
    sdk_root = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or "/usr/lib/android-sdk"
    sdk_path = Path(sdk_root)

    def locate_tool(env_var: str, tool_name: str) -> Path:
        if (env_val := os.environ.get(env_var)) and Path(env_val).exists():
            return Path(env_val)
        if found := shutil.which(tool_name):
            return Path(found)
        if sdk_path.exists() and (sdk_path / "build-tools").exists():
            bt_dirs = sorted((sdk_path / "build-tools").iterdir(), reverse=True)
            for bt in bt_dirs:
                cand = bt / tool_name
                if cand.exists():
                    return cand
        raise RuntimeError(f"[!] 找不到必要构建工具: {tool_name}，请检查 Actions 环境依赖。")

    aapt2 = locate_tool("AAPT2_PATH", "aapt2")
    zipalign = locate_tool("ZIPALIGN_PATH", "zipalign")
    apksigner = locate_tool("APKSIGNER_PATH", "apksigner")

    # 定位 android.jar，防踩坑：过滤掉预览版或大于 35 的不兼容 platform 目录
    android_jar = None
    if (jar_env := os.environ.get("ANDROID_JAR_PATH")) and Path(jar_env).exists():
        android_jar = Path(jar_env)
    elif sdk_path.exists() and (sdk_path / "platforms").exists():
        # 筛选合法且稳定的 android-XX 平台，优先选 35/34 等稳定版 API
        candidate_platforms = []
        for p in (sdk_path / "platforms").iterdir():
            if p.is_dir() and (p / "android.jar").exists():
                match = re.search(r'android-(\d+)', p.name)
                if match:
                    api_level = int(match.group(1))
                    # 限制最高 API 级别为 35，避开不兼容的 36+ 或小数点预览版
                    if api_level <= 35:
                        candidate_platforms.append((api_level, p / "android.jar"))
        
        if candidate_platforms:
            # 排序后取最高且稳定的版本 (比如 android-35)
            candidate_platforms.sort(key=lambda x: x[0], reverse=True)
            android_jar = candidate_platforms[0][1]

    if not android_jar:
        # 兜底保底方案
        fallback = sdk_path / "platforms" / "android-35" / "android.jar"
        if fallback.exists():
            android_jar = fallback
        else:
            raise RuntimeError("[!] 未能定位兼容的 android.jar，请指定 ANDROID_JAR_PATH 环境变量。")

    print(f"[+] 选中编译依赖基础库: {android_jar}")
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
    req = urllib.request.Request(APK_URL, headers={'User-Agent': 'Mozilla/5.0 (Linux)'})
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
                    apk_code = line.split("versionCode:")[1].strip(" '\"")
                elif "versionName:" in line:
                    apk_name = line.split("versionName:")[1].strip(" '\"")

    final_apk_name = apk_name or web_version or "unknown_version"
    final_changelog = changelog if (not web_version or web_version == final_apk_name) else []

    return new_sha256, apk_code, final_apk_name, release_date, final_changelog

# ==============================================================================
# 4. 阶段二：提取 Smali 混淆 Key 映射与生成配置
# ==============================================================================

def parse_smali_color_mappings() -> dict[str, str]:
    """深度正则扫描 Smali 代码中的资源 ID 定义"""
    key_to_id = {}
    smali_field_pattern = re.compile(r'\.field\s+.*?\s+([a-zA-Z0-9_]+):I\s*=\s*(0x7f[0-9a-fA-F]+)')
    
    for smali_dir in DECOMPILE_DIR.glob("smali*"):
        target_pkg = smali_dir / "com" / "tencent" / "wetype"
        if not target_pkg.exists():
            continue
        for smali_file in target_pkg.rglob("*.smali"):
            with open(smali_file, "r", encoding="utf-8", errors="ignore") as f:
                for match in smali_field_pattern.finditer(f.read()):
                    key_to_id[match.group(1)] = match.group(2).lower()

    print(f"[+] Smali 扫描完成，共获取 {len(key_to_id)} 项 Key-ID 字典")
    return key_to_id

def generate_version_config(
    sha256_str: str, apk_code: str, apk_name: str, release_date: str, changelog: list[str]
) -> Path:
    """结合 base_colors.json 模板生成当前版本的 JSON 映射配置"""
    if not BASE_COLORS_PATH.exists():
        raise FileNotFoundError(f"[!] 找不到基础颜色模板: {BASE_COLORS_PATH}")

    key_to_id = parse_smali_color_mappings()

    with open(BASE_COLORS_PATH, "r", encoding="utf-8") as f:
        base_colors = json.load(f).get("theme_colors", [])

    updated_colors, missing_keys = [], []
    for item in base_colors:
        raw_key = item.get("key")
        res_id = key_to_id.get(raw_key)
        
        if not res_id:
            missing_keys.append(raw_key)

        updated_colors.append({
            "unobfuscated_key": raw_key,
            "obfuscated_key": raw_key,  # 若需配合混淆替换，可在此处绑定关联逻辑
            "light": item.get("light"),
            "night": item.get("night"),
            "description": item.get("description", "")
        })

    if missing_keys:
        print(f"[!] 警告: 共 {len(missing_keys)} 个 Key 未能在 Smali 中发现硬编码定义。")

    safe_name = re.sub(r'[\\/:*?"<>|\s]', '_', apk_name)
    safe_code = re.sub(r'[\\/:*?"<>|\s]', '_', apk_code)
    filename = f"{safe_name}({safe_code}).json" if safe_code else f"{safe_name}.json"

    json_payload = {
        "version_name": apk_name,
        "version_code": apk_code,
        "release_date": release_date,
        "sha256": sha256_str,
        "changelog": changelog,
        "theme_colors": updated_colors
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
    aapt2, zipalign, apksigner, android_jar = find_linux_sdk_tools()

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
    version_code = version_code_override or os.environ.get("VERSION_CODE", git_count)
    version_name = f"{BASE_VERSION}-{version_name_override}" if version_name_override else f"{BASE_VERSION}-{git_hash}"

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