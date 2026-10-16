import json
import tempfile
import unittest
from pathlib import Path

from scripts import resources
from scripts.paths import BuildPaths


class BuildTestCase(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.root = Path(temp_dir.name)
        self.paths = BuildPaths(self.root)
        self.paths.mapping_config_dir.mkdir(parents=True)
        self.paths.out_dir.mkdir(parents=True)
        self._write_fixture_apk()

    def _write_fixture_apk(self):
        public_xml = self.paths.decompile_dir / "res" / "values" / "public.xml"
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
        hld_root = self.paths.decompile_dir / "smali" / resources.HLD_PACKAGE_PATH
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
        values_dir = self.paths.decompile_dir / "res" / "values"
        values_dir.mkdir(parents=True, exist_ok=True)
        (values_dir / "colors.xml").write_text('<resources><color name="a">#000000</color></resources>', encoding="utf-8")
        (values_dir / "strings.xml").write_text('<resources><string name="b">old value</string></resources>', encoding="utf-8")
        drawable_dir = self.paths.decompile_dir / "res" / "drawable"
        drawable_dir.mkdir(parents=True)
        (drawable_dir / "c.xml").write_text('<vector />', encoding="utf-8")

    def _write_theme_config(self, drawables=None):
        payload = {
            "schema_version": 1,
            "target_package": "com.tencent.wetype",
            "colors": {
                "color_key": {"default": "#123456", "night": "#654321", "description": "颜色"},
                "missing_color": {"default": "#000000"},
            },
            "strings": {
                "string_key": {"value": "A & B", "description": "字符串"},
                "missing_string": {"value": "ignored"},
            },
            "drawables": drawables
            if drawables is not None
            else {"drawable_key": {"source": "overlay/assets/drawable/source.xml", "description": "图标"}},
        }
        self.paths.theme_config_path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def _write_latest_state(self, apk_name="1.0", apk_code="1", apk_sha="a", base_sha="b"):
        config_path = self.paths.mapping_config_dir / f"{apk_name}({apk_code}).json"
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
                "config_file": config_path.relative_to(self.paths.config_dir).as_posix(),
            },
        }
        self.paths.latest_config_path.write_text(json.dumps(state), encoding="utf-8")
        return state
