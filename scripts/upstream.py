"""获取官方安装包、版本信息与更新日志。"""

import hashlib
import json
import re
import shutil
import subprocess
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from scripts import release
from scripts.paths import BuildPaths

APK_URL = "https://z.weixin.qq.com/android/download?channel=latest"
CHANGELOG_URL = "https://z.weixin.qq.com/web/changelog/android"


def calculate_sha256(file_path: Path) -> str:
    """计算指定文件的 SHA256"""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as source_file:
        while chunk := source_file.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()


class OfficialChangelogParser(HTMLParser):
    """解析官方更新日志中的版本、日期和条目。"""

    _METADATA_LABELS = {"发布版本:": "version", "发布日期:": "date"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._tag_stack: list[str] = []
        self._content_depth: int | None = None
        self._heading_depth: int | None = None
        self._heading_parts: list[str] = []
        self._pending_metadata: str | None = None
        self.version_text = ""
        self.release_date = ""
        self.changelog: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        self._tag_stack.append(tag)
        attributes = dict(attrs)
        if tag == "div" and self._content_depth is None:
            classes = (attributes.get("class") or "").split()
            if "content" in classes:
                self._content_depth = len(self._tag_stack)
        if self._content_depth is not None and tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_depth = len(self._tag_stack)
            self._heading_parts = []

    def handle_endtag(self, tag: str):
        depth = len(self._tag_stack)
        if self._heading_depth == depth and tag == self._tag_stack[-1]:
            entry = re.sub(r"\s+", " ", "".join(self._heading_parts)).strip()
            entry = re.sub(r"^[\-–—•*]\s*", "", entry).strip()
            if entry:
                self.changelog.append(entry)
            self._heading_depth = None
            self._heading_parts = []
        if self._content_depth == depth and tag == "div":
            self._content_depth = None
        if self._tag_stack:
            self._tag_stack.pop()

    def handle_data(self, data: str):
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        if self._heading_depth is not None:
            self._heading_parts.append(text)
        if text in self._METADATA_LABELS:
            self._pending_metadata = self._METADATA_LABELS[text]
            return
        if self._pending_metadata == "version":
            self.version_text = text
            self._pending_metadata = None
        elif self._pending_metadata == "date":
            self.release_date = text
            self._pending_metadata = None


def parse_changelog_entries(content_html: str) -> list[str]:
    """解析官方更新日志条目。"""
    parser = OfficialChangelogParser()
    parser.feed(f'<div class="content">{content_html}</div>')
    parser.close()
    return parser.changelog


def parse_injected_changelog_data(html: str) -> tuple[str, str, list[str]]:
    """读取官网内嵌的 Android 更新日志。"""
    marker = "window.injectData="
    payload_start = html.find(marker)
    if payload_start < 0:
        return "", "", []

    try:
        payload, _ = json.JSONDecoder().raw_decode(html[payload_start + len(marker):])
        entries = [
            entry for entry in payload.get("appChangelog", [])
            if isinstance(entry, dict) and entry.get("platform") == 2
        ]
        latest_entry = max(
            entries,
            key=lambda entry: (int(entry.get("release_date", 0)), int(entry.get("id", 0))),
        )
        release_timestamp = int(latest_entry["release_date"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return "", "", []

    version_match = re.search(r"\d+(?:\.\d+)+", str(latest_entry.get("version", "")))
    if not version_match:
        return "", "", []

    try:
        release_date = datetime.fromtimestamp(
            release_timestamp, tz=release.china_timezone()
        ).date().isoformat()
    except (OSError, OverflowError, ValueError):
        return "", "", []

    content_html = latest_entry.get("content_html")
    changelog = parse_changelog_entries(content_html) if isinstance(content_html, str) else []
    return version_match.group(0), release_date, changelog


def parse_official_changelog_html(html: str) -> tuple[str, str, list[str]]:
    """解析官方更新日志页面。"""
    version, release_date, changelog = parse_injected_changelog_data(html)
    if version and release_date and changelog:
        return version, release_date, changelog

    # 官网内嵌数据不可用时解析页面内容。
    parser = OfficialChangelogParser()
    parser.feed(html)
    parser.close()
    version_match = re.search(r"\d+(?:\.\d+)+", parser.version_text)
    release_date_match = re.search(r"\d{4}-\d{2}-\d{2}", parser.release_date)
    return (
        version_match.group(0) if version_match else "",
        release_date_match.group(0) if release_date_match else "",
        parser.changelog,
    )


def fetch_changelog_info() -> tuple[str, str, list[str]]:
    """获取最新的 Android 更新日志。"""
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    request = urllib.request.Request(CHANGELOG_URL, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            html = response.read().decode("utf-8")
        web_version, release_date, changelog = parse_official_changelog_html(html)
        print(f"[+] 官网版本: '{web_version}' | 日期: '{release_date}' | 日志: {len(changelog)} 条")
        return web_version, release_date, changelog
    except Exception as error:
        print(f"[!] 抓取官网日志失败: {error}")
        return "", "", []


def download_and_decompile_apk(
    paths: BuildPaths,
    *, force_build: bool = False
) -> tuple[str, str, str, str, list[str]] | None:
    """下载并解包 APK"""
    web_version, release_date, changelog = fetch_changelog_info()

    print(f"[*] 正在下载最新官方 APK: {APK_URL}")
    paths.download_apk_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(APK_URL, headers={'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36'})
    with urllib.request.urlopen(req) as resp, open(paths.download_apk_path, 'wb') as out_file:
        shutil.copyfileobj(resp, out_file)

    new_sha256 = calculate_sha256(paths.download_apk_path)
    print(f"[+] 下载完成，当前 APK SHA256: {new_sha256}")

    latest_state = release.get_latest_build_state(paths)
    # 主题资源变更通过手动更新发布。
    if not release.should_build(new_sha256, latest_state, force_build=force_build):
        print("[=] 官方 APK 未更新，跳过构建。")
        return None

    print(f"[*] 开始使用 Apktool 解包 APK -> {paths.decompile_dir}")
    if paths.decompile_dir.exists():
        shutil.rmtree(paths.decompile_dir)

    res = subprocess.run(
        ["apktool", "d", str(paths.download_apk_path), "-o", str(paths.decompile_dir), "-f"],
        capture_output=True, text=True
    )
    if res.returncode != 0:
        raise RuntimeError(f"Apktool 解包失败:\n{res.stderr}")
    print("[+] Apktool 解包完成")

    yml_path = paths.decompile_dir / "apktool.yml"
    apk_code, apk_name = "", ""
    if yml_path.exists():
        with open(yml_path, "r", encoding="utf-8") as source_file:
            for line in source_file:
                if "versionCode:" in line:
                    apk_code = line.split("versionCode:")[1].strip(" '\"\r\n")
                elif "versionName:" in line:
                    apk_name = line.split("versionName:")[1].strip(" '\"\r\n")

    final_apk_name = apk_name or web_version or "unknown_version"
    final_changelog = changelog if (not web_version or web_version == final_apk_name) else []

    return new_sha256, apk_code, final_apk_name, release_date, final_changelog
