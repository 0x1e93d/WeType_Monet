import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build.py"
SPEC = importlib.util.spec_from_file_location("wetype_build", SCRIPT_PATH)
build = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(build)


class BuildMappingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.original_paths = {
            "PROJECT_ROOT": build.PROJECT_ROOT,
            "CONFIG_DIR": build.CONFIG_DIR,
            "OUT_DIR": build.OUT_DIR,
            "BUILD_TMP_DIR": build.BUILD_TMP_DIR,
            "BUILD_METADATA_PATH": build.BUILD_METADATA_PATH,
            "UPDATE_JSON_PATH": build.UPDATE_JSON_PATH,
            "KSU_CHANGELOG_PATH": build.KSU_CHANGELOG_PATH,
            "OVERLAY_DIR": build.OVERLAY_DIR,
            "DECOMPILE_DIR": build.DECOMPILE_DIR,
            "BASE_CONFIG_PATH": build.BASE_CONFIG_PATH,
            "TARGET_CONFIG_DIR": build.TARGET_CONFIG_DIR,
            "LATEST_CONFIG_PATH": build.LATEST_CONFIG_PATH,
            "DOWNLOAD_APK_PATH": build.DOWNLOAD_APK_PATH,
        }
        build.PROJECT_ROOT = self.root
        build.CONFIG_DIR = self.root / "config"
        build.OUT_DIR = self.root / "out"
        build.BUILD_TMP_DIR = build.OUT_DIR / "build_tmp"
        build.BUILD_METADATA_PATH = build.OUT_DIR / "internal" / "build-metadata.json"
        build.UPDATE_JSON_PATH = self.root / "wetype_monet.json"
        build.KSU_CHANGELOG_PATH = self.root / "CHANGELOG.md"
        build.OVERLAY_DIR = self.root / "overlay"
        build.DECOMPILE_DIR = build.OUT_DIR / "decompiled_apk"
        build.BASE_CONFIG_PATH = build.CONFIG_DIR / "base.json"
        build.TARGET_CONFIG_DIR = build.CONFIG_DIR / "targets"
        build.LATEST_CONFIG_PATH = build.CONFIG_DIR / "latest.json"
        build.DOWNLOAD_APK_PATH = build.OUT_DIR / "wetype_latest.apk"
        build.TARGET_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        build.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        build.OUT_DIR.mkdir(parents=True)
        self._write_fixture_apk()

    def tearDown(self):
        for name, value in self.original_paths.items():
            setattr(build, name, value)
        self.temp_dir.cleanup()

    def _write_fixture_apk(self):
        public_xml = build.DECOMPILE_DIR / "res" / "values" / "public.xml"
        public_xml.parent.mkdir(parents=True)
        public_xml.write_text(
            "\n".join(
                [
                    '<resources>',
                    '<public type="color" name="a" id="0x7f060001" />',
                    '<public type="string" name="b" id="0x7f070001" />',
                    '<public type="drawable" name="c" id="0x7f080001" />',
                    '<public type="color" name="ignored" id="0x7f060002" />',
                    '</resources>',
                ]
            ),
            encoding="utf-8",
        )
        hld_root = build.DECOMPILE_DIR / "smali" / build.HLD_PACKAGE_PATH
        hld_root.mkdir(parents=True)
        (hld_root / "r.smali").write_text(
            "\n".join(
                [
                    '.field public static final color_key:I = 0x7f060001',
                    '.field public static final string_key:I = 0x7f070001',
                    '.field public static final drawable_key:I = 0x7f080001',
                ]
            ),
            encoding="utf-8",
        )
        nested = hld_root / "nested"
        nested.mkdir()
        (nested / "ignored.smali").write_text(
            '.field public static final ignored_key:I = 0x7f060002', encoding="utf-8"
        )
        source_drawable = self.root / "overlay" / "assets" / "drawable" / "source.xml"
        source_drawable.parent.mkdir(parents=True)
        source_drawable.write_text('<vector xmlns:android="http://schemas.android.com/apk/res/android" />', encoding="utf-8")
        values_dir = build.DECOMPILE_DIR / "res" / "values"
        values_dir.mkdir(parents=True, exist_ok=True)
        (values_dir / "colors.xml").write_text('<resources><color name="a">#000000</color></resources>', encoding="utf-8")
        (values_dir / "strings.xml").write_text('<resources><string name="b">old value</string></resources>', encoding="utf-8")
        drawable_dir = build.DECOMPILE_DIR / "res" / "drawable"
        drawable_dir.mkdir(parents=True)
        (drawable_dir / "c.xml").write_text('<vector />', encoding="utf-8")

    def _write_base_config(self, drawables=None):
        payload = {
            "theme_colors": [
                {"key": "color_key", "light": "#123456", "night": "#654321", "description": "color"},
                {"key": "missing_color", "light": "#000000"},
            ],
            "theme_strings": [
                {"key": "string_key", "value": "A & B", "description": "string"},
                {"key": "missing_string", "value": "ignored"},
            ],
            "theme_drawables": drawables
            if drawables is not None
            else [{"key": "drawable_key", "file_path": "overlay/assets/drawable/source.xml", "description": "drawable"}],
        }
        build.BASE_CONFIG_PATH.write_text(json.dumps(payload), encoding="utf-8")

    def test_maps_hld_root_resources_and_generates_files(self):
        self._write_base_config()
        output = StringIO()
        with redirect_stdout(output):
            config_path = build.generate_version_config("hash", "1", "1.0", "2026-01-01", [])
            build.sync_src_resources(config_path)

        payload = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["theme_colors"][0]["obfuscated_key"], "a")
        self.assertEqual(payload["theme_strings"][0]["obfuscated_key"], "b")
        self.assertEqual(payload["theme_drawables"][0]["obfuscated_key"], "c")
        self.assertEqual(len(payload["theme_colors"]), 2)
        self.assertEqual(len(payload["theme_strings"]), 1)
        self.assertNotIn("ignored_key", build.parse_hld_key_to_id())
        self.assertIn('<color name="a">#123456</color>', (build.OVERLAY_DIR / "res" / "values" / "colors.xml").read_text(encoding="utf-8"))
        self.assertIn('<color name="missing_color">#000000</color>', (build.OVERLAY_DIR / "res" / "values" / "colors.xml").read_text(encoding="utf-8"))
        self.assertIn('<string name="b">A &amp; B</string>', (build.OVERLAY_DIR / "res" / "values" / "strings.xml").read_text(encoding="utf-8"))
        self.assertTrue((build.OVERLAY_DIR / "res" / "drawable" / "c.xml").is_file())
        self.assertIn("missing_color", output.getvalue())
        self.assertIn("missing_string", output.getvalue())

    def test_fails_for_unmapped_drawable(self):
        self._write_base_config(drawables=[{"key": "missing_drawable", "file_path": "overlay/assets/drawable/source.xml"}])
        with self.assertRaisesRegex(RuntimeError, "missing_drawable"):
            build.generate_version_config("hash", "1", "1.0", "2026-01-01", [])

    def _write_latest_state(self, apk_name="1.0", apk_code="1", apk_sha="a", base_sha="b"):
        config_path = build.TARGET_CONFIG_DIR / f"{apk_name}({apk_code}).json"
        config_path.write_text("{}", encoding="utf-8")
        state = {
            "state_version": 1,
            "module_version": 1,
            "base_sha256": base_sha * 64,
            "upstream": {
                "version_name": apk_name,
                "version_code": apk_code,
                "sha256": apk_sha * 64,
                "release_date": "2026-01-01",
                "config_file": config_path.relative_to(build.CONFIG_DIR).as_posix(),
            },
        }
        build.LATEST_CONFIG_PATH.write_text(json.dumps(state), encoding="utf-8")
        return state

    def test_latest_hash_uses_explicit_state_file(self):
        self._write_latest_state(apk_name="2.0", apk_code="2", apk_sha="e")

        self.assertEqual(build.get_latest_sha256(), ("targets/2.0(2).json", "e" * 64))

    def test_write_latest_config_records_successful_build(self):
        config_path = build.TARGET_CONFIG_DIR / "1.0(1).json"
        config_path.write_text("{}", encoding="utf-8")

        build.write_latest_config(2, "b" * 64, "a" * 64, "1", "1.0", "2026-01-01", config_path)

        self.assertEqual(
            json.loads(build.LATEST_CONFIG_PATH.read_text(encoding="utf-8")),
            {
                "state_version": 1,
                "module_version": 2,
                "base_sha256": "b" * 64,
                "upstream": {
                    "version_name": "1.0",
                    "version_code": "1",
                    "sha256": "a" * 64,
                    "release_date": "2026-01-01",
                    "config_file": "targets/1.0(1).json",
                },
                "release": {
                    "tag": "v2",
                    "title": "微信输入法_1.0_v2",
                },
            },
        )
        self.assertEqual(build.get_latest_sha256(), ("targets/1.0(1).json", "a" * 64))

    def test_should_build_for_apk_or_base_changes(self):
        state = self._write_latest_state()
        self.assertFalse(build.should_build("a" * 64, "b" * 64, state))
        self.assertTrue(build.should_build("c" * 64, "b" * 64, state))
        self.assertTrue(build.should_build("a" * 64, "d" * 64, state))
        self.assertTrue(build.should_build("a" * 64, "b" * 64, None))

    def test_canonical_json_hash_ignores_formatting(self):
        build.BASE_CONFIG_PATH.write_text('{"b": 2, "a": 1}', encoding="utf-8")
        first_hash = build.get_base_sha256()
        build.BASE_CONFIG_PATH.write_text('{\n  "a": 1,\n  "b": 2\n}', encoding="utf-8")

        self.assertEqual(build.get_base_sha256(), first_hash)
        self.assertEqual(build.get_next_module_version({"module_version": 1}), 2)

    def test_module_metadata_uses_latest_integer_version(self):
        build.BUILD_TMP_DIR.mkdir(parents=True)

        self.assertEqual(build.generate_module_prop(2), ("v2", "2"))
        module_prop = (build.BUILD_TMP_DIR / "module.prop").read_text(encoding="utf-8")
        self.assertIn("version=v2", module_prop)
        self.assertIn("versionCode=2", module_prop)
        self.assertIn(f"updateJson={build.UPDATE_JSON_URL}", module_prop)
        self.assertEqual(build.get_module_zip_filename(2), "Wetype_Monet_v2.zip")
        self.assertEqual(build.get_release_title("3.5.4", 2), "微信输入法_3.5.4_v2")

    def test_archive_official_apk_uses_release_filename(self):
        build.DOWNLOAD_APK_PATH.write_bytes(b"official-apk")
        config_path = build.TARGET_CONFIG_DIR / "3.5.2(55201).json"
        zip_path = build.OUT_DIR / "Wetype_Monet_v2.zip"
        config_path.write_text("{}", encoding="utf-8")
        zip_path.write_bytes(b"module-zip")

        archive_path = build.archive_official_apk("3.5.2", "55201")
        monet_path = build.OUT_DIR / "Wetype_Monet_3.5.2(55201)_v2.apk"
        monet_path.write_bytes(b"monet-apk")
        build.write_build_metadata(
            "v2", "2", "3.5.2", "55201", config_path, zip_path, archive_path, monet_path
        )

        self.assertEqual(archive_path.name, "Wetype_3.5.2(55201).apk")
        self.assertEqual(archive_path.read_bytes(), b"official-apk")
        metadata = json.loads(build.BUILD_METADATA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(metadata["official_apk_file"], archive_path.name)
        self.assertEqual(metadata["monet_apk_file"], monet_path.name)

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

        version, release_date, changelog = build.parse_official_changelog_html(html)

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

        version, release_date, changelog = build.parse_official_changelog_html(html)

        self.assertEqual(version, "3.5.2")
        self.assertEqual(release_date, "2026-07-22")
        self.assertEqual(changelog, ["跨设备功能支持自定义设备名称"])

    def test_target_config_preserves_official_changelog(self):
        self._write_base_config()

        config_path = build.generate_version_config(
            "a" * 64,
            "55201",
            "3.5.2",
            "2026-07-22",
            ["跨设备功能支持自定义设备名称", "体验优化与问题修复"],
        )

        payload = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["changelog"],
            ["跨设备功能支持自定义设备名称", "体验优化与问题修复"],
        )

    def test_apply_monet_resources_replaces_target_values_and_drawable(self):
        config_path = build.TARGET_CONFIG_DIR / "1.0(1).json"
        config_path.write_text(
            json.dumps(
                {
                    "theme_colors": [{"obfuscated_key": "a", "light": "#112233", "night": "#445566"}],
                    "theme_strings": [{"obfuscated_key": "b", "value": "new value"}],
                    "theme_drawables": [{"obfuscated_key": "c", "file_path": "overlay/assets/drawable/source.xml"}],
                }
            ),
            encoding="utf-8",
        )

        build.apply_monet_resources(config_path)

        colors = (build.DECOMPILE_DIR / "res" / "values" / "colors.xml").read_text(encoding="utf-8")
        strings = (build.DECOMPILE_DIR / "res" / "values" / "strings.xml").read_text(encoding="utf-8")
        night = (build.DECOMPILE_DIR / "res" / "values-night" / "wetype_monet.xml").read_text(encoding="utf-8")
        drawable = (build.DECOMPILE_DIR / "res" / "drawable" / "c.xml").read_text(encoding="utf-8")
        self.assertIn("#112233", colors)
        self.assertIn("new value", strings)
        self.assertIn("#445566", night)
        self.assertIn("xmlns:android", drawable)

    def test_writes_kernelsu_changelog_and_update_manifest(self):
        build.write_update_changelog(
            2,
            "3.5.2",
            "55201",
            "2026-07-22",
            "a" * 64,
            ["跨设备功能支持自定义设备名称", "体验优化与问题修复"],
        )
        build.write_update_json(2)

        changelog = build.KSU_CHANGELOG_PATH.read_text(encoding="utf-8")
        self.assertIn("# WeType Monet", changelog)
        self.assertIn(f"**Version:** `v2`", changelog)
        self.assertIn(f"**WeType:** `3.5.2 (55201)`", changelog)
        self.assertIn("跨设备功能支持自定义设备名称", changelog)
        self.assertIn("体验优化与问题修复", changelog)
        self.assertIn("a" * 64, changelog)
        self.assertEqual(
            json.loads(build.UPDATE_JSON_PATH.read_text(encoding="utf-8")),
            {
                "versionCode": 2,
                "version": "v2",
                "zipUrl": "https://github.com/0x1e93d/WeType_Monet/releases/download/v2/Wetype_Monet_v2.zip",
                "changelog": "https://raw.githubusercontent.com/0x1e93d/WeType_Monet/main/CHANGELOG.md",
            },
        )

    def test_fails_for_missing_drawable_source(self):
        self._write_base_config(drawables=[{"key": "drawable_key", "file_path": "overlay/assets/drawable/missing.xml"}])
        config_path = build.generate_version_config("hash", "1", "1.0", "2026-01-01", [])
        with self.assertRaisesRegex(FileNotFoundError, "源文件不存在"):
            build.sync_src_resources(config_path)

    def test_base_drawables_match_source_files(self):
        repository_root = Path(__file__).resolve().parents[1]
        base_config = json.loads((repository_root / "config" / "base.json").read_text(encoding="utf-8"))
        drawables = base_config["theme_drawables"]
        configured_names = {Path(item["file_path"]).name for item in drawables}
        source_names = {
            path.name for path in (repository_root / "overlay" / "assets" / "drawable").glob("*") if path.is_file()
        }

        self.assertEqual(configured_names, source_names)
        self.assertEqual(len({item["key"] for item in drawables}), len(drawables))
        self.assertTrue(all(item["description"].strip() for item in drawables))


if __name__ == "__main__":
    unittest.main()
