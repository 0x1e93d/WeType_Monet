"""查找构建工具并打包、签名发布产物。"""

import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from scripts import release, resources
from scripts.paths import BuildPaths

PUBLIC_SIGNING_PASSWORD = "114514"
PUBLIC_SIGNING_ALIAS = "114514"


def find_sdk_tools() -> tuple[str, str, str, str]:
    """自动查找 aapt2, zipalign, apksigner 与 android.jar 路径"""
    aapt2 = os.environ.get("AAPT2_PATH")
    zipalign = os.environ.get("ZIPALIGN_PATH")
    apksigner = os.environ.get("APKSIGNER_PATH")
    android_jar = os.environ.get("ANDROID_JAR_PATH")

    sdk_root = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not sdk_root and os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if local_appdata:
            sdk_root = os.path.join(local_appdata, "Android", "Sdk")

    build_tools_dir = os.path.join(sdk_root, "build-tools") if sdk_root and os.path.exists(os.path.join(sdk_root, "build-tools")) else None
    latest_build_tool = None
    if build_tools_dir:
        versions = sorted(os.listdir(build_tools_dir), reverse=True)
        if versions:
            latest_build_tool = os.path.join(build_tools_dir, versions[0])

    def locate_tool(env_val, tool_name):
        if env_val and os.path.exists(env_val):
            return env_val

        exe_name = f"{tool_name}.exe" if os.name == "nt" else tool_name
        bat_name = f"{tool_name}.bat" if os.name == "nt" else tool_name

        if latest_build_tool:
            for cand in [os.path.join(latest_build_tool, exe_name), os.path.join(latest_build_tool, bat_name)]:
                if os.path.exists(cand):
                    return cand

        found = shutil.which(tool_name)
        if found:
            return found

        return None

    aapt2 = locate_tool(aapt2, "aapt2")
    zipalign = locate_tool(zipalign, "zipalign")
    apksigner = locate_tool(apksigner, "apksigner")

    if not android_jar and sdk_root and os.path.exists(os.path.join(sdk_root, "platforms")):
        platforms_dir = os.path.join(sdk_root, "platforms")
        platforms = sorted(os.listdir(platforms_dir), reverse=True)
        for plat in platforms:
            candidate = os.path.join(platforms_dir, plat, "android.jar")
            if os.path.exists(candidate):
                android_jar = candidate
                break

    if not aapt2 or not os.path.exists(aapt2):
        raise RuntimeError("未找到 aapt2！请确认已安装 Android SDK 或手动配置 AAPT2_PATH 环境变量。")
    if not zipalign or not os.path.exists(zipalign):
        raise RuntimeError("未找到 zipalign！请确认已安装 Android SDK 或手动配置 ZIPALIGN_PATH 环境变量。")
    if not apksigner or not os.path.exists(apksigner):
        raise RuntimeError("未找到 apksigner！请确认已安装 Android SDK 或手动配置 APKSIGNER_PATH 环境变量。")
    if not android_jar or not os.path.exists(android_jar):
        raise RuntimeError("未找到 android.jar！请确认已安装 Android SDK 或手动配置 ANDROID_JAR_PATH 环境变量。")

    print(
        "[+] 成功定位工具链:"
        f"\n    - AAPT2: {aapt2}"
        f"\n    - ZIPALIGN: {zipalign}"
        f"\n    - APKSIGNER: {apksigner}"
        f"\n    - ANDROID_JAR: {android_jar}"
    )
    return aapt2, zipalign, apksigner, android_jar


def find_keytool() -> str:
    configured = os.environ.get("KEYTOOL_PATH")
    if configured and Path(configured).is_file():
        return configured
    executable = "keytool.exe" if os.name == "nt" else "keytool"
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidate = Path(java_home) / "bin" / executable
        if candidate.is_file():
            return str(candidate)
    located = shutil.which(executable)
    if located:
        return located
    raise RuntimeError("未找到 keytool，请配置 JAVA_HOME 或 KEYTOOL_PATH")


