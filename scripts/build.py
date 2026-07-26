#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
微信输入法 Monet Overlay 自动化适配与构建工作流 (GitHub Actions Linux 专用)
支持 theme_colors 与 theme_drawables 统一适配映射
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
BASE_CONFIG_PATH = CONFIG_DIR / "base.json"
DOWNLOAD_APK_PATH = OUT_DIR / "wetype_latest.apk"

# 远程源
APK_URL = "https://z.weixin.qq.com/android/download?channel=latest"
CHANGELOG_URL = "https://z.weixin.qq.com/web/changelog/android"

# 模块元数据
MODULE_ID = "Wetype_Monet"
MODULE_NAME = "微信输入法 Monet"
MODULE_AUTHOR = "酷安@1e93d"
MODULE_DESCRIPTION = "为微信输入法提供 Monet 动态色彩主题与资源定制。"
BASE_VERSION = "v1.0.0"


# ==============================================================================
# 2. 通用底层辅助函数
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

def calculate_sha256(file_path: Path) -> str:
    """计算指定文件的 SHA256"""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()

def find_sdk_tools() -> tuple[str, str, str, str]:
    """自动查找 aapt2, zipalign, apksigner 与 android.jar 路径"""
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

def get_latest_sha256() -> tuple[str | None, str | None]:
    """获取 config 目录下历史记录中最新的 JSON SHA256"""
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
# 4. 阶段二：解析公共映射与混淆对齐 (重构逻辑)
# ==============================================================================

def parse_public_xml_mappings(res_type: str) -> dict[str, str]:
    """解析 public.xml：0x7f... -> 混淆资源名 (obfuscated_name)"""
    id_to_name = {}
    public_xml = DECOMPILE_DIR / "res" / "values" / "public.xml"
    if public_xml.exists():
        with open(public_xml, "r", encoding="utf-8", errors="ignore") as f:
            pattern = re.compile(rf'<public\s+type="{res_type}"\s+name="([^"]+)"\s+id="(0x7f[0-9a-fA-F]{6})"')
            for line in f:
                match = pattern.search(line)
                if match:
                    id_to_name[match.group(2).lower()] = match.group(1)
    print(f"[+] public.xml 解析 [{res_type}] 完成，获取 {len(id_to_name)} 个资源 ID 定义")
    return id_to_name

