import json
import unittest
from pathlib import Path

from scripts import packaging, release
from tests.build_fixtures import BuildTestCase


class PackagingTests(BuildTestCase):
    def test_overlay_signing_command_uses_public_lspatch_keystore(self):
        command = packaging.get_apksigner_sign_command(
            "apksigner",
            Path("public.p12"),
            Path("overlay.apk"),
            Path("aligned.apk"),
        )

        self.assertIn("--ks-type", command)
        self.assertEqual(command[command.index("--ks-type") + 1], "PKCS12")
        self.assertEqual(command[command.index("--ks-key-alias") + 1], packaging.PUBLIC_SIGNING_ALIAS)
        self.assertNotIn("androiddebugkey", command)

    def test_archive_official_apk_uses_release_filename(self):
        self.paths.download_apk_path.parent.mkdir(parents=True, exist_ok=True)
        self.paths.download_apk_path.write_bytes(b"official-apk")
        config_path = self.paths.mapping_config_dir / "3.5.2(55201).json"
        zip_path = self.paths.out_dir / "Wetype_Monet_v2.zip"
        config_path.write_text("{}", encoding="utf-8")
        zip_path.write_bytes(b"module-zip")

        archive_path = packaging.archive_official_apk(self.paths, "3.5.2", "55201")
        monet_path = self.paths.out_dir / "Wetype_Monet_3.5.2(55201)_v2.apk"
        monet_path.write_bytes(b"monet-apk")
        release.write_build_metadata(
            self.paths,
            "v2", "2", "3.5.2", "55201", config_path, zip_path, archive_path, monet_path
        )

        self.assertEqual(archive_path.name, "Wetype_3.5.2(55201).apk")
        self.assertEqual(archive_path.read_bytes(), b"official-apk")
        metadata = json.loads(self.paths.build_metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["official_apk_file"], archive_path.name)
        self.assertEqual(metadata["monet_apk_file"], monet_path.name)


if __name__ == "__main__":
    unittest.main()
