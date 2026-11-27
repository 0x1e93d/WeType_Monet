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
BETA_UPDATE_JSON_URL = f"https://raw.githubusercontent.com/{REPOSITORY_SLUG}/main/wetype_monet_beta.json"
KSU_CHANGELOG_URL = f"https://raw.githubusercontent.com/{REPOSITORY_SLUG}/main/CHANGELOG.md"
BETA_CHANGELOG_URL = f"https://raw.githubusercontent.com/{REPOSITORY_SLUG}/main/CHANGELOG_BETA.md"


def format_module_version(module_version: int) -> str:
    if isinstance(module_version, bool) or not isinstance(module_version, int) or module_version < 1:
        raise ValueError("[!] 模块版本必须为不小于 1 的整数")
    return f"v{module_version}"


def compose_version_code(module_version: int, build: str) -> int:
    """v 与 build 共同决定版本代码，两者任一变化都能触发更新。"""
    build_number = int(build) if str(build).isdigit() else 0
    return module_version * 100000 + build_number


def get_version_label(module_version: int, build: str, beta: bool) -> str:
    if beta:
        return f"{format_module_version(module_version)}-beta.{build}"
    return format_module_version(module_version)


def get_release_tag(module_version: int, build: str, beta: bool) -> str:
    return get_version_label(module_version, build, beta)


def get_update_json_url(beta: bool) -> str:
    return BETA_UPDATE_JSON_URL if beta else UPDATE_JSON_URL


def get_changelog_url(beta: bool) -> str:
    return BETA_CHANGELOG_URL if beta else KSU_CHANGELOG_URL


def _safe(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]', "_", value)


def get_module_zip_filename(apk_name: str, apk_code: str, build: str, module_version: int) -> str:
    return (
        f"{MODULE_ID}_{_safe(apk_name)}.{_safe(apk_code)}.{_safe(build)}"
        f"_{format_module_version(module_version)}.zip"
    )


def get_official_apk_filename(apk_name: str, apk_code: str, build: str) -> str:
    return f"Wetype_{_safe(apk_name)}.{_safe(apk_code)}.{_safe(build)}.apk"


def get_monet_apk_filename(apk_name: str, apk_code: str, build: str, module_version: int) -> str:
    return (
        f"Wetype_Monet_{_safe(apk_name)}.{_safe(apk_code)}.{_safe(build)}"
        f"_{format_module_version(module_version)}.apk"
    )


def get_release_title(apk_name: str, module_version: int, beta: bool = False) -> str:
    suffix = " Beta" if beta else ""
    return f"微信输入法 {apk_name}{suffix} · Monet {format_module_version(module_version)}"


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


def _build_key(value: str) -> int:
    return int(value) if str(value).isdigit() else 0