def parse_smali_mappings() -> dict[str, str]:
    """通用 Smali 扫描器：源码字段/Key名 -> 0x7f... ID 映射"""
    key_to_id = {}
    field_pattern = re.compile(r'\.field\s+.*?\s+([a-zA-Z0-9_$]+):I\s*=\s*(0x7f[0-9a-fA-F]{6})', re.IGNORECASE)
    const_pattern = re.compile(r'const[/\w]*\s+v\d+,\s*(0x7f[0-9a-fA-F]{6})', re.IGNORECASE)
    sput_pattern = re.compile(r'sput[/\w]*\s+v\d+,\s*L[^;]+;->([a-zA-Z0-9_$]+):I')

    for smali_dir in DECOMPILE_DIR.glob("smali*"):
        for smali_file in smali_dir.rglob("*.smali"):
            if not smali_file.is_file():
                continue
            with open(smali_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                for match in field_pattern.finditer(content):
                    key_to_id[match.group(1)] = match.group(2).lower()
                
                lines = content.splitlines()
                last_const_id = None
                for line in lines:
                    c_match = const_pattern.search(line)
                    if c_match:
                        last_const_id = c_match.group(1).lower()
                    else:
                        sput_match = sput_pattern.search(line)
                        if sput_match and last_const_id:
                            key_to_id[sput_match.group(1)] = last_const_id
                            last_const_id = None

    print(f"[+] Smali 反查表建立完成，共抓取 {len(key_to_id)} 组 Key -> ID 映射")
    return key_to_id

def process_color_items(items: list, key_to_id: dict, id_to_obf_name: dict) -> list:
    """处理 theme_colors 的命名对齐逻辑"""
    processed = []
    for item in items:
        # 获取未混淆名称 (优先读 unobfuscated_key)
        unobf_key = item.get("unobfuscated_key") or item.get("key") or item.get("name") or ""
        if not unobf_key:
            continue

        res_id = key_to_id.get(unobf_key)
        obf_name = id_to_obf_name.get(res_id, "") if res_id else ""

        new_item = {
            "unobfuscated_key": unobf_key,
            "obfuscated_key": obf_name if obf_name else unobf_key
        }
        if "light" in item:
            new_item["light"] = item["light"]
        if "night" in item:
            new_item["night"] = item["night"]

        processed.append(new_item)
    return processed

def process_drawable_items(items: list, key_to_id: dict, id_to_obf_name: dict) -> list:
    """处理 theme_drawables 的命名对齐逻辑"""
    processed = []
    for item in items:
        unobf_name = item.get("unobfuscated_name") or item.get("name") or ""
        file_path = item.get("file_path") or ""
        if not unobf_name:
            continue

        res_id = key_to_id.get(unobf_name)
        obf_name = id_to_obf_name.get(res_id, "") if res_id else ""

        new_item = {
            "unobfuscated_name": unobf_name,
            "obfuscated_name": obf_name if obf_name else unobf_name,
            "file_path": file_path
        }
        processed.append(new_item)
    return processed

def generate_version_config(sha256_str: str, apk_code: str, apk_name: str, release_date: str, changelog: list) -> Path:
    if not BASE_CONFIG_PATH.exists():
        raise FileNotFoundError(f"[!] 找不到基础配置文件: {BASE_CONFIG_PATH}")

    # 解析映射
    color_public = parse_public_xml_mappings("color")
    drawable_public = parse_public_xml_mappings("drawable")
    key_to_id = parse_smali_mappings()

    with open(BASE_CONFIG_PATH, "r", encoding="utf-8") as f:
        base_config = json.load(f)

    updated_colors = process_color_items(base_config.get("theme_colors", []), key_to_id, color_public)
    updated_drawables = process_drawable_items(base_config.get("theme_drawables", []), key_to_id, drawable_public)

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
# 5. 阶段三：同步资源 (Colors & Drawables) 并编译 Overlay
# ==============================================================================

def sync_src_resources(config_file: Path):
    """根据生成的版本 JSON 清理并全新构建 src/res 中的 颜色与 Drawable 资源"""
    with open(config_file, "r", encoding="utf-8") as f:
        config_data = json.load(f)

    res_dir = SRC_DIR / "res"
    values_day_dir = res_dir / "values"
    values_night_dir = res_dir / "values-night"
    drawable_target_dir = res_dir / "drawable"

    # 清理并全新创建
    if drawable_target_dir.exists():
        shutil.rmtree(drawable_target_dir)
    values_day_dir.mkdir(parents=True, exist_ok=True)
    values_night_dir.mkdir(parents=True, exist_ok=True)
    drawable_target_dir.mkdir(parents=True, exist_ok=True)

    # 1. 写入 Colors
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
    print("[+] 颜色资源 colors.xml 同步完成")

    # 2. 复制并重命名 Drawables
    drawable_count = 0
    for item in config_data.get("theme_drawables", []):
        obf_name = item.get("obfuscated_name") or item.get("unobfuscated_name")
        file_path = item.get("file_path")
        if not obf_name or not file_path:
            continue

        src_file = PROJECT_ROOT / file_path
        if src_file.exists():
            ext = src_file.suffix  # 包含点号, .png / .xml
            target_file = drawable_target_dir / f"{obf_name}{ext}"
            shutil.copy2(src_file, target_file)
            drawable_count += 1
            print(f"[Drawable] 引入资源: {src_file.name} -> {target_file.name}")
        else:
            print(f"[!] 警告: 未找到指定的 Drawable 资源文件: {src_file}")

    print(f"[+] 共同步 {drawable_count} 个 Drawable 资源到 {drawable_target_dir}")

def build_overlay_apk():
    """编译并签名 Overlay APK，输出到 BUILD_TMP_DIR"""
    aapt2, zipalign, apksigner, android_jar = find_sdk_tools()
    
    # 将 Overlay APK 放置在模块模板的正确位置 (假设在 system/product/overlay/ 或 files/)
    # 优先创建 Overlay 存放路径
    target_apk_dir = BUILD_TMP_DIR / "system" / "product" / "overlay"
    if not (BUILD_TMP_DIR / "system").exists():
        target_apk_dir = BUILD_TMP_DIR / "files"
    target_apk_dir.mkdir(parents=True, exist_ok=True)

    compiled_zip = OUT_DIR / "compiled.zip"
    unsigned_apk = OUT_DIR / "unsigned.apk"
    aligned_apk = OUT_DIR / "aligned.apk"
    final_apk = target_apk_dir / "WetypeMonet.apk"

    # Compile
    subprocess.run([str(aapt2), "compile", "--dir", str(SRC_DIR / "res"), "-o", str(compiled_zip)], check=True)
    
    # Link
    link_cmd = [
        str(aapt2), "link", "-I", str(android_jar),
        "--manifest", str(SRC_DIR / "AndroidManifest.xml"),
        "-o", str(unsigned_apk), str(compiled_zip),
        "--auto-add-overlay", "--min-sdk-version", "26", "--target-sdk-version", "35"
    ]
    subprocess.run(link_cmd, check=True)

    # Zipalign & Sign
    subprocess.run([str(zipalign), "-p", "-f", "4", str(unsigned_apk), str(aligned_apk)], check=True)
    keystore = ensure_debug_keystore()
    
    sign_cmd = [
        str(apksigner), "sign", "--ks", str(keystore),
        "--ks-pass", "pass:android", "--key-pass", "pass:android", "--ks-key-alias", "androiddebugkey",
        "--v1-signing-enabled", "false", "--v2-signing-enabled", "true",
        "--out", str(final_apk), str(aligned_apk)
    ]
    subprocess.run(sign_cmd, check=True)

    # 清理过程中间文件
    for tmp in [compiled_zip, unsigned_apk, aligned_apk, Path(f"{final_apk}.idsig")]:
        if tmp.exists():
            tmp.unlink()
    print(f"[+] Overlay APK 成功构建并复制打包到: {final_apk}")


# ==============================================================================
# 6. 阶段四：模块组装与打包
# ==============================================================================

def prepare_template():
    """把模板目录全量拷贝到构建临时目录"""
    if BUILD_TMP_DIR.exists():
        shutil.rmtree(BUILD_TMP_DIR, ignore_errors=True)
    BUILD_TMP_DIR.mkdir(parents=True, exist_ok=True)
    if TEMPLATE_DIR.exists():
        shutil.copytree(TEMPLATE_DIR, BUILD_TMP_DIR, dirs_exist_ok=True)

def generate_module_prop(apk_name: str, apk_code: str) -> str:
    """优化版本与模块元数据生成机制"""
    git_count, git_hash = get_git_info()
    
    # Version Code: 优先用 APK 本身的 versionCode，否则用 git 提交数
    v_code = apk_code if apk_code else git_count
    
    # Version Name 优化格式: v1.0.0 (1.0.8) 或 v1.0.0 (dev_hash)
    version_suffix = apk_name if apk_name else git_hash
    v_name = f"{BASE_VERSION} ({version_suffix})"
    
    description = f"{MODULE_DESCRIPTION} [适配版本: {apk_name} | 构建: {datetime.now().strftime('%Y-%m-%d %H:%M')}]"

    lines = [
        f"id={MODULE_ID}",
        f"name={MODULE_NAME}",
        f"version={v_name}",
        f"versionCode={v_code}",
        f"author={MODULE_AUTHOR}",
        f"description={description}"
    ]
    (BUILD_TMP_DIR / "module.prop").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return v_name

def create_module_zip(version_name: str):
    """将 BUILD_TMP_DIR 压缩为标准的 Magisk/KernelSU Zip 刷机包"""
    # 格式化文件名，避免包含非法字符
    safe_vname = re.sub(r'[\\/:*?"<>|\s]', '_', version_name)
    zip_path = OUT_DIR / f"{MODULE_ID}_{safe_vname}.zip"
    
    if zip_path.exists():
        zip_path.unlink()
        
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in BUILD_TMP_DIR.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(BUILD_TMP_DIR))
    print(f"[+] 刷机模块 Zip 构建成功: {zip_path}")


# ==============================================================================
# 7. 主流程执行控制
# ==============================================================================

def main():
    print("======================================================")
    print(" 微信输入法 Monet Overlay 自动化适配与构建工作流 (扩展版)")
    print("======================================================\n")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        # 1. 下载解包
        sha256_str, apk_code, apk_name, release_date, changelog = download_and_decompile_apk()
        
        # 2. 生成版本 Config (计算混淆映射)
        config_path = generate_version_config(sha256_str, apk_code, apk_name, release_date, changelog)
        
        # 3. 同步 SRC 资源 (Colors 和 Drawables)
        sync_src_resources(config_path)
        
        # 4. 初始化模块模板
        prepare_template()
        
        # 5. 生成 module.prop
        built_vname = generate_module_prop(apk_name, apk_code)
        
        # 6. 编译、签名 Overlay APK 并写入模块目录
        build_overlay_apk()
        
        # 7. 压缩成 Magisk/KernelSU 模块 Zip
        create_module_zip(built_vname)

        print("\n[✓] 所有步骤全流程顺利执行完毕！")
    except Exception as e:
        print(f"\n[!] 工作流执行异常中断: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()