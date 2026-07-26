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
            "OVERLAY_DIR": build.OVERLAY_DIR,
            "DECOMPILE_DIR": build.DECOMPILE_DIR,
            "BASE_CONFIG_PATH": build.BASE_CONFIG_PATH,
            "MODULE_CONFIG_PATH": build.MODULE_CONFIG_PATH,
        }
        build.PROJECT_ROOT = self.root
        build.CONFIG_DIR = self.root / "config"
        build.OUT_DIR = self.root / "out"
        build.OVERLAY_DIR = self.root / "overlay"
        build.DECOMPILE_DIR = build.OUT_DIR / "decompiled_apk"
        build.BASE_CONFIG_PATH = build.CONFIG_DIR / "base.json"
        build.MODULE_CONFIG_PATH = build.CONFIG_DIR / "module.json"
        build.CONFIG_DIR.mkdir(parents=True)
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

    def test_latest_hash_ignores_base_and_module_config(self):
        (build.CONFIG_DIR / "1.0(1).json").write_text('{"sha256": "expected"}', encoding="utf-8")
        build.BASE_CONFIG_PATH.write_text("{}", encoding="utf-8")
        build.MODULE_CONFIG_PATH.write_text('{"version": "1.0.0"}', encoding="utf-8")

        self.assertEqual(build.get_latest_sha256(), ("1.0(1).json", "expected"))

    def test_fails_for_missing_drawable_source(self):
        self._write_base_config(drawables=[{"key": "drawable_key", "file_path": "overlay/assets/drawable/missing.xml"}])
        config_path = build.generate_version_config("hash", "1", "1.0", "2026-01-01", [])
        with self.assertRaisesRegex(FileNotFoundError, "源文件不存在"):
            build.sync_src_resources(config_path)


if __name__ == "__main__":
    unittest.main()
