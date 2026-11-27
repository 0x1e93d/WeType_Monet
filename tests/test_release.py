import json
import subprocess
import unittest
from unittest.mock import patch

from scripts import release
from tests.build_fixtures import BuildTestCase


class ReleaseTests(BuildTestCase):
    def test_latest_hash_uses_explicit_state_file(self):
        self._write_latest_state(apk_name="2.0", apk_code="2", apk_sha="e")

        self.assertEqual(release.get_latest_sha256(self.paths), ("mappings/2.0(2).json", "e" * 64))

    def test_write_latest_config_records_successful_build(self):
        config_path = self.paths.mapping_config_dir / "1.0(1).json"
        config_path.write_text("{}", encoding="utf-8")

        release.write_latest_config(self.paths, 2, "b" * 64, "a" * 64, "1", "1.0", "2026-01-01", config_path)

        self.assertEqual(
            json.loads(self.paths.latest_config_path.read_text(encoding="utf-8")),
            {
                "state_version": 1,
                "module_version": 2,
                "base_sha256": "b" * 64,
                "upstream": {
                    "version_name": "1.0",
                    "version_code": "1",
                    "sha256": "a" * 64,
                    "release_date": "2026-01-01",
                    "config_file": "mappings/1.0(1).json",
                },
                "release": {
                    "tag": "v2",
                    "title": "微信输入法 1.0 · Monet v2",
                },
            },
        )
        self.assertEqual(release.get_latest_sha256(self.paths), ("mappings/1.0(1).json", "a" * 64))

    def test_should_skip_when_version_code_unchanged(self):
        state = self._write_latest_state(apk_name="4.0.1", apk_code="59201")

        self.assertFalse(release.should_build("59201", state))

    def test_should_build_when_version_code_increases(self):
        state = self._write_latest_state(apk_name="4.0.1", apk_code="59201")

        self.assertTrue(release.should_build("60201", state))

    def test_should_skip_when_version_code_decreases(self):
        state = self._write_latest_state(apk_name="4.0.2", apk_code="59201")

        self.assertFalse(release.should_build("58201", state))

    def test_should_build_without_previous_state(self):
        self.assertTrue(release.should_build("59201", None))

    def test_should_build_when_forced(self):
        state = self._write_latest_state(apk_name="4.0.1", apk_code="59201")

        self.assertTrue(release.should_build("59201", state, force_build=True))

    def test_module_metadata_uses_latest_integer_version(self):
        self.paths.module_dir.mkdir(parents=True)

        self.assertEqual(release.generate_module_prop(self.paths, 2), ("v2", "2"))
        module_prop = (self.paths.module_dir / "module.prop").read_text(encoding="utf-8")
        self.assertIn("version=v2", module_prop)
        self.assertIn("versionCode=2", module_prop)
        self.assertIn(f"updateJson={release.UPDATE_JSON_URL}", module_prop)
        self.assertNotIn("构建时间", module_prop)
        self.assertEqual(release.get_module_zip_filename(2), "Wetype_Monet_v2.zip")
        self.assertEqual(release.get_release_title("3.5.4", 2), "微信输入法 3.5.4 · Monet v2")

    def test_writes_kernelsu_changelog_and_update_manifest(self):
        release.write_update_changelog(
            self.paths,
            2,
            "3.5.2",
            "55201",
            ["跨设备功能支持自定义设备名称", "体验优化与问题修复"],
            ["fix: 调整候选栏配色"],
        )
        release.write_update_json(self.paths, 2)

        changelog = self.paths.ksu_changelog_path.read_text(encoding="utf-8")
        self.assertIn("## v2 · 微信输入法 3.5.2 (55201)", changelog)
        self.assertIn("- 官方更新：", changelog)
        self.assertIn("  - 跨设备功能支持自定义设备名称", changelog)
        self.assertIn("- 本次调整：", changelog)
        self.assertIn("  - fix: 调整候选栏配色", changelog)
        self.assertIn("### 安装", changelog)
        self.assertEqual(
            json.loads(self.paths.update_json_path.read_text(encoding="utf-8")),
            {
                "versionCode": 2,
                "version": "v2",
                "zipUrl": "https://github.com/0x1e93d/WeType_Monet/releases/download/v2/Wetype_Monet_v2.zip",
                "changelog": "https://raw.githubusercontent.com/0x1e93d/WeType_Monet/main/CHANGELOG.md",
            },
        )

    def test_writes_release_notes(self):
        config_path = self.paths.mapping_config_dir / "4.0.2(59201).json"
        config_path.write_text("{}", encoding="utf-8")
        zip_path = self.paths.out_dir / "Wetype_Monet_v26.zip"
        official_apk = self.paths.out_dir / "Wetype_4.0.2(59201).apk"
        monet_apk = self.paths.out_dir / "Wetype_Monet_4.0.2(59201)_v26.apk"
        for path in (zip_path, official_apk, monet_apk):
            path.write_bytes(b"apk")

        release.write_release_notes(
            self.paths,
            26,
            "4.0.2",
            "59201",
            "a" * 64,
            ["体验优化与问题修复"],
            config_path,
            zip_path,
            official_apk,
            monet_apk,
            ["fix: 调整候选栏配色"],
        )
        notes = self.paths.release_notes_path.read_text(encoding="utf-8")
        self.assertIn("## 微信输入法 Monet v26", notes)
        self.assertIn("适配微信输入法 **4.0.2 (59201)**。", notes)
        self.assertIn("  - 体验优化与问题修复", notes)
        self.assertIn("- 本次调整：", notes)
        self.assertIn("  - fix: 调整候选栏配色", notes)
        self.assertIn("### 安装", notes)
        self.assertIn("| `Wetype_Monet_v26.zip` | Magisk / KernelSU Overlay 模块 |", notes)
        self.assertIn("`config/mappings/4.0.2(59201).json`", notes)
        self.assertIn("<summary>构建信息</summary>", notes)

    def test_collects_git_changes_since_last_release(self):
        completed = subprocess.CompletedProcess(
            [],
            0,
            stdout="feat: 新增功能\nchore: 更新发布状态与资源映射\nfix: 修复问题\n",
            stderr="",
        )

        with patch.object(release.subprocess, "run", return_value=completed):
            self.assertEqual(release.get_git_changes("v25"), ["feat: 新增功能", "fix: 修复问题"])
        self.assertEqual(release.get_git_changes(None), [])

    def test_manual_changelog_without_official_section(self):
        release.write_update_changelog(
            self.paths,
            2,
            "3.5.2",
            "55201",
            ["官方更新日志"],
            ["fix: 修复问题"],
            include_official=False,
        )

        changelog = self.paths.ksu_changelog_path.read_text(encoding="utf-8")
        self.assertNotIn("官方更新", changelog)
        self.assertIn("- 本次调整：", changelog)
        self.assertIn("  - fix: 修复问题", changelog)

    def test_release_notes_without_official_section(self):
        config_path = self.paths.mapping_config_dir / "1.0(1).json"
        config_path.write_text("{}", encoding="utf-8")
        zip_path = self.paths.out_dir / "Wetype_Monet_v2.zip"
        official_apk = self.paths.out_dir / "Wetype_1.0(1).apk"
        monet_apk = self.paths.out_dir / "Wetype_Monet_1.0(1)_v2.apk"

        release.write_release_notes(
            self.paths,
            2,
            "1.0",
            "1",
            "a" * 64,
            ["官方更新日志"],
            config_path,
            zip_path,
            official_apk,
            monet_apk,
            ["fix: 修复问题"],
            include_official=False,
        )

        notes = self.paths.release_notes_path.read_text(encoding="utf-8")
        self.assertNotIn("官方更新", notes)
        self.assertIn("- 本次调整：", notes)
        self.assertIn("  - fix: 修复问题", notes)

    def test_release_notes_fallback_without_changelog(self):
        config_path = self.paths.mapping_config_dir / "1.0(1).json"
        config_path.write_text("{}", encoding="utf-8")
        zip_path = self.paths.out_dir / "Wetype_Monet_v2.zip"
        official_apk = self.paths.out_dir / "Wetype_1.0(1).apk"
        monet_apk = self.paths.out_dir / "Wetype_Monet_1.0(1)_v2.apk"

        release.write_release_notes(
            self.paths, 2, "1.0", "1", "a" * 64, [], config_path, zip_path, official_apk, monet_apk
        )

        self.assertIn("官方未提供该版本更新日志", self.paths.release_notes_path.read_text(encoding="utf-8"))

    def test_next_module_version_skips_existing_tags(self):
        self.assertEqual(release.get_next_module_version({"module_version": 1}), 2)
        self.assertEqual(release.get_next_module_version({"module_version": 2}, {3, 4}), 5)


if __name__ == "__main__":
    unittest.main()