def prepare_public_signing_keystore(paths: BuildPaths) -> Path:
    """将仓库中的公开 BKS 密钥临时转换为 apksigner 可直接使用的 PKCS#12。"""
    if not paths.public_signing_bks_path.is_file():
        raise FileNotFoundError(f"[!] 找不到公开签名密钥: {paths.public_signing_bks_path}")
    provider_path = os.environ.get("BCPROV_JAR_PATH")
    if not provider_path or not Path(provider_path).is_file():
        raise RuntimeError("未找到 Bouncy Castle Provider，请配置 BCPROV_JAR_PATH")
    keystore_path = paths.signing_keystore_path
    keystore_path.parent.mkdir(parents=True, exist_ok=True)
    if keystore_path.exists():
        keystore_path.unlink()
    command = [find_keytool(), "-importkeystore", "-noprompt", "-srckeystore", str(paths.public_signing_bks_path), "-srcstoretype", "BKS", "-srcstorepass", PUBLIC_SIGNING_PASSWORD, "-srcalias", PUBLIC_SIGNING_ALIAS, "-destkeystore", str(keystore_path), "-deststoretype", "PKCS12", "-deststorepass", PUBLIC_SIGNING_PASSWORD, "-destkeypass", PUBLIC_SIGNING_PASSWORD, "-destalias", PUBLIC_SIGNING_ALIAS, "-providerclass", "org.bouncycastle.jce.provider.BouncyCastleProvider", "-providerpath", provider_path]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if result.returncode != 0:
        raise RuntimeError(f"公开 BKS 密钥转换失败:\n{result.stderr or result.stdout}")
    return keystore_path


def get_apksigner_sign_command(
    apksigner: str | Path,
    keystore: Path,
    output_apk: Path,
    input_apk: Path,
) -> list[str]:
    """生成 APK 发布签名命令。"""
    return [
        str(apksigner),
        "sign",
        "--ks",
        str(keystore),
        "--ks-type",
        "PKCS12",
        "--ks-pass",
        f"pass:{PUBLIC_SIGNING_PASSWORD}",
        "--key-pass",
        f"pass:{PUBLIC_SIGNING_PASSWORD}",
        "--ks-key-alias",
        PUBLIC_SIGNING_ALIAS,
        "--v1-signing-enabled",
        "true",
        "--v2-signing-enabled",
        "true",
        "--v3-signing-enabled",
        "true",
        "--v4-signing-enabled",
        "false",
        "--out",
        str(output_apk),
        str(input_apk),
    ]


def ensure_original_package_name(paths: BuildPaths):
    manifest_path = paths.decompile_dir / "AndroidManifest.xml"
    manifest = manifest_path.read_text(encoding="utf-8", errors="ignore")
    package_match = re.search(r'<manifest\b[^>]*\bpackage=["\']([^"\']+)["\']', manifest)
    if not package_match:
        raise RuntimeError(f"[!] 无法读取已解包 APK 的包名: {manifest_path}")
    if package_match.group(1) != "com.tencent.wetype":
        raise RuntimeError(f"[!] 原始包名异常: {package_match.group(1)}")


