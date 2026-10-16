"""解析资源映射并生成、替换主题资源。"""

import hashlib
import json
import re
import shutil
from pathlib import Path
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET

from scripts.paths import BuildPaths

HLD_PACKAGE_PATH = Path("com/tencent/wetype/plugin/hld")


def calculate_canonical_json_sha256(file_path: Path) -> str:
    """计算 JSON 有效内容的稳定 SHA256，忽略格式差异。"""
    data = json.loads(file_path.read_text(encoding="utf-8"))
    canonical_json = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical_json).hexdigest()


def get_base_sha256(paths: BuildPaths) -> str:
    """读取基础资源配置的有效内容 SHA256。"""
    if not paths.base_config_path.is_file():
        raise FileNotFoundError(f"[!] 找不到基础配置文件: {paths.base_config_path}")
    return calculate_canonical_json_sha256(paths.base_config_path)


def iter_hld_root_smali_files(paths: BuildPaths):
    """仅返回各 Smali 分包中 hld 根目录的直接类文件。"""
    for smali_dir in paths.decompile_dir.glob("smali*"):
        hld_dir = smali_dir / HLD_PACKAGE_PATH
        if hld_dir.is_dir():
            yield from (path for path in hld_dir.glob("*.smali") if path.is_file())


def parse_hld_key_to_id(paths: BuildPaths) -> dict[str, str]:
    """从 hld 根目录的类字段中提取未混淆 key 到资源 ID 的映射。"""
    field_pattern = re.compile(
        r"\.field\s+.*?\s+([a-zA-Z0-9_$]+):I\s*=\s*(0x7f[0-9a-fA-F]{6})",
        re.IGNORECASE,
    )
    const_pattern = re.compile(r"const[/\w]*\s+v\d+,\s*(0x7f[0-9a-fA-F]{6})", re.IGNORECASE)
    sput_pattern = re.compile(r"sput[/\w]*\s+v\d+,\s*L[^;]+;->([a-zA-Z0-9_$]+):I")
    key_to_id: dict[str, str] = {}
    scanned_files = 0

    for smali_file in iter_hld_root_smali_files(paths):
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


def parse_public_xml_mappings(paths: BuildPaths, resource_type: str) -> dict[str, str]:
    """返回指定资源类型的 ID 到混淆资源名映射。"""
    public_xml = paths.decompile_dir / "res" / "values" / "public.xml"
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
    paths: BuildPaths,
    items: list[dict], resource_type: str, key_to_id: dict[str, str], *, require_mapping: bool
) -> list[dict]:
    """使用 hld 字段 ID 和同类型 public.xml 生成资源映射结果。"""
    id_to_obfuscated = parse_public_xml_mappings(paths, resource_type)
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
    paths: BuildPaths,
    sha256_str: str, apk_code: str, apk_name: str, release_date: str, changelog: list[str]
) -> Path:
    if not paths.base_config_path.exists():
        raise FileNotFoundError(f"[!] 找不到基础配置文件: {paths.base_config_path}")

    base_config = json.loads(paths.base_config_path.read_text(encoding="utf-8"))
    key_to_id = parse_hld_key_to_id(paths)
    updated_colors = resolve_theme_resources(
        paths,
        base_config.get("theme_colors", []), "color", key_to_id, require_mapping=False
    )
    updated_strings = resolve_theme_resources(
        paths,
        base_config.get("theme_strings", []), "string", key_to_id, require_mapping=False
    )
    updated_drawables = resolve_theme_resources(
        paths,
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

    paths.target_config_dir.mkdir(parents=True, exist_ok=True)
    config_path = paths.target_config_dir / filename
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


def sync_src_resources(paths: BuildPaths, config_file: Path):
    """将版本配置转换为 colors、strings 和改名后的 Drawable 资源。"""
    config_data = json.loads(config_file.read_text(encoding="utf-8"))
    res_dir = paths.overlay_dir / "res"
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
        source_path = paths.project_root / item["file_path"]
        if not source_path.is_file():
            raise FileNotFoundError(f"[!] drawable '{raw_key}' 的源文件不存在: {source_path}")
        if not source_path.suffix:
            raise ValueError(f"[!] drawable '{raw_key}' 的源文件没有扩展名: {source_path}")
        destination = drawable_dir / (
            f"{item['obfuscated_key']}{source_path.suffix.lower()}"
        )
        shutil.copy2(source_path, destination)

    print(f"[+] Overlay 资源已同步至: {res_dir}")


def get_value_resource_dirs(paths: BuildPaths, *, night: bool) -> list[Path]:
    res_dir = paths.decompile_dir / "res"
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


def replace_monet_drawables(paths: BuildPaths, items: list[dict]):
    res_dir = paths.decompile_dir / "res"
    for item in items:
        target_key = item["obfuscated_key"]
        source_path = paths.project_root / item["file_path"]
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


def apply_monet_resources(paths: BuildPaths, config_file: Path):
    """将目标映射中的 Monet 资源直接写回已解包的微信输入法 APK。"""
    config_data = json.loads(config_file.read_text(encoding="utf-8"))
    day_colors = {item["obfuscated_key"]: item["light"] for item in config_data.get("theme_colors", []) if item.get("light")}
    night_colors = {item["obfuscated_key"]: item["night"] for item in config_data.get("theme_colors", []) if item.get("night")}
    strings = {item["obfuscated_key"]: item.get("value", "") for item in config_data.get("theme_strings", [])}
    day_found = replace_named_value_resources("color", day_colors, get_value_resource_dirs(paths, night=False))
    missing_day = sorted(set(day_colors) - day_found)
    if missing_day:
        raise RuntimeError(f"[!] 未在原始 APK 中找到日间颜色资源: {', '.join(missing_day)}")
    night_found = replace_named_value_resources("color", night_colors, get_value_resource_dirs(paths, night=True))
    missing_night = {key: night_colors[key] for key in night_colors if key not in night_found}
    if missing_night:
        entries = [f'    <color name="{name}">{escape(value)}</color>' for name, value in missing_night.items()]
        write_xml_resource_file(paths.decompile_dir / "res" / "values-night" / "wetype_monet.xml", entries)
    string_found = replace_named_value_resources("string", strings, get_value_resource_dirs(paths, night=False))
    missing_strings = sorted(set(strings) - string_found)
    if missing_strings:
        raise RuntimeError(f"[!] 未在原始 APK 中找到字符串资源: {', '.join(missing_strings)}")
    replace_monet_drawables(paths, config_data.get("theme_drawables", []))
    print("[+] Monet 资源已写回已解包的微信输入法 APK")
