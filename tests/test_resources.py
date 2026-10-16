import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from scripts import resources
from tests.build_fixtures import BuildTestCase


class ResourcesTests(BuildTestCase):
    def test_maps_hld_root_resources_and_generates_files(self):
        self._write_base_config()
        output = StringIO()
        with redirect_stdout(output):
            config_path = resources.generate_version_config(self.paths, "hash", "1", "1.0", "2026-01-01", [])
            resources.sync_src_resources(self.paths, config_path)

        payload = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["theme_colors"][0]["obfuscated_key"], "a")
        self.assertEqual(payload["theme_strings"][0]["obfuscated_key"], "b")
        self.assertEqual(payload["theme_drawables"][0]["obfuscated_key"], "c")
        self.assertEqual(len(payload["theme_colors"]), 2)
        self.assertEqual(len(payload["theme_strings"]), 1)
        self.assertNotIn("ignored_key", resources.parse_hld_key_to_id(self.paths))
        self.assertIn('<color name="a">#123456</color>', (self.paths.overlay_dir / "res" / "values" / "colors.xml").read_text(encoding="utf-8"))
        self.assertIn('<color name="missing_color">#000000</color>', (self.paths.overlay_dir / "res" / "values" / "colors.xml").read_text(encoding="utf-8"))
        self.assertIn('<string name="b">A &amp; B</string>', (self.paths.overlay_dir / "res" / "values" / "strings.xml").read_text(encoding="utf-8"))
        self.assertTrue((self.paths.overlay_dir / "res" / "drawable" / "c.xml").is_file())
        self.assertIn("missing_color", output.getvalue())
        self.assertIn("missing_string", output.getvalue())

    def test_fails_for_unmapped_drawable(self):
        self._write_base_config(drawables=[{"key": "missing_drawable", "file_path": "overlay/assets/drawable/source.xml"}])
        with self.assertRaisesRegex(RuntimeError, "missing_drawable"):
            resources.generate_version_config(self.paths, "hash", "1", "1.0", "2026-01-01", [])

    def test_canonical_json_hash_ignores_formatting(self):
        self.paths.base_config_path.write_text('{"b": 2, "a": 1}', encoding="utf-8")
        first_hash = resources.get_base_sha256(self.paths)
        self.paths.base_config_path.write_text('{\n  "a": 1,\n  "b": 2\n}', encoding="utf-8")

        self.assertEqual(resources.get_base_sha256(self.paths), first_hash)

    def test_target_config_preserves_official_changelog(self):
        self._write_base_config()

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
        config_path = self.paths.target_config_dir / "1.0(1).json"
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
        self._write_base_config(drawables=[{"key": "drawable_key", "file_path": "overlay/assets/drawable/missing.xml"}])
        config_path = resources.generate_version_config(self.paths, "hash", "1", "1.0", "2026-01-01", [])
        with self.assertRaisesRegex(FileNotFoundError, "源文件不存在"):
            resources.sync_src_resources(self.paths, config_path)

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