def build_monet_apk(
    paths: BuildPaths,
    config_file: Path,
    apk_name: str,
    apk_code: str,
    module_version: int,
    signing_keystore: Path,
) -> Path:
    """重建、发布签名并校验保持原包名的 Monet 微信输入法 APK。"""
    _, zipalign, apksigner, _ = find_sdk_tools()
    ensure_original_package_name(paths)
    resources.apply_monet_resources(paths, config_file)
    unsigned_apk = paths.apk_dir / "unsigned.apk"
    aligned_apk = paths.apk_dir / "aligned.apk"
    final_apk = paths.out_dir / release.get_monet_apk_filename(apk_name, apk_code, module_version)
    for path in (unsigned_apk, aligned_apk, final_apk, Path(f"{final_apk}.idsig")):
        if path.exists():
            path.unlink()
    print("[*] 重建 Monet 微信输入法 APK (apktool)...")
    build_result = subprocess.run(["apktool", "b", str(paths.decompile_dir), "-o", str(unsigned_apk)], capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if build_result.returncode != 0:
        raise RuntimeError(f"Apktool 重建 Monet APK 失败:\n{build_result.stderr or build_result.stdout}")
    subprocess.run([str(zipalign), "-p", "-f", "4", str(unsigned_apk), str(aligned_apk)], check=True)
    sign_command = get_apksigner_sign_command(apksigner, signing_keystore, final_apk, aligned_apk)
    subprocess.run(sign_command, check=True)
    subprocess.run([str(apksigner), "verify", "--verbose", "--print-certs", str(final_apk)], check=True)
    for path in (unsigned_apk, aligned_apk, Path(f"{final_apk}.idsig")):
        if path.exists():
            path.unlink()
    print(f"[+] Monet 微信输入法 APK 生成成功 -> {final_apk}")
    return final_apk


def write_overlay_manifest(paths: BuildPaths) -> Path:
    """生成 Overlay 清单，统一声明覆盖目标与 SDK 版本。"""
    manifest_path = paths.overlay_dir / "AndroidManifest.xml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="monet.com.tencent.wetype"
    android:versionCode="1"
    android:versionName="1.0">
    <uses-sdk
        android:minSdkVersion="34"
        android:targetSdkVersion="36"/>
    <overlay
        android:isStatic="false"
        android:priority="10"
        android:targetPackage="com.tencent.wetype"/>
    <application
        android:extractNativeLibs="false"
        android:hasCode="false"/>
</manifest>
""",
        encoding="utf-8",
    )
    return manifest_path


def build_overlay_apk(paths: BuildPaths, signing_keystore: Path):
    """编译并签名 Overlay APK"""
    aapt2, zipalign, apksigner, android_jar = find_sdk_tools()

    target_apk_dir = paths.module_dir / "files"
    target_apk_dir.mkdir(parents=True, exist_ok=True)

    compiled_zip = paths.overlay_dir / "compiled.zip"
    unsigned_apk = paths.overlay_dir / "unsigned.apk"
    aligned_apk = paths.overlay_dir / "aligned.apk"
    final_apk = paths.overlay_dir / "WetypeMonet.apk"

    res_dir = paths.overlay_dir / "res"
    manifest_xml = write_overlay_manifest(paths)

    print("[1/4] 编译资源 (aapt2 compile)...")
    res = subprocess.run(
        [str(aapt2), "compile", "--dir", str(res_dir), "-o", str(compiled_zip)],
        capture_output=True, text=True, encoding="utf-8", errors="ignore"
    )
    if res.returncode != 0:
        raise RuntimeError(f"aapt2 compile 失败:\n{res.stderr or res.stdout}")

    print("[2/4] 链接 APK (aapt2 link)...")
    link_cmd = [
        str(aapt2), "link",
        "-I", str(android_jar),
        "--manifest", str(manifest_xml),
        "-o", str(unsigned_apk),
        str(compiled_zip),
        "--auto-add-overlay",
    ]
    res = subprocess.run(link_cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if res.returncode != 0:
        raise RuntimeError(f"aapt2 link 失败:\n{res.stderr or res.stdout}")

    print("[3/4] 4 字节对齐 (zipalign)...")
    if aligned_apk.exists():
        aligned_apk.unlink()
    subprocess.run([str(zipalign), "-p", "-f", "4", str(unsigned_apk), str(aligned_apk)], check=True)

    print("[4/4] 使用 LSPatch 发布签名进行 APK 签名 (apksigner)...")
    if final_apk.exists():
        final_apk.unlink()

    sign_cmd = get_apksigner_sign_command(apksigner, signing_keystore, final_apk, aligned_apk)
    subprocess.run(sign_cmd, check=True)
    shutil.copy2(final_apk, target_apk_dir / final_apk.name)

    for tmp in [compiled_zip, unsigned_apk, aligned_apk, Path(f"{final_apk}.idsig")]:
        if tmp.exists():
            tmp.unlink()

    print(f"[+] Overlay APK 生成成功 -> {final_apk}")


def prepare_template(paths: BuildPaths):
    """同步模块模板结构"""
    if paths.module_dir.exists():
        shutil.rmtree(paths.module_dir)
    paths.module_dir.mkdir(parents=True, exist_ok=True)

    if paths.template_dir.exists():
        shutil.copytree(paths.template_dir, paths.module_dir, dirs_exist_ok=True)


def create_module_zip(paths: BuildPaths, module_version: int) -> Path:
    """打包输出 Magisk/KernelSU 模块 ZIP。"""
    print("[*] 打包 Magisk/KernelSU 刷机 ZIP 包...")
    zip_path = paths.out_dir / release.get_module_zip_filename(module_version)

    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in paths.module_dir.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(paths.module_dir)
                archive.write(file_path, arcname)

    print(f"[+] 刷机包构建成功: {zip_path}")
    return zip_path


def archive_official_apk(paths: BuildPaths, apk_name: str, apk_code: str) -> Path:
    """将本次成功适配的官方 APK 复制为可发布的归档产物。"""
    if not paths.download_apk_path.is_file():
        raise FileNotFoundError(f"官方 APK 不存在，无法归档: {paths.download_apk_path}")

    archive_path = paths.out_dir / release.get_official_apk_filename(apk_name, apk_code)
    shutil.copy2(paths.download_apk_path, archive_path)
    print(f"[+] 官方 APK 已归档: {archive_path}")
    return archive_path
