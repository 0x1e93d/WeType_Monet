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
    return f"微信输入法 {apk_name} · Monet {format_module_version(module_version)}"


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


def _version_code_key(value: str) -> int:
    return int(value) if value.isdigit() else 0


def should_build(
    version_code: str,
    latest_state: dict | None,
    *,
    force_build: bool = False,
) -> bool:
    """versionCode 增大时构建。"""
    if force_build or latest_state is None:
        return True
    return _version_code_key(version_code) > _version_code_key(latest_state["upstream"]["version_code"])


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


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


def _official_entries(changelog: list[str]) -> list[str]:
    return [re.sub(r"\s+", " ", str(item)).strip() for item in changelog if str(item).strip()]


def get_git_changes(since_tag: str | None) -> list[str]:
    """读取上次发布以来的提交标题。"""
    if not since_tag:
        return []
    try:
        result = subprocess.run(
            ["git", "log", f"{since_tag}..HEAD", "--no-merges", "--pretty=format:%s"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    return [
        line.strip()
        for line in (result.stdout or "").splitlines()
        if line.strip() and not line.strip().startswith("chore: 更新发布状态与资源映射")
    ]


def write_update_changelog(
    paths: BuildPaths,
    module_version: int,
    apk_name: str,
    apk_code: str,
    changelog: list[str],
    changes: list[str] | None = None,
    *,
    include_official: bool = True,
):
    """写入 KernelSU 更新日志（精简版）。"""
    version = format_module_version(module_version)
    entries = _official_entries(changelog)
    lines = [f"## {version} · 微信输入法 {apk_name} ({apk_code})", "", "### 更新内容"]
    if include_official:
        if entries:
            lines.append("- 官方更新：")
            lines.extend(f"  - {entry}" for entry in entries)
        else:
            lines.append("- 官方更新：官方未提供该版本更新日志。")
    if changes:
        lines.append("- 本次调整：")
        lines.extend(f"  - {change}" for change in changes)
    lines.extend(["", "### 安装", "- 模块直接刷入后重启；独立 APK 首次安装需卸载官方版。", ""])
    _write_text(paths.ksu_changelog_path, "\n".join(lines))


def write_release_notes(
    paths: BuildPaths,
    module_version: int,
    apk_name: str,
    apk_code: str,
    apk_sha256: str,
    changelog: list[str],
    config_path: Path,
    zip_path: Path,
    official_apk_path: Path,
    monet_apk_path: Path,
    changes: list[str] | None = None,
    *,
    include_official: bool = True,
):
    """写入 GitHub Release 说明（完整版）。"""
    version = format_module_version(module_version)
    entries = _official_entries(changelog)
    config_relative_path = config_path.relative_to(paths.config_dir).as_posix()
    lines = [
        f"## 微信输入法 Monet {version}",
        "",
        f"适配微信输入法 **{apk_name} ({apk_code})**。",
        "",
        "### 更新内容",
    ]
    if include_official:
        if entries:
            lines.append("- 官方更新：")
            lines.extend(f"  - {entry}" for entry in entries)
        else:
            lines.append("- 官方更新：官方未提供该版本更新日志。")
    if changes:
        lines.append("- 本次调整：")
        lines.extend(f"  - {change}" for change in changes)
    lines.extend(
        [
            "",
            "### 安装",
            f"- Magisk / KernelSU：刷入 `{zip_path.name}` 后重启；动态模块支持热更新。",
            f"- 独立 APK：`{monet_apk_path.name}`；首次从官方版切换需备份数据并卸载（签名不同）。",
            "",
            "### 文件说明",
            "| 文件 | 用途 |",
            "| --- | --- |",
            f"| `{zip_path.name}` | Magisk / KernelSU Overlay 模块 |",
            f"| `{monet_apk_path.name}` | 独立安装包（含 Monet 资源） |",
            f"| `{official_apk_path.name}` | 官方原始 APK |",
            "",
            "<details>",
            "<summary>构建信息</summary>",
            "",
            f"- 模块版本：{version}（版本代码 {module_version}）",
            f"- 构建时间：{current_build_time()}",
            f"- 官方 APK SHA256：`{apk_sha256}`",
            f"- 资源映射：`config/{config_relative_path}`",
            "</details>",
            "",
        ]
    )
    _write_text(paths.release_notes_path, "\n".join(lines))


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
    description = MODULE_DESCRIPTION

    lines = [
        f"id={MODULE_ID}",
        f"name={MODULE_NAME}",
        f"version={version_name}",
        f"versionCode={version_code}",
        f"author={MODULE_AUTHOR}",
        f"description={description}",
        f"updateJson={UPDATE_JSON_URL}",
    ]
    (paths.module_dir / "module.prop").write_text("\n".join(lines) + "\n", encoding="utf-8")
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
