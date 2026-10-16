"""管理模块版本、发布状态与更新清单。"""

import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from scripts.paths import BuildPaths

MODULE_ID = "Wetype_Monet"
MODULE_NAME = "微信输入法 Monet"
MODULE_AUTHOR = "酷安@1e93d"
MODULE_DESCRIPTION = "为微信输入法提供 Monet 动态色彩主题。"
REPOSITORY_SLUG = os.environ.get("GITHUB_REPOSITORY", "0x1e93d/WeType_Monet")
UPDATE_JSON_URL = f"https://raw.githubusercontent.com/{REPOSITORY_SLUG}/main/wetype_monet.json"
KSU_CHANGELOG_URL = f"https://raw.githubusercontent.com/{REPOSITORY_SLUG}/main/CHANGELOG.md"


def format_module_version(module_version: int) -> str:
    if isinstance(module_version, bool) or not isinstance(module_version, int) or module_version < 1:
        raise ValueError("[!] 模块版本必须为不小于 1 的整数")
    return f"v{module_version}"


def get_module_zip_filename(module_version: int) -> str:
    return f"{MODULE_ID}_{format_module_version(module_version)}.zip"


def get_official_apk_filename(apk_name: str, apk_code: str) -> str:
    safe_name = re.sub(r'[\\/:*?"<>|\s]', "_", apk_name)
    safe_code = re.sub(r'[\\/:*?"<>|\s]', "_", apk_code)
    return f"Wetype_{safe_name}({safe_code}).apk"


def get_monet_apk_filename(apk_name: str, apk_code: str, module_version: int) -> str:
    safe_name = re.sub(r'[\\/:*?"<>|\s]', "_", apk_name)
    safe_code = re.sub(r'[\\/:*?"<>|\s]', "_", apk_code)
    return f"Wetype_Monet_{safe_name}({safe_code})_{format_module_version(module_version)}.apk"


def get_release_title(apk_name: str, module_version: int) -> str:
    return f"微信输入法_{apk_name}_{format_module_version(module_version)}"


def china_timezone():
    """获取北京时间，兼容未安装 tzdata 的环境。"""
    try:
        return ZoneInfo("Asia/Shanghai")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=8))


def current_build_time() -> str:
    return datetime.now(china_timezone()).strftime("%Y-%m-%d %H:%M UTC+08:00")


def is_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None


def get_latest_build_state(paths: BuildPaths) -> dict | None:
    """读取最后一次成功发布的模块状态。"""
    if not paths.latest_config_path.is_file():
        return None

    try:
        state = json.loads(paths.latest_config_path.read_text(encoding="utf-8"))
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
            or config_relative_path.parts[0] != paths.mapping_config_dir.name
            or config_relative_path.suffix != ".json"
        ):
            return None
        if not (paths.config_dir / config_relative_path).is_file():
            return None
        return state
    except (OSError, json.JSONDecodeError):
        return None


def get_latest_sha256(paths: BuildPaths) -> tuple[str | None, str | None]:
    """读取最后一次成功发布的 APK SHA256。"""
    latest_state = get_latest_build_state(paths)
    if latest_state is None:
        return None, None
    upstream = latest_state["upstream"]
    return upstream["config_file"], upstream["sha256"]


def should_build(
    apk_sha256: str,
    latest_state: dict | None,
    *,
    force_build: bool = False,
) -> bool:
    """根据上游 APK 和强制构建选项判断是否构建。"""
    if force_build:
        return True
    if latest_state is None:
        return True
    upstream = latest_state["upstream"]
    return apk_sha256.lower() != upstream["sha256"].lower()


