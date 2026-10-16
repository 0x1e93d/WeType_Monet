import json
import unittest

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
                    "title": "微信输入法_1.0_v2",
                },
            },
        )
        self.assertEqual(release.get_latest_sha256(self.paths), ("mappings/1.0(1).json", "a" * 64))

    def test_should_skip_unchanged_apk(self):
        state = self._write_latest_state()

        self.assertFalse(release.should_build("a" * 64, state))

    def test_should_build_for_apk_changes(self):
        state = self._write_latest_state()

        self.assertTrue(release.should_build("c" * 64, state))

    def test_should_ignore_recorded_base_hash(self):
        state = self._write_latest_state(base_sha="d")

        self.assertFalse(release.should_build("a" * 64, state))

    def test_should_build_without_previous_state(self):
        self.assertTrue(release.should_build("a" * 64, None))

    def test_should_ignore_apk_hash_case(self):
        state = self._write_latest_state()

        self.assertFalse(release.should_build("A" * 64, state))

    def test_should_build_when_forced_even_without_input_changes(self):
        state = self._write_latest_state()

        self.assertTrue(release.should_build("a" * 64, state, force_build=True))

    def test_module_metadata_uses_latest_integer_version(self):
        self.paths.module_dir.mkdir(parents=True)

        self.assertEqual(release.generate_module_prop(self.paths, 2), ("v2", "2"))
        module_prop = (self.paths.module_dir / "module.prop").read_text(encoding="utf-8")
        self.assertIn("version=v2", module_prop)
        self.assertIn("versionCode=2", module_prop)
        self.assertIn(f"updateJson={release.UPDATE_JSON_URL}", module_prop)
        self.assertEqual(release.get_module_zip_filename(2), "Wetype_Monet_v2.zip")
        self.assertEqual(release.get_release_title("3.5.4", 2), "微信输入法_3.5.4_v2")

    def test_writes_kernelsu_changelog_and_update_manifest(self):
        release.write_update_changelog(
            self.paths,
            2,
            "3.5.2",
            "55201",
            "2026-07-22",
            "a" * 64,
            ["跨设备功能支持自定义设备名称", "体验优化与问题修复"],
        )
        release.write_update_json(self.paths, 2)

        changelog = self.paths.ksu_changelog_path.read_text(encoding="utf-8")
        self.assertIn("# WeType Monet", changelog)
        self.assertIn(f"**Version:** `v2`", changelog)
        self.assertIn(f"**WeType:** `3.5.2 (55201)`", changelog)
        self.assertIn("跨设备功能支持自定义设备名称", changelog)
        self.assertIn("体验优化与问题修复", changelog)
        self.assertIn("a" * 64, changelog)
        self.assertEqual(
            json.loads(self.paths.update_json_path.read_text(encoding="utf-8")),
            {
                "versionCode": 2,
                "version": "v2",
                "zipUrl": "https://github.com/0x1e93d/WeType_Monet/releases/download/v2/Wetype_Monet_v2.zip",
                "changelog": "https://raw.githubusercontent.com/0x1e93d/WeType_Monet/main/CHANGELOG.md",
            },
        )

    def test_next_module_version_skips_existing_tags(self):
        self.assertEqual(release.get_next_module_version({"module_version": 1}), 2)
        self.assertEqual(release.get_next_module_version({"module_version": 2}, {3, 4}), 5)


if __name__ == "__main__":
    unittest.main()