def should_build(
    build: str,
    latest_state: dict | None,
    *,
    force_build: bool = False,
) -> bool:
    """出现新的 build 号时构建。"""
    if force_build or latest_state is None:
        return True
    return _build_key(build) > _build_key(latest_state["upstream"].get("build", ""))


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
    apk_name: str,
    apk_code: str,
    build: str,
    download_url: str,
    release_date: str,
    config_path: Path,
    beta: bool = False,
    release_tag: str | None = None,
    next_module_version: int | None = None,
):
    """原子写入最后一次成功发布的模块状态。"""
    config_relative_path = config_path.relative_to(paths.config_dir).as_posix()
    release_version = next_module_version or module_version
    latest_config = {
        "state_version": 1,
        "module_version": module_version,
        # 沿用发布状态中的字段名，记录本次主题配置摘要。
        "base_sha256": base_sha256,
        "upstream": {
            "version_name": apk_name,
            "version_code": apk_code,
            "build": build,
            "sha256": sha256_str,
            "download_url": download_url,
            "release_date": release_date,
            "config_file": config_relative_path,
        },
        "release": {
            "tag": release_tag or format_module_version(module_version),
            "title": get_release_title(apk_name, release_version, beta),
            "channel": "beta" if beta else "official",
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
    build: str,
    changelog: list[str],
    changes: list[str] | None = None,
    *,
    include_official: bool = True,
):
    """写入 KernelSU 更新日志（精简版）。"""
    version = format_module_version(module_version)
    entries = _official_entries(changelog)
    lines = [f"## {version} · 微信输入法 {apk_name}.{apk_code}.{build}", "", "### 更新内容"]
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


def write_beta_changelog(
    paths: BuildPaths,
    module_version: int,
    apk_name: str,
    apk_code: str,
    build: str,
    changes: list[str] | None = None,
):
    """写入测试版更新日志。"""
    label = get_version_label(module_version, build, True)
    lines = [f"## {label} · 微信输入法 {apk_name}.{apk_code}.{build}", "", "### 更新内容"]
    if changes:
        lines.append("- 本次调整：")
        lines.extend(f"  - {change}" for change in changes)
    else:
        lines.append("- 跟随官方测试构建。")
    lines.extend(
        ["", "### 安装", "- 测试版模块，刷入后重启；正式版发布后可自动收到正式版更新。", ""]
    )
    _write_text(paths.beta_changelog_path, "\n".join(lines))


def write_release_notes(
    paths: BuildPaths,
    module_version: int,
    apk_name: str,
    apk_code: str,
    build: str,
    download_url: str,
    apk_sha256: str,
    changelog: list[str],
    config_path: Path,
    zip_path: Path,
    official_apk_path: Path,
    monet_apk_path: Path,
    changes: list[str] | None = None,
    *,
    include_official: bool = True,
    beta: bool = False,
    version_label: str | None = None,
    version_code_value: int | None = None,
):
    """写入 GitHub Release 说明（完整版）。"""
    version = format_module_version(module_version)
    display_label = version_label or version
    display_code = version_code_value if version_code_value is not None else module_version
    entries = _official_entries(changelog)
    config_relative_path = config_path.relative_to(paths.config_dir).as_posix()
    title_suffix = " Beta" if beta else ""
    lines = [f"## 微信输入法 Monet {version}{title_suffix}", ""]
    if beta:
        lines.extend(["> 测试版本，仅供体验，可能不稳定；稳定版请使用最新正式版。", ""])
    lines.extend(
        [
            f"适配微信输入法 **{apk_name}.{apk_code}.{build}**{'（Beta）' if beta else ''}。",
            "",
            "### 更新内容",
        ]
    )
    if include_official:
        if entries:
            lines.append("- 官方更新：")
            lines.extend(f"  - {entry}" for entry in entries)
        else:
            lines.append("- 官方更新：官方未提供该版本更新日志。")
    elif beta:
        lines.append("- 官方：该版本尚未发布正式版，无官方更新日志。")
    if changes:
        lines.append("- 本次调整：")
        lines.extend(f"  - {change}" for change in changes)
    install_suffix = "（预发布，不进入在线更新）" if beta else ""
    lines.extend(
        [
            "",
            "### 安装",
            f"- Magisk / KernelSU：刷入 `{zip_path.name}` 后重启；动态模块支持热更新。{install_suffix}",
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
            f"- 模块版本：{display_label}（版本代码 {display_code}）",
            f"- 构建时间：{current_build_time()}",
            f"- 官方 APK 下载：{download_url}",
            f"- 官方 APK SHA256：`{apk_sha256}`",
            f"- 资源映射：`config/{config_relative_path}`",
            "</details>",
            "",
        ]
    )
    _write_text(paths.release_notes_path, "\n".join(lines))


def write_update_json(
    paths: BuildPaths,
    module_version: int,
    apk_name: str,
    apk_code: str,
    build: str,
    *,
    beta: bool = False,
    release_tag: str | None = None,
    target_beta: bool | None = None,
):
    """写入在线更新清单；正式版发布时同时把测试通道指向正式版。"""
    version = get_version_label(module_version, build, beta)
    tag = release_tag or version
    zip_name = get_module_zip_filename(apk_name, apk_code, build, module_version)
    payload = {
        "versionCode": compose_version_code(module_version, build),
        "version": version,
        "zipUrl": f"https://github.com/{REPOSITORY_SLUG}/releases/download/{tag}/{zip_name}",
        "changelog": get_changelog_url(beta),
    }
    target_path = (
        paths.beta_update_json_path if (beta if target_beta is None else target_beta) else paths.update_json_path
    )
    temp_path = target_path.with_name(f"{target_path.name}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(target_path)


def generate_module_prop(
    paths: BuildPaths,
    module_version: int,
    *,
    version_label: str | None = None,
    version_code: int | None = None,
    update_json_url: str | None = None,
) -> tuple[str, str]:
    """生成模块属性清单 module.prop。"""
    version_name = version_label or format_module_version(module_version)
    code = version_code if version_code is not None else module_version
    description = MODULE_DESCRIPTION

    lines = [
        f"id={MODULE_ID}",
        f"name={MODULE_NAME}",
        f"version={version_name}",
        f"versionCode={code}",
        f"author={MODULE_AUTHOR}",
        f"description={description}",
        f"updateJson={update_json_url or UPDATE_JSON_URL}",
    ]
    (paths.module_dir / "module.prop").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[+] module.prop 生成完成 (Version: {version_name}, Code: {code})")
    return version_name, str(code)


def write_build_metadata(
    paths: BuildPaths,
    module_version: str,
    version_code: str,
    apk_name: str,
    apk_code: str,
    build: str,
    download_url: str,
    prerelease: bool,
    config_path: Path,
    zip_path: Path,
    official_apk_path: Path,
    monet_apk_path: Path,
    *,
    release_tag: str | None = None,
    release_version: int | None = None,
):
    match = re.match(r"v(\d+)", module_version)
    fallback_version = int(match.group(1)) if match else 0
    metadata = {
        "module_version": module_version,
        "version_code": version_code,
        "apk_name": apk_name,
        "apk_code": apk_code,
        "upstream_build": build,
        "download_url": download_url,
        "prerelease": "true" if prerelease else "false",
        "channel": "beta" if prerelease else "official",
        "config_file": config_path.relative_to(paths.config_dir).as_posix(),
        "zip_file": zip_path.name,
        "official_apk_file": official_apk_path.name,
        "monet_apk_file": monet_apk_path.name,
        "release_tag": release_tag or module_version,
        "release_title": get_release_title(
            apk_name, release_version or fallback_version, prerelease
        ),
        "build_time": current_build_time(),
    }
    paths.build_metadata_path.parent.mkdir(parents=True, exist_ok=True)
    paths.build_metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=4) + "\n", encoding="utf-8"
    )
