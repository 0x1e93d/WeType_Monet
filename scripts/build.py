#!/usr/bin/env python3
"""编排微信输入法 Monet 构建流程。"""

import os
import sys
from pathlib import Path

# 支持直接执行脚本与模块方式启动。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import packaging, release, resources, upstream
from scripts.paths import BuildPaths


def main(paths: BuildPaths | None = None):
    paths = paths or BuildPaths()
    print("======================================================")
    print("   微信输入法 Monet Overlay 自动化适配与构建工作流  ")
    print("======================================================\n")

    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.mapping_config_dir.mkdir(parents=True, exist_ok=True)
    paths.out_dir.mkdir(parents=True, exist_ok=True)

    signing_keystore: Path | None = None
    try:
        print("[+] ===== 阶段 1: 检查更新 & 检索/解包 APK =====")
        force_build = os.environ.get("FORCE_BUILD", "").strip().lower() in {"1", "true", "yes"}
        build_input = upstream.download_and_decompile_apk(paths, force_build=force_build)
        if build_input is None:
            return
        sha256_str, apk_code, apk_name, release_date, changelog = build_input

        print("\n[+] ===== 阶段 2: 解析 ID & 生成版本映射 =====")
        config_path = resources.generate_version_config(paths, sha256_str, apk_code, apk_name, release_date, changelog)

        print("\n[+] ===== 阶段 3: 生成 Overlay 资源并编译签名 APK =====")
        resources.sync_src_resources(paths, config_path)
        packaging.prepare_template(paths)
        previous_state = release.get_latest_build_state(paths)
        next_module_version = release.get_next_module_version(
            previous_state, release.get_existing_module_versions()
        )
        module_version, version_code = release.generate_module_prop(paths, next_module_version)
        signing_keystore = packaging.prepare_public_signing_keystore(paths)
        packaging.build_overlay_apk(paths, signing_keystore)
        monet_apk_path = packaging.build_monet_apk(
            paths,
            config_path, apk_name, apk_code, next_module_version, signing_keystore
        )

        print("\n[+] ===== 阶段 4: 打包 Magisk/KernelSU 模块 ZIP =====")
        zip_path = packaging.create_module_zip(paths, next_module_version)
        official_apk_path = packaging.archive_official_apk(paths, apk_name, apk_code)
        release.write_build_metadata(
            paths,
            module_version,
            version_code,
            apk_name,
            apk_code,
            config_path,
            zip_path,
            official_apk_path,
            monet_apk_path,
        )
        release.write_latest_config(
            paths,
            next_module_version,
            resources.get_theme_sha256(paths),
            sha256_str,
            apk_code,
            apk_name,
            release_date,
            config_path,
        )
        release.write_update_changelog(
            paths,
            next_module_version,
            apk_name,
            apk_code,
            release_date,
            sha256_str,
            changelog,
        )
        release.write_update_json(paths, next_module_version)

        print("\n[✓] 所有步骤全流程顺利执行完毕！")

    except Exception as error:
        print(f"\n[!] 工作流执行异常中断: {error}")
        sys.exit(1)
    finally:
        keystore_path = signing_keystore or paths.signing_keystore_path
        if keystore_path.exists():
            keystore_path.unlink()


if __name__ == "__main__":
    main()
