#!/usr/bin/env python3
"""构建微信输入法 Monet Overlay 模块。"""

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
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent

CONFIG_DIR = PROJECT_ROOT / "config"
OVERLAY_DIR = PROJECT_ROOT / "overlay"
OUT_DIR = PROJECT_ROOT / "out"
BUILD_TMP_DIR = OUT_DIR / "build_tmp"
TEMPLATE_DIR = PROJECT_ROOT / "module_template"
DECOMPILE_DIR = OUT_DIR / "decompiled_apk"
BUILD_METADATA_PATH = OUT_DIR / "internal" / "build-metadata.json"

BASE_CONFIG_PATH = CONFIG_DIR / "base.json"
TARGET_CONFIG_DIR = CONFIG_DIR / "targets"
LATEST_CONFIG_PATH = CONFIG_DIR / "latest.json"
UPDATE_JSON_PATH = PROJECT_ROOT / "wetype_monet.json"
DOWNLOAD_APK_PATH = OUT_DIR / "wetype_latest.apk"
PUBLIC_SIGNING_BKS_PATH = PROJECT_ROOT / "signing" / "LSPatch.bks"
PUBLIC_SIGNING_PASSWORD = "114514"
PUBLIC_SIGNING_ALIAS = "114514"
HLD_PACKAGE_PATH = Path("com/tencent/wetype/plugin/hld")

APK_URL = "https://z.weixin.qq.com/android/download?channel=latest"
CHANGELOG_URL = "https://z.weixin.qq.com/web/changelog/android"

MODULE_ID = "Wetype_Monet"
MODULE_NAME = "微信输入法 Monet"
MODULE_AUTHOR = "酷安@1e93d"
MODULE_DESCRIPTION = "为微信输入法提供 Monet 动态色彩主题。"
REPOSITORY_SLUG = os.environ.get("GITHUB_REPOSITORY", "0x1e93d/WeType_Monet")
UPDATE_JSON_URL = f"https://raw.githubusercontent.com/{REPOSITORY_SLUG}/main/wetype_monet.json"


def format_module_version(module_version: int) -> str:
    if isinstance(module_version, bool) or not isinstance(module_version, int) or module_version < 1:
        raise ValueError("[!] 模块版本必须为不小于 1 的整数")
    return f"v{module_version}"


def get_module_zip_filename(module_version: int) -> str:
    return f"{MODULE_ID}_{format_module_version(module_version)}.zip"


def get_official_apk_filename(apk_name: str, apk_code: str) -> str:
    safe_name = re.sub(r'[\\/:*?"<>|\s]', "_", apk_name)
    safe_code = re.sub(r'[\\/:*?"<>|\s]', "_", apk_code)
    return f"微信输入法_{safe_name}({safe_code}).apk"


def get_monet_apk_filename(apk_name: str, apk_code: str, module_version: int) -> str:
    safe_name = re.sub(r'[\\/:*?"<>|\s]', "_", apk_name)
    safe_code = re.sub(r'[\\/:*?"<>|\s]', "_", apk_code)
    return f"微信输入法_Monet_{safe_name}({safe_code})_{format_module_version(module_version)}.apk"


def get_release_title(apk_name: str, module_version: int) -> str:
    return f"微信输入法_{apk_name}_{format_module_version(module_version)}"


def current_build_time() -> str:
    try:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
    except ZoneInfoNotFoundError:
        now = datetime.now().astimezone()
    return now.strftime("%Y-%m-%d %H:%M UTC+08:00")

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

    print(
        "[+] 成功定位工具链:"
        f"\n    - AAPT2: {aapt2}"
        f"\n    - ZIPALIGN: {zipalign}"
        f"\n    - APKSIGNER: {apksigner}"
        f"\n    - ANDROID_JAR: {android_jar}"
    )
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

def calculate_sha256(file_path: Path) -> str:
    """计算指定文件的 SHA256"""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()

def calculate_canonical_json_sha256(file_path: Path) -> str:
    """计算 JSON 有效内容的稳定 SHA256，忽略格式差异。"""
    data = json.loads(file_path.read_text(encoding="utf-8"))
    canonical_json = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical_json).hexdigest()


