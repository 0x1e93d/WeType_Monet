#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

# ----------------- 路径配置 -----------------
SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent

SRC_DIR = PROJECT_ROOT / "src"
OUT_DIR = PROJECT_ROOT / "out"
BUILD_TMP_DIR = OUT_DIR / "build_tmp"
TEMPLATE_DIR = PROJECT_ROOT / "module_template"

# ----------------- 模块信息 -----------------
MODULE_ID = "Wetype_Monet"
MODULE_NAME = "微信输入法 Monet"
MODULE_AUTHOR = "酷安@1e93d"
MODULE_DESCRIPTION = "为微信输入法提供 Monet 动态色彩主题。"
BASE_VERSION = "v1.0.0"
UPDATE_JSON_URL = ""

# ----------------- Git 工具函数 -----------------

def get_git_info() -> tuple[str, str]:
    """获取 Git 提交次数 (versionCode) 与 Short Hash"""
    try:
        count = subprocess.check_output(["git", "rev-list", "--count", "HEAD"], stderr=subprocess.DEVNULL, text=True).strip()
        git_hash = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL, text=True).strip()
        return count or "1", git_hash or "dev"
    except Exception:
        return "1", "dev"

# ----------------- SDK / Java 环境工具 -----------------

def fix_java_env() -> str | None:
    """自动纠正并返回 JAVA_HOME"""
    if java_home := os.environ.get("JAVA_HOME"):
        exe = "java.exe" if os.name == "nt" else "java"
        if (Path(java_home) / "bin" / exe).exists():
            return java_home

    if java_bin := shutil.which("java"):
        guessed = str(Path(java_bin).resolve().parent.parent)
        os.environ["JAVA_HOME"] = guessed
        return guessed

    return None

def find_sdk_tools() -> tuple[Path, Path, Path, Path]:
    """定位 aapt2, zipalign, apksigner, android.jar"""
    sdk_root = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not sdk_root and os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if local_appdata:
            sdk_root = Path(local_appdata) / "Android" / "Sdk"

    sdk_path = Path(sdk_root) if sdk_root and Path(sdk_root).exists() else None

    # 寻找最新的 build-tools
    latest_build_tool = None
    if sdk_path and (sdk_path / "build-tools").exists():
        bt_dirs = sorted((sdk_path / "build-tools").iterdir(), reverse=True)
        if bt_dirs:
            latest_build_tool = bt_dirs[0]

    def locate(env_var: str, tool_name: str) -> Path:
        if (val := os.environ.get(env_var)) and Path(val).exists():
            return Path(val)
        if found := shutil.which(tool_name):
            return Path(found)
        if latest_build_tool:
            exts = [".exe", ".bat", ""] if os.name == "nt" else [""]
            for ext in exts:
                cand = latest_build_tool / f"{tool_name}{ext}"
                if cand.exists():
                    return cand
        raise RuntimeError(f"未找到必要工具: {tool_name}，请配置相关环境变量。")

    aapt2 = locate("AAPT2_PATH", "aapt2")
    zipalign = locate("ZIPALIGN_PATH", "zipalign")
    apksigner = locate("APKSIGNER_PATH", "apksigner")

    # 寻找 android.jar
    android_jar = None
    if (jar_env := os.environ.get("ANDROID_JAR_PATH")) and Path(jar_env).exists():
        android_jar = Path(jar_env)
    elif sdk_path and (sdk_path / "platforms").exists():
        platforms = sorted((sdk_path / "platforms").iterdir(), reverse=True)
        for p in platforms:
            cand = p / "android.jar"
            if cand.exists():
                android_jar = cand
                break

    if not android_jar:
        raise RuntimeError("未找到 android.jar，请配置 ANDROID_JAR_PATH。")

    return aapt2, zipalign, apksigner, android_jar

