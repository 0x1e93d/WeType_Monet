import hashlib
import json
import unittest
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from unittest.mock import patch

from scripts import resources, upstream
from tests.build_fixtures import BuildTestCase


class UpstreamTests(BuildTestCase):
    def test_download_skips_unchanged_apk_after_theme_changes(self):
        self._write_base_config()
        state = self._write_latest_state()
        apk_content = b"official-apk"
        state["upstream"]["sha256"] = hashlib.sha256(apk_content).hexdigest()
        previous_state = json.dumps(state)
        self.paths.latest_config_path.write_text(previous_state, encoding="utf-8")
        self.assertNotEqual(resources.get_base_sha256(self.paths), state["base_sha256"])

        with (
            patch.object(upstream, "fetch_changelog_info", return_value=("1.0", "2026-01-01", [])),
            patch.object(upstream.urllib.request, "urlopen", return_value=BytesIO(apk_content)),
            patch.object(upstream.subprocess, "run", side_effect=AssertionError("APK 未变化时不应调用解包工具")),
            redirect_stdout(StringIO()),
        ):
            result = upstream.download_and_decompile_apk(self.paths)

        self.assertIsNone(result)
        self.assertEqual(self.paths.download_apk_path.read_bytes(), apk_content)
        self.assertEqual(self.paths.latest_config_path.read_text(encoding="utf-8"), previous_state)

    def test_parses_official_changelog_content(self):
        html = """
        <div class="meta"><span class="label">发布日期:</span>2026-07-22</div>
        <div class="meta"><span class="label">发布版本:</span>3.5.2 for Android</div>
        <div class="content" data-nosnippet="true">
          <h2>- 跨设备功能支持自定义设备名称</h2>
          <h2>- 长按候选词，可将其固定至首位或删除</h2>
          <h2><span>- 体验优化与问题修复</span></h2>
        </div>
        """

        version, release_date, changelog = upstream.parse_official_changelog_html(html)

        self.assertEqual(version, "3.5.2")
        self.assertEqual(release_date, "2026-07-22")
        self.assertEqual(
            changelog,
            [
                "跨设备功能支持自定义设备名称",
                "长按候选词，可将其固定至首位或删除",
                "体验优化与问题修复",
            ],
        )

    def test_prefers_embedded_android_changelog_data(self):
        payload = {
            "appChangelog": [
                {
                    "id": 160,
                    "platform": 1,
                    "version": "9.9.9",
                    "release_date": 1784649600,
                    "content_html": "<h2>- iOS 更新</h2>",
                },
                {
                    "id": 159,
                    "platform": 2,
                    "version": "3.5.0",
                    "release_date": 1781611200,
                    "content_html": "<h2>- Android 旧版更新</h2>",
                },
                {
                    "id": 160,
                    "platform": 2,
                    "version": "3.5.2",
                    "release_date": 1784649600,
                    "content_html": "<h2>- 跨设备功能支持自定义设备名称</h2>",
                },
            ]
        }
        html = f"<script>window.injectData={json.dumps(payload, ensure_ascii=False)}</script>"

        version, release_date, changelog = upstream.parse_official_changelog_html(html)

        self.assertEqual(version, "3.5.2")
        self.assertEqual(release_date, "2026-07-22")
        self.assertEqual(changelog, ["跨设备功能支持自定义设备名称"])


if __name__ == "__main__":
    unittest.main()