def get_existing_module_versions() -> set[int]:
    """读取仓库中已经占用的 vN 标签，避免重复创建 Release。"""
    try:
        result = subprocess.run(
            ["git", "tag", "--list", "v[0-9]*"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return set()
    versions: set[int] = set()
    for tag in result.stdout.splitlines():
        match = re.fullmatch(r"v([1-9][0-9]*)", tag.strip())
        if match:
            versions.add(int(match.group(1)))
    return versions


def get_next_module_version(
    latest_state: dict | None,
    reserved_versions: set[int] | None = None,
) -> int:
    """计算本次成功发布应使用的模块版本，并跳过已存在的版本标签。"""
    next_version = (latest_state or {"module_version": 0})["module_version"] + 1
    reserved_versions = reserved_versions or set()
    while next_version in reserved_versions:
        next_version += 1
    return next_version


def write_latest_config(
    paths: BuildPaths,
    module_version: int,
    base_sha256: str,
    sha256_str: str,
    apk_code: str,
    apk_name: str,
    release_date: str,
    config_path: Path,
):
    """原子写入最后一次成功发布的模块状态。"""
    config_relative_path = config_path.relative_to(paths.config_dir).as_posix()
    latest_config = {
        "state_version": 1,
        "module_version": module_version,
        # 沿用发布状态中的字段名，记录本次主题配置摘要。
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
    temp_path = paths.latest_config_path.with_name(f"{paths.latest_config_path.name}.tmp")
    temp_path.write_text(
        json.dumps(latest_config, ensure_ascii=False, indent=4) + "\n", encoding="utf-8"
    )
    temp_path.replace(paths.latest_config_path)


def write_update_changelog(
    paths: BuildPaths,
    module_version: int,
    apk_name: str,
    apk_code: str,
    release_date: str,
    apk_sha256: str,
    changelog: list[str],
):
    """写入 KernelSU 更新日志。"""
    version = format_module_version(module_version)
    entries = [
        re.sub(r"\s+", " ", str(entry)).strip()
        for entry in changelog
        if str(entry).strip()
    ]
    lines = [
        "# WeType Monet",
        "",
        f"- **Version:** `{version}`",
        f"- **VersionCode:** `{module_version}`",
        f"- **WeType:** `{apk_name} ({apk_code})`",
        f"- **Official release date:** `{release_date or 'Unknown'}`",
        f"- **Build time:** `{current_build_time()}`",
        f"- **SHA256:** `{apk_sha256}`",
        "",
        "## Official Changelog",
        "",
    ]
    lines.extend(f"- {entry}" for entry in entries)
    if not entries:
        lines.append("- No matching official changelog is available.")

    temp_path = paths.ksu_changelog_path.with_name(f"{paths.ksu_changelog_path.name}.tmp")
    temp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temp_path.replace(paths.ksu_changelog_path)


def write_update_json(paths: BuildPaths, module_version: int):
    """写入在线更新清单。"""
    version = format_module_version(module_version)
    payload = {
        "versionCode": module_version,
        "version": version,
        "zipUrl": f"https://github.com/{REPOSITORY_SLUG}/releases/download/{version}/{get_module_zip_filename(module_version)}",
        "changelog": KSU_CHANGELOG_URL,
    }
    temp_path = paths.update_json_path.with_name(f"{paths.update_json_path.name}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(paths.update_json_path)


def generate_module_prop(paths: BuildPaths, module_version: int) -> tuple[str, str]:
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
    (paths.build_tmp_dir / "module.prop").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[+] module.prop 生成完成 (Version: {version_name}, Code: {version_code})")
    return version_name, version_code


def write_build_metadata(
    paths: BuildPaths,
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
        "config_file": config_path.relative_to(paths.config_dir).as_posix(),
        "zip_file": zip_path.name,
        "official_apk_file": official_apk_path.name,
        "monet_apk_file": monet_apk_path.name,
        "release_tag": module_version,
        "release_title": get_release_title(apk_name, int(version_code)),
        "build_time": current_build_time(),
    }
    paths.build_metadata_path.parent.mkdir(parents=True, exist_ok=True)
    paths.build_metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=4) + "\n", encoding="utf-8"
    )