def ensure_debug_keystore() -> Path:
    """确保 debug.keystore 存在"""
    keystore = Path.home() / ".android" / "debug.keystore"
    if keystore.exists():
        return keystore

    keystore.parent.mkdir(parents=True, exist_ok=True)
    keytool = shutil.which("keytool")
    if not keytool and os.environ.get("JAVA_HOME"):
        cand = Path(os.environ["JAVA_HOME"]) / "bin" / ("keytool.exe" if os.name == "nt" else "keytool")
        if cand.exists():
            keytool = str(cand)

    if not keytool:
        raise RuntimeError("缺少 keytool 且未找到 debug.keystore，无法完成签名。")

    cmd = [
        keytool, "-genkeypair", "-v",
        "-keystore", str(keystore),
        "-storepass", "android", "-alias", "androiddebugkey", "-keypass", "android",
        "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
        "-dname", "CN=Android Debug,O=Android,C=US"
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return keystore

# ----------------- 核心构建逻辑 -----------------

def prepare_template():
    """复制模块模板至临时构建目录"""
    if BUILD_TMP_DIR.exists():
        shutil.rmtree(BUILD_TMP_DIR, ignore_errors=True)
    BUILD_TMP_DIR.mkdir(parents=True, exist_ok=True)

    if TEMPLATE_DIR.exists():
        shutil.copytree(TEMPLATE_DIR, BUILD_TMP_DIR, dirs_exist_ok=True)

def generate_module_prop() -> str:
    """生成 module.prop 文件"""
    git_count, git_hash = get_git_info()
    version_code = os.environ.get("VERSION_CODE", git_count)
    env_vname = os.environ.get("VERSION_NAME")
    version_name = env_vname.strip() if env_vname and env_vname.strip() else f"{BASE_VERSION}-{git_hash}"

    build_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    description = f"{MODULE_DESCRIPTION} [构建: {build_time}]"

    lines = [
        f"id={MODULE_ID}",
        f"name={MODULE_NAME}",
        f"version={version_name}",
        f"versionCode={version_code}",
        f"author={MODULE_AUTHOR}",
        f"description={description}"
    ]
    if UPDATE_JSON_URL.strip():
        lines.append(f"updateJson={UPDATE_JSON_URL.strip()}")

    (BUILD_TMP_DIR / "module.prop").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[+] module.prop 生成完成 -> 版本: {version_name} ({version_code})")
    return version_name

def build_overlay_apk():
    """使用 AAPT2 编译 Overlay，对齐并进行 V2 签名"""
    fix_java_env()
    aapt2, zipalign, apksigner, android_jar = find_sdk_tools()

    target_apk_dir = BUILD_TMP_DIR / "files"
    target_apk_dir.mkdir(parents=True, exist_ok=True)

    compiled_zip = OUT_DIR / "compiled.zip"
    unsigned_apk = OUT_DIR / "unsigned.apk"
    aligned_apk = OUT_DIR / "aligned.apk"
    final_apk = target_apk_dir / "WetypeMonet.apk"

    res_dir = SRC_DIR / "res"
    manifest_xml = SRC_DIR / "AndroidManifest.xml"

    # 1. Compile
    print("[1/4] 编译资源 (aapt2 compile)...")
    res = subprocess.run([str(aapt2), "compile", "--dir", str(res_dir), "-o", str(compiled_zip)], 
                         capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if res.returncode != 0:
        raise RuntimeError(f"aapt2 compile 失败:\n{res.stderr or res.stdout}")

    # 2. Link
    print("[2/4] 链接 APK (aapt2 link)...")
    link_cmd = [
        str(aapt2), "link",
        "-I", str(android_jar),
        "--manifest", str(manifest_xml),
        "-o", str(unsigned_apk),
        str(compiled_zip),
        "--auto-add-overlay",
        "--min-sdk-version", "26",
        "--target-sdk-version", "35"
    ]
    res = subprocess.run(link_cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if res.returncode != 0:
        raise RuntimeError(f"aapt2 link 失败:\n{res.stderr or res.stdout}")

    # 3. Zipalign
    print("[3/4] 4 字节对齐 (zipalign)...")
    if aligned_apk.exists():
        aligned_apk.unlink()
    subprocess.run([str(zipalign), "-p", "-f", "4", str(unsigned_apk), str(aligned_apk)], check=True)

    # 4. Sign (V2 only)
    print("[4/4] 使用 Debug Key 进行 V2 签名 (apksigner)...")
    keystore = ensure_debug_keystore()
    if final_apk.exists():
        final_apk.unlink()

    sign_cmd = [
        str(apksigner), "sign",
        "--ks", str(keystore),
        "--ks-pass", "pass:android",
        "--key-pass", "pass:android",
        "--ks-key-alias", "androiddebugkey",
        "--v1-signing-enabled", "false",
        "--v2-signing-enabled", "true",
        "--v3-signing-enabled", "false",
        "--v4-signing-enabled", "false",
        "--out", str(final_apk),
        str(aligned_apk)
    ]
    subprocess.run(sign_cmd, check=True)

    # 5. 清理编译中间文件
    for tmp in [compiled_zip, unsigned_apk, aligned_apk, Path(f"{final_apk}.idsig")]:
        if tmp.exists():
            tmp.unlink()

    print(f"[+] Overlay APK 生成成功: {final_apk}")

def create_module_zip(version_name: str):
    """把 BUILD_TMP_DIR 内容打包为标准的刷机 ZIP 包"""
    print("[*] 正在打包模块 ZIP 文件...")
    zip_filename = f"{MODULE_ID}_{version_name}.zip"
    zip_path = OUT_DIR / zip_filename

    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in BUILD_TMP_DIR.rglob("*"):
            if file_path.is_file():
                # 保持相对路径归档，解压直接为模块根目录
                arcname = file_path.relative_to(BUILD_TMP_DIR)
                zf.write(file_path, arcname)

    print(f"[+] 刷机包制作成功: {zip_path}")

# ----------------- 执行入口 -----------------

if __name__ == "__main__":
    try:
        print("=== 开始构建 Magisk / KernelSU Overlay 模块 ===")
        prepare_template()
        version_name = generate_module_prop()
        build_overlay_apk()
        create_module_zip(version_name)
        print("=== 构建完全成功！===\n")
    except Exception as e:
        print(f"\n[!] 构建失败: {e}")
        sys.exit(1)