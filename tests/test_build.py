import hashlib
import json
import os
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET
import zipfile
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch

from scripts import build, upstream
from tests.build_fixtures import BuildTestCase


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class BuildPipelineTests(BuildTestCase):
    def setUp(self):
        super().setUp()
        self._write_theme_config()
        self.apk_content = b"official-apk"
        self.apk_sha256 = hashlib.sha256(self.apk_content).hexdigest()
        state = self._write_latest_state()
        state["upstream"]["sha256"] = self.apk_sha256
        self.previous_state = json.dumps(state)
        self.paths.latest_config_path.write_text(self.previous_state, encoding="utf-8")

        self.decoded_fixture = self.root / "decoded-fixture"
        shutil.copytree(self.paths.decompile_dir, self.decoded_fixture)
        (self.decoded_fixture / "AndroidManifest.xml").write_text(
            '<manifest package="com.tencent.wetype" />', encoding="utf-8"
        )
        (self.decoded_fixture / "apktool.yml").write_text(
            "versionCode: '1'\nversionName: '1.0'\n", encoding="utf-8"
        )
        (self.decoded_fixture / "res/values/colors.xml").write_text(
            '<resources><color name="a">#000000</color><color name="missing_color">#ffffff</color></resources>',
            encoding="utf-8",
        )
        shutil.copytree(PROJECT_ROOT / "module_template", self.paths.template_dir)
        self.paths.public_signing_bks_path.parent.mkdir()
        self.paths.public_signing_bks_path.write_bytes(b"public-signing-fixture")

        toolchain_dir = self.root / "toolchain"
        toolchain_dir.mkdir()
        self.build_environment = {"FORCE_BUILD": "true"}
        for variable, filename in {
            "AAPT2_PATH": "aapt2",
            "ZIPALIGN_PATH": "zipalign",
            "APKSIGNER_PATH": "apksigner",
            "ANDROID_JAR_PATH": "android.jar",
            "KEYTOOL_PATH": "keytool",
            "BCPROV_JAR_PATH": "bcprov.jar",
        }.items():
            tool_path = toolchain_dir / filename
            tool_path.write_bytes(b"build-tool-fixture")
            self.build_environment[variable] = str(tool_path)
        self.fail_link = False

    def open_upstream_url(self, request, **options):
        if request.full_url == upstream.APK_URL:
            return BytesIO(self.apk_content)
        if request.full_url == upstream.CHANGELOG_URL:
            return BytesIO(
                '<div>发布版本:<span>1.0</span></div>'
                '<div>发布日期:<span>2026-01-01</span></div>'
                '<div class="content"><h2>- 体验优化</h2></div>'.encode("utf-8")
            )
        raise AssertionError(f"未知请求: {request.full_url}")

    def run_build_command(self, command, **options):
        tool_name = Path(command[0]).stem
        if tool_name == "git":
            return subprocess.CompletedProcess(command, 0, stdout="v2\n", stderr="")
        if tool_name == "apktool" and command[1] == "d":
            shutil.copytree(self.decoded_fixture, Path(command[command.index("-o") + 1]))
        elif tool_name == "apktool" and command[1] == "b":
            output_apk = Path(command[command.index("-o") + 1])
            with zipfile.ZipFile(output_apk, "w") as archive:
                for source_path in self.paths.decompile_dir.rglob("*"):
                    if source_path.is_file():
                        archive.write(source_path, source_path.relative_to(self.paths.decompile_dir))
        elif tool_name == "keytool":
            Path(command[command.index("-destkeystore") + 1]).write_bytes(b"temporary-keystore")
        elif tool_name == "aapt2":
            if command[1] == "link" and self.fail_link:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="link failed")
            Path(command[command.index("-o") + 1]).write_bytes(b"overlay-apk")
        elif tool_name == "zipalign":
            shutil.copy2(command[-2], command[-1])
        elif tool_name == "apksigner" and command[1] == "sign":
            shutil.copy2(command[-1], command[command.index("--out") + 1])
        elif tool_name == "apksigner" and command[1] == "verify":
            if not Path(command[-1]).is_file():
                raise AssertionError("待验证的 APK 不存在")
        else:
            raise AssertionError(f"未知构建命令: {command}")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def run_build(self, **environment):
        output = StringIO()
        with (
            patch.dict(os.environ, {**self.build_environment, **environment}),
            patch.object(upstream.urllib.request, "urlopen", side_effect=self.open_upstream_url),
            patch.object(subprocess, "run", side_effect=self.run_build_command),
            redirect_stdout(output),
        ):
            build.main(self.paths)
        return output.getvalue()

    def test_force_build_generates_artifacts_and_release_state(self):
        self.run_build()

        metadata = json.loads(self.paths.build_metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["module_version"], "v3")
        self.assertEqual(metadata["config_file"], "mappings/1.0(1).json")
        self.assertEqual(metadata["zip_file"], "Wetype_Monet_v3.zip")
        self.assertEqual(metadata["official_apk_file"], "Wetype_1.0(1).apk")
        self.assertEqual(metadata["monet_apk_file"], "Wetype_Monet_1.0(1)_v3.apk")
        self.assertEqual((self.paths.out_dir / metadata["official_apk_file"]).read_bytes(), self.apk_content)
        with zipfile.ZipFile(self.paths.out_dir / metadata["zip_file"]) as archive:
            self.assertEqual(archive.read("files/WetypeMonet.apk"), b"overlay-apk")
            self.assertIn("version=v3", archive.read("module.prop").decode("utf-8"))
            self.assertIn("service.sh", archive.namelist())
        with zipfile.ZipFile(self.paths.out_dir / metadata["monet_apk_file"]) as archive:
            self.assertIn("#123456", archive.read("res/values/colors.xml").decode("utf-8"))
            self.assertIn("#654321", archive.read("res/values-night/wetype_monet.xml").decode("utf-8"))
            self.assertIn("A &amp; B", archive.read("res/values/strings.xml").decode("utf-8"))
        state = json.loads(self.paths.latest_config_path.read_text(encoding="utf-8"))
        self.assertEqual(state["module_version"], 3)
        self.assertEqual(state["upstream"]["sha256"], self.apk_sha256)
        self.assertEqual(json.loads(self.paths.update_json_path.read_text(encoding="utf-8"))["version"], "v3")
        self.assertIn("体验优化", self.paths.ksu_changelog_path.read_text(encoding="utf-8"))
        self.assertFalse(self.paths.signing_keystore_path.exists())

    def test_unchanged_upstream_skips_artifacts_and_release_state(self):
        self.run_build(FORCE_BUILD="false")

        self.assertEqual(self.paths.latest_config_path.read_text(encoding="utf-8"), self.previous_state)
        self.assertFalse(self.paths.build_metadata_path.exists())
        self.assertFalse(self.paths.update_json_path.exists())
        self.assertFalse(self.paths.module_dir.exists())

    def test_build_uses_separate_workspaces_and_generated_manifest(self):
        self.run_build()

        self.assertTrue((self.root / "out/apk/official.apk").is_file())
        self.assertEqual((self.root / "out/apk/official.apk").read_bytes(), self.apk_content)
        decoded_manifest = ET.parse(self.root / "out/apk/decoded/AndroidManifest.xml")
        self.assertEqual(decoded_manifest.getroot().get("package"), "com.tencent.wetype")
        overlay_manifest = ET.parse(self.root / "out/overlay/AndroidManifest.xml").getroot()
        android_prefix = "{http://schemas.android.com/apk/res/android}"
        self.assertEqual(overlay_manifest.get("package"), "monet.com.tencent.wetype")
        self.assertEqual(overlay_manifest.find("uses-sdk").get(android_prefix + "minSdkVersion"), "34")
        self.assertEqual(overlay_manifest.find("uses-sdk").get(android_prefix + "targetSdkVersion"), "36")
        self.assertEqual(overlay_manifest.find("overlay").get(android_prefix + "targetPackage"), "com.tencent.wetype")
        self.assertEqual((self.root / "out/overlay/WetypeMonet.apk").read_bytes(), b"overlay-apk")
        self.assertEqual((self.root / "out/module/files/WetypeMonet.apk").read_bytes(), b"overlay-apk")
        for filename in ("unsigned.apk", "aligned.apk", "monet-unsigned.apk", "monet-aligned.apk", "compiled.zip"):
            self.assertFalse((self.paths.out_dir / filename).exists())

    def test_repeated_build_clears_previous_workspaces(self):
        self.run_build()
        stale_files = [
            self.paths.decompile_dir / "res/drawable/obsolete.xml",
            self.paths.overlay_dir / "res/drawable/obsolete.xml",
            self.paths.module_dir / "obsolete.txt",
        ]
        for stale_file in stale_files:
            stale_file.write_bytes(b"previous-build")

        self.run_build()

        for stale_file in stale_files:
            self.assertFalse(stale_file.exists())
        metadata = json.loads(self.paths.build_metadata_path.read_text(encoding="utf-8"))
        with zipfile.ZipFile(self.paths.out_dir / metadata["zip_file"]) as archive:
            self.assertNotIn("obsolete.txt", archive.namelist())

    def test_failed_build_preserves_release_state_and_cleans_keystore(self):
        self.fail_link = True

        with self.assertRaises(SystemExit) as raised:
            self.run_build()

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(self.paths.latest_config_path.read_text(encoding="utf-8"), self.previous_state)
        self.assertFalse(self.paths.build_metadata_path.exists())
        self.assertFalse(self.paths.update_json_path.exists())
        self.assertFalse(self.paths.signing_keystore_path.exists())


if __name__ == "__main__":
    unittest.main()
