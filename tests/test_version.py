import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "version.py"
SPEC = importlib.util.spec_from_file_location("wetype_version", SCRIPT_PATH)
version = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(version)


class VersionTests(unittest.TestCase):
    def test_bump_patch_keeps_major_and_minor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "module.json"
            config_path.write_text('{"version": "1.2.99"}', encoding="utf-8")

            self.assertEqual(version.bump_patch(config_path), "1.2.100")
            self.assertEqual(json.loads(config_path.read_text(encoding="utf-8"))["version"], "1.2.100")


if __name__ == "__main__":
    unittest.main()