def get_base_sha256() -> str:
    """读取基础资源配置的有效内容 SHA256。"""
    if not BASE_CONFIG_PATH.is_file():
        raise FileNotFoundError(f"[!] 找不到基础配置文件: {BASE_CONFIG_PATH}")
    return calculate_canonical_json_sha256(BASE_CONFIG_PATH)


def is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None


def get_latest_build_state() -> dict | None:
    """读取最后一次成功发布的模块状态。"""
    if not LATEST_CONFIG_PATH.is_file():
        return None

    try:
        state = json.loads(LATEST_CONFIG_PATH.read_text(encoding="utf-8"))
        upstream = state.get("upstream")
        if state.get("state_version") != 1 or not isinstance(upstream, dict):
            return None
        if isinstance(state.get("module_version"), bool) or not isinstance(
            state.get("module_version"), int
        ) or state["module_version"] < 1:
            return None
        if not is_sha256(state.get("base_sha256")) or not is_sha256(upstream.get("sha256")):
            return None
        if not all(isinstance(upstream.get(key), str) for key in ("version_name", "version_code", "config_file")):
            return None

        config_relative_path = Path(upstream["config_file"])
        if (
            config_relative_path.is_absolute()
            or ".." in config_relative_path.parts
            or not config_relative_path.parts
            or config_relative_path.parts[0] != TARGET_CONFIG_DIR.name
            or config_relative_path.suffix != ".json"
        ):
            return None
        if not (CONFIG_DIR / config_relative_path).is_file():
            return None
        return state
    except (OSError, json.JSONDecodeError):
        return None


def get_latest_sha256() -> tuple[str | None, str | None]:
    """读取最后一次成功发布的 APK SHA256。"""
    latest_state = get_latest_build_state()
    if latest_state is None:
        return None, None
    upstream = latest_state["upstream"]
    return upstream["config_file"], upstream["sha256"]


def should_build(apk_sha256: str, base_sha256: str, latest_state: dict | None) -> bool:
    """仅在上游 APK 或基础资源配置有效内容变化时请求构建。"""
    if latest_state is None:
        return True
    upstream = latest_state["upstream"]
    return (
        apk_sha256.lower() != upstream["sha256"].lower()
        or base_sha256.lower() != latest_state["base_sha256"].lower()
    )


def get_next_module_version(latest_state: dict | None) -> int:
    """计算本次成功发布应使用的模块版本。"""
    return (latest_state or {"module_version": 0})["module_version"] + 1


def write_latest_config(
    module_version: int,
    base_sha256: str,
    sha256_str: str,
    apk_code: str,
    apk_name: str,
    release_date: str,
    config_path: Path,
):
    """原子写入最后一次成功发布的模块状态。"""
    config_relative_path = config_path.relative_to(CONFIG_DIR).as_posix()
    latest_config = {
        "state_version": 1,
        "module_version": module_version,
        "base_sha256": base_sha256,
        "upstream": {
            "version_name": apk_name,
            "version_code": apk_code,
            "sha256": sha256_str,
            "release_date": release_date,
            "config_file": config_relative_path,
        },
        "release": {
            "tag": format_module_version(module_version),
            "title": get_release_title(apk_name, module_version),
        },
    }
    temp_path = LATEST_CONFIG_PATH.with_name(f"{LATEST_CONFIG_PATH.name}.tmp")
    temp_path.write_text(
        json.dumps(latest_config, ensure_ascii=False, indent=4) + "\n", encoding="utf-8"
    )
    temp_path.replace(LATEST_CONFIG_PATH)

