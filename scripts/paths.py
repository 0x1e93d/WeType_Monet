"""集中管理源码、配置与构建产物路径。"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BuildPaths:
    project_root: Path = Path(__file__).resolve().parents[1]

    @property
    def config_dir(self) -> Path:
        return self.project_root / "config"

    @property
    def overlay_dir(self) -> Path:
        return self.project_root / "overlay"

    @property
    def out_dir(self) -> Path:
        return self.project_root / "out"

    @property
    def build_tmp_dir(self) -> Path:
        return self.out_dir / "build_tmp"

    @property
    def template_dir(self) -> Path:
        return self.project_root / "module_template"

    @property
    def decompile_dir(self) -> Path:
        return self.out_dir / "decompiled_apk"

    @property
    def build_metadata_path(self) -> Path:
        return self.out_dir / "internal" / "build-metadata.json"

    @property
    def theme_config_path(self) -> Path:
        return self.config_dir / "theme.json"

    @property
    def mapping_config_dir(self) -> Path:
        return self.config_dir / "mappings"

    @property
    def latest_config_path(self) -> Path:
        return self.config_dir / "latest.json"

    @property
    def update_json_path(self) -> Path:
        return self.project_root / "wetype_monet.json"

    @property
    def ksu_changelog_path(self) -> Path:
        return self.project_root / "CHANGELOG.md"

    @property
    def download_apk_path(self) -> Path:
        return self.out_dir / "wetype_latest.apk"

    @property
    def public_signing_bks_path(self) -> Path:
        return self.project_root / "signing" / "LSPatch.bks"

    @property
    def signing_keystore_path(self) -> Path:
        return self.out_dir / "internal" / "monet-release.p12"
