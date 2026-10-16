import json
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from scripts import resources
from tests.build_fixtures import BuildTestCase


class ResourcesTests(BuildTestCase):
    def test_maps_hld_root_resources_and_generates_files(self):
        self._write_theme_config()
        output = StringIO()
        with redirect_stdout(output):
            config_path = resources.generate_version_config(self.paths, "hash", "1", "1.0", "2026-01-01", [])
            resources.sync_src_resources(self.paths, config_path)

        payload = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(payload.get("colors"), {"color_key": "a", "missing_color": "missing_color"})
        self.assertEqual(payload.get("strings"), {"string_key": "b"})
        self.assertEqual(payload.get("drawables"), {"drawable_key": "c"})
        self.assertNotIn("ignored_key", payload["colors"])
        self.assertIn('<color name="a">#123456</color>', (self.paths.overlay_dir / "res" / "values" / "colors.xml").read_text(encoding="utf-8"))
        self.assertIn('<color name="missing_color">#000000</color>', (self.paths.overlay_dir / "res" / "values" / "colors.xml").read_text(encoding="utf-8"))
        self.assertIn('<string name="b">A &amp; B</string>', (self.paths.overlay_dir / "res" / "values" / "strings.xml").read_text(encoding="utf-8"))
        self.assertTrue((self.paths.overlay_dir / "res" / "drawable" / "c.xml").is_file())
        self.assertIn("missing_color", output.getvalue())
        self.assertIn("missing_string", output.getvalue())

    def test_fails_for_unmapped_drawable(self):
        self._write_theme_config(drawables={"missing_drawable": {"source": "overlay/assets/drawable/source.xml"}})
        with self.assertRaisesRegex(RuntimeError, "missing_drawable"):
            resources.generate_version_config(self.paths, "hash", "1", "1.0", "2026-01-01", [])

    def test_canonical_json_hash_ignores_formatting(self):
        self.paths.theme_config_path.write_text('{"b": 2, "a": 1}', encoding="utf-8")
        first_hash = resources.get_theme_sha256(self.paths)
        self.paths.theme_config_path.write_text('{\n  "a": 1,\n  "b": 2\n}', encoding="utf-8")

        self.assertEqual(resources.get_theme_sha256(self.paths), first_hash)

    def test_target_config_preserves_official_changelog(self):
        self._write_theme_config()

        config_path = resources.generate_version_config(
            self.paths,
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
        theme = self._write_theme_config()
        theme["colors"] = {"color_key": {"default": "#112233", "night": "#445566"}}
        theme["strings"] = {"string_key": {"value": "new value"}}
        self.paths.theme_config_path.write_text(json.dumps(theme), encoding="utf-8")
        config_path = self.paths.mapping_config_dir / "1.0(1).json"
        config_path.write_text(
            json.dumps(
                {
                    "colors": {"color_key": "a"},
                    "strings": {"string_key": "b"},
                    "drawables": {"drawable_key": "c"},
                }
            ),
            encoding="utf-8",
        )

        resources.apply_monet_resources(self.paths, config_path)

        colors = (self.paths.decompile_dir / "res" / "values" / "colors.xml").read_text(encoding="utf-8")
        strings = (self.paths.decompile_dir / "res" / "values" / "strings.xml").read_text(encoding="utf-8")
        night = (self.paths.decompile_dir / "res" / "values-night" / "wetype_monet.xml").read_text(encoding="utf-8")
        drawable = (self.paths.decompile_dir / "res" / "drawable" / "c.xml").read_text(encoding="utf-8")
        self.assertIn("#112233", colors)
        self.assertIn("new value", strings)
        self.assertIn("#445566", night)
        self.assertIn("xmlns:android", drawable)

    def test_fails_for_missing_drawable_source(self):
        self._write_theme_config(drawables={"drawable_key": {"source": "overlay/assets/drawable/missing.xml"}})
        config_path = resources.generate_version_config(self.paths, "hash", "1", "1.0", "2026-01-01", [])
        with self.assertRaisesRegex(FileNotFoundError, "源文件不存在"):
            resources.sync_src_resources(self.paths, config_path)

    def test_theme_drawables_match_source_files(self):
        repository_root = Path(__file__).resolve().parents[1]
        theme_path = repository_root / "config" / "theme.json"
        self.assertTrue(theme_path.is_file(), "主题配置应独立存放在 config/theme.json")
        theme = json.loads(theme_path.read_text(encoding="utf-8"))
        drawables = theme["drawables"]
        configured_names = {Path(item["source"]).name for item in drawables.values()}
        source_names = {
            path.name for path in (repository_root / "overlay" / "assets" / "drawable").glob("*") if path.is_file()
        }

        self.assertEqual(configured_names, source_names)

    def test_mapping_distinguishes_same_name_across_resource_types(self):
        theme = self._write_theme_config()
        theme["colors"] = {"shared": {"default": "#123456"}}
        theme["strings"] = {"shared": {"value": "same name"}}
        self.paths.theme_config_path.write_text(json.dumps(theme), encoding="utf-8")
        hld_dir = self.paths.decompile_dir / "smali" / resources.HLD_PACKAGE_PATH
        for filename, resource_id in (("colors", "0x7f060001"), ("strings", "0x7f070001"), ("attrs", "0x7f010001")):
            (hld_dir / f"{filename}.smali").write_text(
                f'.field public static final shared:I = {resource_id}', encoding="utf-8"
            )

        config_path = resources.generate_version_config(self.paths, "hash", "1", "1.0", "2026-01-01", [])

        mapping = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(mapping.get("colors"), {"shared": "a"})
        self.assertEqual(mapping.get("strings"), {"shared": "b"})

    def test_theme_changes_reuse_mapping_without_rewriting_it(self):
        theme = self._write_theme_config()
        config_path = resources.generate_version_config(self.paths, "hash", "1", "1.0", "2026-01-01", [])
        mapping_bytes = config_path.read_bytes()
        theme["colors"]["color_key"]["default"] = "#abcdef"
        theme["colors"]["color_key"]["note"] = "说明不参与资源生成"
        theme["note"] = "主题备注"
        self.paths.theme_config_path.write_text(json.dumps(theme), encoding="utf-8")

        resources.sync_src_resources(self.paths, config_path)

        colors = ET.parse(self.paths.overlay_dir / "res/values/colors.xml")
        self.assertEqual(colors.find("color[@name='a']").text, "#abcdef")
        self.assertEqual(config_path.read_bytes(), mapping_bytes)

    def test_dark_named_color_uses_default_configuration(self):
        theme = self._write_theme_config()
        theme["colors"] = {
            "ime_skin_dark_keyboard_background": {"default": "@android:color/system_surface_container_dark"}
        }
        self.paths.theme_config_path.write_text(json.dumps(theme), encoding="utf-8")
        config_path = resources.generate_version_config(self.paths, "hash", "1", "1.0", "2026-01-01", [])

        resources.sync_src_resources(self.paths, config_path)

        defaults = ET.parse(self.paths.overlay_dir / "res/values/colors.xml")
        night = ET.parse(self.paths.overlay_dir / "res/values-night/colors.xml")
        self.assertEqual(
            defaults.find("color[@name='ime_skin_dark_keyboard_background']").text,
            "@android:color/system_surface_container_dark",
        )
        self.assertIsNone(night.find("color[@name='ime_skin_dark_keyboard_background']"))

    def test_empty_string_value_clears_existing_text(self):
        theme = self._write_theme_config(drawables={})
        theme["colors"] = {}
        theme["strings"] = {"string_key": {"value": "", "description": "清空文本"}}
        self.paths.theme_config_path.write_text(json.dumps(theme), encoding="utf-8")
        config_path = resources.generate_version_config(self.paths, "hash", "1", "1.0", "2026-01-01", [])

        resources.apply_monet_resources(self.paths, config_path)

        strings = ET.parse(self.paths.decompile_dir / "res/values/strings.xml")
        self.assertEqual(strings.find("string[@name='b']").text or "", "")

    def test_string_without_value_preserves_existing_text(self):
        theme = self._write_theme_config(drawables={})
        theme["colors"] = {}
        theme["strings"] = {"string_key": {"description": "保留原文", "note": "没有设置 value"}}
        self.paths.theme_config_path.write_text(json.dumps(theme), encoding="utf-8")
        config_path = resources.generate_version_config(self.paths, "hash", "1", "1.0", "2026-01-01", [])

        resources.apply_monet_resources(self.paths, config_path)
        resources.sync_src_resources(self.paths, config_path)

        strings = ET.parse(self.paths.decompile_dir / "res/values/strings.xml")
        overlay = ET.parse(self.paths.overlay_dir / "res/values/strings.xml")
        self.assertEqual(strings.find("string[@name='b']").text, "old value")
        self.assertIsNone(overlay.find("string[@name='b']"))

    def test_drawable_requires_source(self):
        self._write_theme_config(drawables={"drawable_key": {"description": "缺少源文件"}})

        with self.assertRaisesRegex(ValueError, "source"):
            resources.generate_version_config(self.paths, "hash", "1", "1.0", "2026-01-01", [])


if __name__ == "__main__":
    unittest.main()