def write_update_json(module_version: int):
    """原子写入 KernelSU/Magisk 在线更新清单。"""
    version = format_module_version(module_version)
    payload = {
        "versionCode": module_version,
        "version": version,
        "zipUrl": f"https://github.com/{REPOSITORY_SLUG}/releases/download/{version}/{get_module_zip_filename(module_version)}",
        "changelog": f"https://github.com/{REPOSITORY_SLUG}/releases/tag/{version}",
    }
    temp_path = UPDATE_JSON_PATH.with_name(f"{UPDATE_JSON_PATH.name}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(UPDATE_JSON_PATH)


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

def download_and_decompile_apk() -> tuple[str, str, str, str, list[str]] | None:
    """下载并解包 APK"""
    web_version, release_date, changelog = fetch_changelog_info()

    print(f"[*] 正在下载最新官方 APK: {APK_URL}")
    req = urllib.request.Request(APK_URL, headers={'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36'})
    with urllib.request.urlopen(req) as resp, open(DOWNLOAD_APK_PATH, 'wb') as out_file:
        shutil.copyfileobj(resp, out_file)

    new_sha256 = calculate_sha256(DOWNLOAD_APK_PATH)
    print(f"[+] 下载完成，当前 APK SHA256: {new_sha256}")

    base_sha256 = get_base_sha256()
    latest_state = get_latest_build_state()
    if not should_build(new_sha256, base_sha256, latest_state):
        print("[=] 上游 APK 与 base.json 有效内容均未变化，无需构建。")
        return None

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

def iter_hld_root_smali_files():
    """仅返回各 Smali 分包中 hld 根目录的直接类文件。"""
    for smali_dir in DECOMPILE_DIR.glob("smali*"):
        hld_dir = smali_dir / HLD_PACKAGE_PATH
        if hld_dir.is_dir():
            yield from (path for path in hld_dir.glob("*.smali") if path.is_file())


def parse_hld_key_to_id() -> dict[str, str]:
    """从 hld 根目录的类字段中提取未混淆 key 到资源 ID 的映射。"""
    field_pattern = re.compile(
        r"\.field\s+.*?\s+([a-zA-Z0-9_$]+):I\s*=\s*(0x7f[0-9a-fA-F]{6})",
        re.IGNORECASE,
    )
    const_pattern = re.compile(r"const[/\w]*\s+v\d+,\s*(0x7f[0-9a-fA-F]{6})", re.IGNORECASE)
    sput_pattern = re.compile(r"sput[/\w]*\s+v\d+,\s*L[^;]+;->([a-zA-Z0-9_$]+):I")
    key_to_id: dict[str, str] = {}
    scanned_files = 0

    for smali_file in iter_hld_root_smali_files():
        scanned_files += 1
        content = smali_file.read_text(encoding="utf-8", errors="ignore")
        for match in field_pattern.finditer(content):
            key_to_id[match.group(1)] = match.group(2).lower()

        last_const_id = None
        for line in content.splitlines():
            const_match = const_pattern.search(line)
            if const_match:
                last_const_id = const_match.group(1).lower()
                continue
            sput_match = sput_pattern.search(line)
            if sput_match and last_const_id:
                key_to_id[sput_match.group(1)] = last_const_id
                last_const_id = None

    print(f"[+] hld 根目录扫描完成: {scanned_files} 个类文件，{len(key_to_id)} 组 Key -> ID 映射")
    return key_to_id


def parse_public_xml_mappings(resource_type: str) -> dict[str, str]:
    """返回指定资源类型的 ID 到混淆资源名映射。"""
    public_xml = DECOMPILE_DIR / "res" / "values" / "public.xml"
    if not public_xml.exists():
        raise FileNotFoundError(f"[!] 找不到 public.xml: {public_xml}")

    pattern = re.compile(
        rf'<public\s+type="{re.escape(resource_type)}"\s+name="([^"]+)"\s+id="(0x7f[0-9a-fA-F]{{6}})"'
    )
    id_to_name: dict[str, str] = {}
    for line in public_xml.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = pattern.search(line)
        if match:
            id_to_name[match.group(2).lower()] = match.group(1)
    return id_to_name


def resolve_theme_resources(
    items: list[dict], resource_type: str, key_to_id: dict[str, str], *, require_mapping: bool
) -> list[dict]:
    """使用 hld 字段 ID 和同类型 public.xml 生成资源映射结果。"""
    id_to_obfuscated = parse_public_xml_mappings(resource_type)
    resolved: list[dict] = []

    for item in items:
        raw_key = (item.get("key") or item.get("unobfuscated_key") or "").strip()
        if not raw_key:
            message = f"[!] {resource_type} 配置存在空 key"
            if require_mapping:
                raise ValueError(message)
            print(f"[!] 警告: {message}，已跳过")
            continue

        resource_id = key_to_id.get(raw_key)
        obfuscated_key = id_to_obfuscated.get(resource_id, "") if resource_id else ""
        if not obfuscated_key and resource_type == "color":
            print(f"[!] 警告: color '{raw_key}' 未在 hld 根目录映射到目标资源，保留未混淆名称")
            obfuscated_key = raw_key
        if not obfuscated_key:
            message = f"{resource_type} '{raw_key}' 未在 hld 根目录映射到目标资源"
            if require_mapping:
                raise RuntimeError(f"[!] {message}")
            print(f"[!] 警告: {message}，已跳过")
            continue

        resolved_item = {
            "unobfuscated_key": raw_key,
            "obfuscated_key": obfuscated_key,
            "description": item.get("description", ""),
        }
        if resource_type == "color":
            for field in ("light", "night"):
                if item.get(field):
                    resolved_item[field] = item[field]
        elif resource_type == "string":
            resolved_item["value"] = item.get("value", "")
        else:
            file_path = item.get("file_path", "")
            if not file_path:
                raise ValueError(f"[!] drawable '{raw_key}' 缺少 file_path")
            resolved_item["file_path"] = file_path
        resolved.append(resolved_item)

    print(f"[+] {resource_type} 映射完成: {len(resolved)}/{len(items)} 项")
    return resolved


def generate_version_config(
    sha256_str: str, apk_code: str, apk_name: str, release_date: str, changelog: list[str]
) -> Path:
    if not BASE_CONFIG_PATH.exists():
        raise FileNotFoundError(f"[!] 找不到基础配置文件: {BASE_CONFIG_PATH}")

    base_config = json.loads(BASE_CONFIG_PATH.read_text(encoding="utf-8"))
    key_to_id = parse_hld_key_to_id()
    updated_colors = resolve_theme_resources(
        base_config.get("theme_colors", []), "color", key_to_id, require_mapping=False
    )
    updated_strings = resolve_theme_resources(
        base_config.get("theme_strings", []), "string", key_to_id, require_mapping=False
    )
    updated_drawables = resolve_theme_resources(
        base_config.get("theme_drawables", []), "drawable", key_to_id, require_mapping=True
    )

    safe_name = re.sub(r'[\\/:*?"<>|\s]', "_", apk_name)
    safe_code = re.sub(r'[\\/:*?"<>|\s]', "_", apk_code)
    filename = f"{safe_name}({safe_code}).json" if safe_code else f"{safe_name}.json"
    payload = {
        "version_name": apk_name,
        "version_code": apk_code,
        "release_date": release_date,
        "sha256": sha256_str,
        "changelog": changelog,
        "theme_colors": updated_colors,
        "theme_strings": updated_strings,
        "theme_drawables": updated_drawables,
    }

    TARGET_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_path = TARGET_CONFIG_DIR / filename
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    print(f"[+] 版本 JSON 配置文件生成完毕: {config_path}")
    return config_path

def write_xml_resource_file(path: Path, entries: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<resources>",
        *entries,
        "</resources>",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def sync_src_resources(config_file: Path):
    """将版本配置转换为 colors、strings 和改名后的 Drawable 资源。"""
    config_data = json.loads(config_file.read_text(encoding="utf-8"))
    res_dir = OVERLAY_DIR / "res"
    day_entries: list[str] = []
    night_entries: list[str] = []
    string_entries: list[str] = []

    for item in config_data.get("theme_colors", []):
        target_key = item["obfuscated_key"]
        if light_color := item.get("light"):
            day_entries.append(f'    <color name="{target_key}">{escape(light_color)}</color>')
        if night_color := item.get("night"):
            night_entries.append(f'    <color name="{target_key}">{escape(night_color)}</color>')

    for item in config_data.get("theme_strings", []):
        target_key = item["obfuscated_key"]
        string_entries.append(f'    <string name="{target_key}">{escape(item.get("value", ""))}</string>')

    write_xml_resource_file(res_dir / "values" / "colors.xml", day_entries)
    write_xml_resource_file(res_dir / "values-night" / "colors.xml", night_entries)
    write_xml_resource_file(res_dir / "values" / "strings.xml", string_entries)

    drawable_dir = res_dir / "drawable"
    drawable_dir.mkdir(parents=True, exist_ok=True)
    for item in config_data.get("theme_drawables", []):
        raw_key = item["unobfuscated_key"]
        source_path = PROJECT_ROOT / item["file_path"]
        if not source_path.is_file():
            raise FileNotFoundError(f"[!] drawable '{raw_key}' 的源文件不存在: {source_path}")
        if not source_path.suffix:
            raise ValueError(f"[!] drawable '{raw_key}' 的源文件没有扩展名: {source_path}")
        destination = drawable_dir / (
            f"{item['obfuscated_key']}{source_path.suffix.lower()}"
        )
        shutil.copy2(source_path, destination)

    print(f"[+] Overlay 资源已同步至: {res_dir}")


def get_value_resource_dirs(*, night: bool) -> list[Path]:
    res_dir = DECOMPILE_DIR / "res"
    if not res_dir.is_dir():
        raise FileNotFoundError(f"[!] 找不到已解包资源目录: {res_dir}")
    dirs = []
    for path in res_dir.iterdir():
        if not path.is_dir() or not path.name.startswith("values"):
            continue
        is_night = path.name == "values-night" or path.name.startswith("values-night-")
        if is_night == night:
            dirs.append(path)
    return sorted(dirs)


def replace_named_value_resources(resource_type: str, replacements: dict[str, str], value_dirs: list[Path]) -> set[str]:
    found: set[str] = set()
    for value_dir in value_dirs:
        for xml_path in sorted(value_dir.rglob("*.xml")):
            if xml_path.name == "public.xml":
                continue
            try:
                tree = ET.parse(xml_path)
            except ET.ParseError as error:
                raise RuntimeError(f"[!] 无法解析资源 XML: {xml_path}") from error
            changed = False
            for element in tree.getroot():
                element_type = element.tag.rsplit("}", 1)[-1]
                is_resource = element_type == resource_type or (element_type == "item" and element.get("type") == resource_type)
                resource_name = element.get("name")
                if is_resource and resource_name in replacements:
                    element.text = replacements[resource_name]
                    found.add(resource_name)
                    changed = True
            if changed:
                tree.write(xml_path, encoding="utf-8", xml_declaration=True)
    return found


def replace_monet_drawables(items: list[dict]):
    res_dir = DECOMPILE_DIR / "res"
    for item in items:
        target_key = item["obfuscated_key"]
        source_path = PROJECT_ROOT / item["file_path"]
        if not source_path.is_file():
            raise FileNotFoundError(f"[!] Drawable 源文件不存在: {source_path}")
        candidates = []
        for drawable_dir in sorted(res_dir.glob("drawable*")):
            if drawable_dir.is_dir():
                candidates.extend(path for path in drawable_dir.glob(f"{target_key}.*") if path.is_file())
        if not candidates:
            raise FileNotFoundError(f"[!] 未在原始 APK 中找到 Drawable: {target_key}")
        for destination in candidates:
            if destination.suffix.lower() != source_path.suffix.lower():
                raise RuntimeError(f"[!] Drawable 格式不匹配: {target_key} ({destination.suffix} != {source_path.suffix})")
            shutil.copy2(source_path, destination)


def apply_monet_resources(config_file: Path):
    """将目标映射中的 Monet 资源直接写回已解包的微信输入法 APK。"""
    config_data = json.loads(config_file.read_text(encoding="utf-8"))
    day_colors = {item["obfuscated_key"]: item["light"] for item in config_data.get("theme_colors", []) if item.get("light")}
    night_colors = {item["obfuscated_key"]: item["night"] for item in config_data.get("theme_colors", []) if item.get("night")}
    strings = {item["obfuscated_key"]: item.get("value", "") for item in config_data.get("theme_strings", [])}
    day_found = replace_named_value_resources("color", day_colors, get_value_resource_dirs(night=False))
    missing_day = sorted(set(day_colors) - day_found)
    if missing_day:
        raise RuntimeError(f"[!] 未在原始 APK 中找到日间颜色资源: {', '.join(missing_day)}")
    night_found = replace_named_value_resources("color", night_colors, get_value_resource_dirs(night=True))
    missing_night = {key: night_colors[key] for key in night_colors if key not in night_found}
    if missing_night:
        entries = [f'    <color name="{name}">{escape(value)}</color>' for name, value in missing_night.items()]
        write_xml_resource_file(DECOMPILE_DIR / "res" / "values-night" / "wetype_monet.xml", entries)
    string_found = replace_named_value_resources("string", strings, get_value_resource_dirs(night=False))
    missing_strings = sorted(set(strings) - string_found)
    if missing_strings:
        raise RuntimeError(f"[!] 未在原始 APK 中找到字符串资源: {', '.join(missing_strings)}")
    replace_monet_drawables(config_data.get("theme_drawables", []))
    print("[+] Monet 资源已写回已解包的微信输入法 APK")


def find_keytool() -> str:
    configured = os.environ.get("KEYTOOL_PATH")
    if configured and Path(configured).is_file():
        return configured
    executable = "keytool.exe" if os.name == "nt" else "keytool"
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidate = Path(java_home) / "bin" / executable
        if candidate.is_file():
            return str(candidate)
    located = shutil.which(executable)
    if located:
        return located
    raise RuntimeError("未找到 keytool，请配置 JAVA_HOME 或 KEYTOOL_PATH")


def prepare_public_signing_keystore() -> Path:
    """将仓库中的公开 BKS 密钥临时转换为 apksigner 可直接使用的 PKCS#12。"""
    if not PUBLIC_SIGNING_BKS_PATH.is_file():
        raise FileNotFoundError(f"[!] 找不到公开签名密钥: {PUBLIC_SIGNING_BKS_PATH}")
    provider_path = os.environ.get("BCPROV_JAR_PATH")
    if not provider_path or not Path(provider_path).is_file():
        raise RuntimeError("未找到 Bouncy Castle Provider，请配置 BCPROV_JAR_PATH")
    keystore_path = OUT_DIR / "internal" / "monet-release.p12"
    keystore_path.parent.mkdir(parents=True, exist_ok=True)
    if keystore_path.exists():
        keystore_path.unlink()
    command = [find_keytool(), "-importkeystore", "-noprompt", "-srckeystore", str(PUBLIC_SIGNING_BKS_PATH), "-srcstoretype", "BKS", "-srcstorepass", PUBLIC_SIGNING_PASSWORD, "-srcalias", PUBLIC_SIGNING_ALIAS, "-destkeystore", str(keystore_path), "-deststoretype", "PKCS12", "-deststorepass", PUBLIC_SIGNING_PASSWORD, "-destkeypass", PUBLIC_SIGNING_PASSWORD, "-destalias", PUBLIC_SIGNING_ALIAS, "-providerclass", "org.bouncycastle.jce.provider.BouncyCastleProvider", "-providerpath", provider_path]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if result.returncode != 0:
        raise RuntimeError(f"公开 BKS 密钥转换失败:\n{result.stderr or result.stdout}")
    return keystore_path


def ensure_original_package_name():
    manifest_path = DECOMPILE_DIR / "AndroidManifest.xml"
    manifest = manifest_path.read_text(encoding="utf-8", errors="ignore")
    package_match = re.search(r'<manifest\b[^>]*\bpackage=["\']([^"\']+)["\']', manifest)
    if not package_match:
        raise RuntimeError(f"[!] 无法读取已解包 APK 的包名: {manifest_path}")
    if package_match.group(1) != "com.tencent.wetype":
        raise RuntimeError(f"[!] 原始包名异常: {package_match.group(1)}")


def build_monet_apk(config_file: Path, apk_name: str, apk_code: str, module_version: int) -> Path:
    """重建、发布签名并校验保持原包名的 Monet 微信输入法 APK。"""
    _, zipalign, apksigner, _ = find_sdk_tools()
    ensure_original_package_name()
    apply_monet_resources(config_file)
    unsigned_apk = OUT_DIR / "monet-unsigned.apk"
    aligned_apk = OUT_DIR / "monet-aligned.apk"
    final_apk = OUT_DIR / get_monet_apk_filename(apk_name, apk_code, module_version)
    for path in (unsigned_apk, aligned_apk, final_apk, Path(f"{final_apk}.idsig")):
        if path.exists():
            path.unlink()
    print("[*] 重建 Monet 微信输入法 APK (apktool)...")
    build_result = subprocess.run(["apktool", "b", str(DECOMPILE_DIR), "-o", str(unsigned_apk)], capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if build_result.returncode != 0:
        raise RuntimeError(f"Apktool 重建 Monet APK 失败:\n{build_result.stderr or build_result.stdout}")
    subprocess.run([str(zipalign), "-p", "-f", "4", str(unsigned_apk), str(aligned_apk)], check=True)
    keystore_path = prepare_public_signing_keystore()
    sign_command = [str(apksigner), "sign", "--ks", str(keystore_path), "--ks-type", "PKCS12", "--ks-pass", f"pass:{PUBLIC_SIGNING_PASSWORD}", "--key-pass", f"pass:{PUBLIC_SIGNING_PASSWORD}", "--ks-key-alias", PUBLIC_SIGNING_ALIAS, "--v1-signing-enabled", "true", "--v2-signing-enabled", "true", "--v3-signing-enabled", "true", "--v4-signing-enabled", "false", "--out", str(final_apk), str(aligned_apk)]
    subprocess.run(sign_command, check=True)
    subprocess.run([str(apksigner), "verify", "--verbose", "--print-certs", str(final_apk)], check=True)
    for path in (unsigned_apk, aligned_apk, Path(f"{final_apk}.idsig"), keystore_path):
        if path.exists():
            path.unlink()
    print(f"[+] Monet 微信输入法 APK 生成成功 -> {final_apk}")
    return final_apk


def build_overlay_apk():
    """编译并签名 Overlay APK"""
    aapt2, zipalign, apksigner, android_jar = find_sdk_tools()

    target_apk_dir = BUILD_TMP_DIR / "files"
    target_apk_dir.mkdir(parents=True, exist_ok=True)

    compiled_zip = OUT_DIR / "compiled.zip"
    unsigned_apk = OUT_DIR / "unsigned.apk"
    aligned_apk = OUT_DIR / "aligned.apk"
    final_apk = target_apk_dir / "WetypeMonet.apk"

    res_dir = OVERLAY_DIR / "res"
    manifest_xml = OVERLAY_DIR / "AndroidManifest.xml"

    print("[1/4] 编译资源 (aapt2 compile)...")
    res = subprocess.run(
        [str(aapt2), "compile", "--dir", str(res_dir), "-o", str(compiled_zip)],
        capture_output=True, text=True, encoding="utf-8", errors="ignore"
    )
    if res.returncode != 0:
        raise RuntimeError(f"aapt2 compile 失败:\n{res.stderr or res.stdout}")

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

    print("[3/4] 4 字节对齐 (zipalign)...")
    if aligned_apk.exists():
        aligned_apk.unlink()
    subprocess.run([str(zipalign), "-p", "-f", "4", str(unsigned_apk), str(aligned_apk)], check=True)

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

def prepare_template():
    """同步模块模板结构"""
    if BUILD_TMP_DIR.exists():
        shutil.rmtree(BUILD_TMP_DIR, ignore_errors=True)
    BUILD_TMP_DIR.mkdir(parents=True, exist_ok=True)

    if TEMPLATE_DIR.exists():
        shutil.copytree(TEMPLATE_DIR, BUILD_TMP_DIR, dirs_exist_ok=True)

def generate_module_prop(module_version: int) -> tuple[str, str]:
    """生成模块属性清单 module.prop。"""
    version_name = format_module_version(module_version)
    version_code = str(module_version)
    build_time = current_build_time()
    description = f"{MODULE_DESCRIPTION} [构建时间: {build_time}]"

    lines = [
        f"id={MODULE_ID}",
        f"name={MODULE_NAME}",
        f"version={version_name}",
        f"versionCode={version_code}",
        f"author={MODULE_AUTHOR}",
        f"description={description}",
        f"updateJson={UPDATE_JSON_URL}",
    ]
    (BUILD_TMP_DIR / "module.prop").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[+] module.prop 生成完成 (Version: {version_name}, Code: {version_code})")
    return version_name, version_code


def create_module_zip(module_version: int) -> Path:
    """打包输出 Magisk/KernelSU 模块 ZIP。"""
    print("[*] 打包 Magisk/KernelSU 刷机 ZIP 包...")
    zip_path = OUT_DIR / get_module_zip_filename(module_version)

    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in BUILD_TMP_DIR.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(BUILD_TMP_DIR)
                zf.write(file_path, arcname)

    print(f"[+] 刷机包构建成功: {zip_path}")
    return zip_path


def archive_official_apk(apk_name: str, apk_code: str) -> Path:
    """将本次成功适配的官方 APK 复制为可发布的归档产物。"""
    if not DOWNLOAD_APK_PATH.is_file():
        raise FileNotFoundError(f"官方 APK 不存在，无法归档: {DOWNLOAD_APK_PATH}")

    archive_path = OUT_DIR / get_official_apk_filename(apk_name, apk_code)
    shutil.copy2(DOWNLOAD_APK_PATH, archive_path)
    print(f"[+] 官方 APK 已归档: {archive_path}")
    return archive_path


def write_build_metadata(
    module_version: str,
    version_code: str,
    apk_name: str,
    apk_code: str,
    config_path: Path,
    zip_path: Path,
    official_apk_path: Path,
    monet_apk_path: Path,
):
    metadata = {
        "module_version": module_version,
        "version_code": version_code,
        "apk_name": apk_name,
        "apk_code": apk_code,
        "config_file": config_path.relative_to(CONFIG_DIR).as_posix(),
        "zip_file": zip_path.name,
        "official_apk_file": official_apk_path.name,
        "monet_apk_file": monet_apk_path.name,
        "release_tag": module_version,
        "release_title": get_release_title(apk_name, int(version_code)),
        "build_time": current_build_time(),
    }
    BUILD_METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    BUILD_METADATA_PATH.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=4) + "\n", encoding="utf-8"
    )

def main():
    print("======================================================")
    print("   微信输入法 Monet Overlay 自动化适配与构建工作流  ")
    print("======================================================\n")

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TARGET_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        print("[+] ===== 阶段 1: 检查更新 & 检索/解包 APK =====")
        build_input = download_and_decompile_apk()
        if build_input is None:
            return
        sha256_str, apk_code, apk_name, release_date, changelog = build_input

        print("\n[+] ===== 阶段 2: 解析 ID 映射 & 生成配置 =====")
        config_path = generate_version_config(sha256_str, apk_code, apk_name, release_date, changelog)

        print("\n[+] ===== 阶段 3: 生成 Overlay 资源并编译签名 APK =====")
        sync_src_resources(config_path)
        prepare_template()
        previous_state = get_latest_build_state()
        next_module_version = get_next_module_version(previous_state)
        module_version, version_code = generate_module_prop(next_module_version)
        build_overlay_apk()
        monet_apk_path = build_monet_apk(config_path, apk_name, apk_code, next_module_version)

        print("\n[+] ===== 阶段 4: 打包 Magisk/KernelSU 模块 ZIP =====")
        zip_path = create_module_zip(next_module_version)
        official_apk_path = archive_official_apk(apk_name, apk_code)
        write_build_metadata(
            module_version,
            version_code,
            apk_name,
            apk_code,
            config_path,
            zip_path,
            official_apk_path,
            monet_apk_path,
        )
        write_latest_config(
            next_module_version,
            get_base_sha256(),
            sha256_str,
            apk_code,
            apk_name,
            release_date,
            config_path,
        )
        write_update_json(next_module_version)

        print("\n[✓] 所有步骤全流程顺利执行完毕！")

    except Exception as e:
        print(f"\n[!] 工作流执行异常中断: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
