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
from zoneinfo import ZoneInfo

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
MODULE_CONFIG_PATH = CONFIG_DIR / "module.json"
TARGET_CONFIG_DIR = CONFIG_DIR / "targets"
LATEST_CONFIG_PATH = CONFIG_DIR / "latest.json"
DOWNLOAD_APK_PATH = OUT_DIR / "wetype_latest.apk"
HLD_PACKAGE_PATH = Path("com/tencent/wetype/plugin/hld")

APK_URL = "https://z.weixin.qq.com/android/download?channel=latest"
CHANGELOG_URL = "https://z.weixin.qq.com/web/changelog/android"

MODULE_ID = "Wetype_Monet"
MODULE_NAME = "微信输入法 Monet"
MODULE_AUTHOR = "酷安@1e93d"
MODULE_DESCRIPTION = "为微信输入法提供 Monet 动态色彩主题。"
UPDATE_JSON_URL = ""

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


def load_module_config() -> dict[str, str]:
    """读取模块展示版本与静态元数据。"""
    if not MODULE_CONFIG_PATH.exists():
        raise FileNotFoundError(f"[!] 找不到模块配置文件: {MODULE_CONFIG_PATH}")
    config = json.loads(MODULE_CONFIG_PATH.read_text(encoding="utf-8"))
    version = str(config.get("version", "")).strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("[!] module.json 的 version 必须为 MAJOR.MINOR.PATCH 格式")
    return config


def current_build_time() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M UTC+08:00")

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
    }
    temp_path = LATEST_CONFIG_PATH.with_name(f"{LATEST_CONFIG_PATH.name}.tmp")
    temp_path.write_text(
        json.dumps(latest_config, ensure_ascii=False, indent=4) + "\n", encoding="utf-8"
    )
    temp_path.replace(LATEST_CONFIG_PATH)

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

def generate_module_prop() -> tuple[str, str]:
    """生成模块属性清单 module.prop"""
    git_count, _ = get_git_info()
    module_config = load_module_config()
    version_name = f"v{module_config['version']}"
    version_code = os.environ.get("VERSION_CODE", git_count).strip() or git_count

    build_time = current_build_time()
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
    return version_name, version_code

def create_module_zip(version_name: str, apk_name: str, apk_code: str) -> Path:
    """打包输出 Magisk/KernelSU ZIP"""
    print("[*] 打包 Magisk/KernelSU 刷机 ZIP 包...")
    safe_apk_name = re.sub(r'[\\/:*?"<>|\s]', "_", apk_name)
    safe_apk_code = re.sub(r'[\\/:*?"<>|\s]', "_", apk_code)
    target_version = (
        f"wetype-{safe_apk_name}({safe_apk_code})"
        if safe_apk_code
        else f"wetype-{safe_apk_name}"
    )
    zip_filename = f"{MODULE_ID}_{version_name}_{target_version}.zip"
    zip_path = OUT_DIR / zip_filename

    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in BUILD_TMP_DIR.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(BUILD_TMP_DIR)
                zf.write(file_path, arcname)

    print(f"[+] 刷机包构建成功: {zip_path}")
    return zip_path


def write_build_metadata(
    module_version: str, version_code: str, apk_name: str, apk_code: str, config_path: Path, zip_path: Path
):
    metadata = {
        "module_version": module_version,
        "version_code": version_code,
        "apk_name": apk_name,
        "apk_code": apk_code,
        "config_file": config_path.name,
        "zip_file": zip_path.name,
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
        module_version, version_code = generate_module_prop()
        build_overlay_apk()

        print("\n[+] ===== 阶段 4: 打包 Magisk/KernelSU 模块 ZIP =====")
        zip_path = create_module_zip(module_version, apk_name, apk_code)
        write_build_metadata(module_version, version_code, apk_name, apk_code, config_path, zip_path)
        previous_state = get_latest_build_state()
        write_latest_config(
            get_next_module_version(previous_state),
            get_base_sha256(),
            sha256_str,
            apk_code,
            apk_name,
            release_date,
            config_path,
        )

        print("\n[✓] 所有步骤全流程顺利执行完毕！")

    except Exception as e:
        print(f"\n[!] 工作流执行异常中断: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
