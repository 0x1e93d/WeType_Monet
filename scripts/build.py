import os
import shutil
import subprocess
import zipfile
from datetime import datetime

# ==================== 第一步：路径配置 ====================

# 获取当前脚本（比如 build.py）所在的文件夹绝对路径
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

# 获取项目的根目录
PROJECT_ROOT = os.path.dirname(SCRIPTS_DIR)

# 源码与资源目录：存放 res/ 资源和 AndroidManifest.xml
SRC_DIR = os.path.join(PROJECT_ROOT, "src")

# 最终产物输出目录
OUT_DIR = os.path.join(PROJECT_ROOT, "out")

# 临时打包构建目录 (out/build_tmp)
BUILD_TMP_DIR = os.path.join(OUT_DIR, "build_tmp")

# 模块模板目录：存放 META-INF、customize.sh 等模版文件
TEMPLATE_DIR = os.path.join(PROJECT_ROOT, "module_template")


# ==================== 第二步：模块常量配置 ====================

MODULE_ID = "Wetype_Monet"
MODULE_NAME = "微信输入法 Monet"
MODULE_AUTHOR = "酷安@1e93d"
MODULE_DESCRIPTION = "为微信输入法提供 Monet 动态色彩主题。"

BASE_VERSION = "v1.0.0"
UPDATE_JSON_URL = ""


# ==================== 第三步：Git 版本工具函数 ====================

def get_git_commit_count():
    """获取 Git 的总提交次数，用作 versionCode（必须是纯数字且递增）"""
    try:
        count = subprocess.check_output(["git", "rev-list", "--count", "HEAD"], stderr=subprocess.DEVNULL)
        return count.decode("utf-8").strip()
    except Exception:
        return "1"


def get_git_short_hash():
    """获取当前提交的 short hash (如 a1b2c3d)"""
    try:
        git_hash = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL)
        return git_hash.decode("utf-8").strip()
    except Exception:
        return "dev"


# ==================== 第四步：跨平台查找构建工具与环境修复 ====================

def fix_java_environment():
    """检查并自动修复 JAVA_HOME 环境变量，解决 apksigner 依赖 Java 的问题"""
    java_home = os.environ.get("JAVA_HOME")
    exe_name = "java.exe" if os.name == "nt" else "java"

    # 如果当前 JAVA_HOME 路径正常，直接返回
    if java_home and os.path.exists(os.path.join(java_home, "bin", exe_name)):
        return java_home

    # 尝试自动定位 Android Studio 自带的 jbr/JDK
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        r"C:\Program Files\Android\Android Studio\jbr",
        r"C:\Program Files (x86)\Android\Android Studio\jbr",
        os.path.join(local_appdata, "Android", "Sdk", "jbr"),
    ]

    for candidate in candidates:
        if os.path.exists(os.path.join(candidate, "bin", exe_name)):
            os.environ["JAVA_HOME"] = candidate
            print(f"[i] 自动修复 JAVA_HOME 为 Android Studio 环境: {candidate}")
            return candidate

    # 尝试从系统 PATH 中推导
    java_bin = shutil.which("java")
    if java_bin:
        guessed_home = os.path.dirname(os.path.dirname(os.path.realpath(java_bin)))
        os.environ["JAVA_HOME"] = guessed_home
        print(f"[i] 从 PATH 推导并更新 JAVA_HOME: {guessed_home}")
        return guessed_home

    # 若原先路径失效且未找到替代路径，删除无效变量避免批处理脚本直接崩溃
    if "JAVA_HOME" in os.environ and not os.path.exists(os.environ["JAVA_HOME"]):
        del os.environ["JAVA_HOME"]
        
    return None


def ensure_debug_keystore():
    """确保本地存在 debug.keystore，不存在则自动生成"""
    keystore_path = os.path.join(os.path.expanduser("~"), ".android", "debug.keystore")
    if os.path.exists(keystore_path):
        return keystore_path

    os.makedirs(os.path.dirname(keystore_path), exist_ok=True)
    keytool = shutil.which("keytool")
    if not keytool and os.environ.get("JAVA_HOME"):
        cand = os.path.join(os.environ["JAVA_HOME"], "bin", "keytool.exe" if os.name == "nt" else "keytool")
        if os.path.exists(cand):
            keytool = cand

    if not keytool:
        raise RuntimeError("未检测到 keytool 且缺少 debug.keystore，无法签名！")

    print("[i] 正在生成 Debug 签名密钥...")
    cmd = [
        keytool, "-genkeypair", "-v",
        "-keystore", keystore_path,
        "-storepass", "android",
        "-alias", "androiddebugkey",
        "-keypass", "android",
        "-keyalg", "RSA",
        "-keysize", "2048",
        "-validity", "10000",
        "-dname", "CN=Android Debug,O=Android,C=US"
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return keystore_path


def find_sdk_tools():
    """
    自动查找 aapt2, zipalign, apksigner 与 android.jar 路径
    兼容 Windows 本地环境与 GitHub Actions (Ubuntu / macOS / Windows) 环境
    """
    aapt2 = os.environ.get("AAPT2_PATH")
    zipalign = os.environ.get("ZIPALIGN_PATH")
    apksigner = os.environ.get("APKSIGNER_PATH")
    android_jar = os.environ.get("ANDROID_JAR_PATH")

    sdk_root = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not sdk_root:
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
        found = shutil.which(tool_name)
        if found:
            return found
        if latest_build_tool:
            exe_name = f"{tool_name}.exe" if os.name == "nt" else tool_name
            bat_name = f"{tool_name}.bat" if os.name == "nt" else tool_name
            for cand in [os.path.join(latest_build_tool, exe_name), os.path.join(latest_build_tool, bat_name)]:
                if os.path.exists(cand):
                    return cand
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

    return aapt2, zipalign, apksigner, android_jar


# ==================== 第五步：核心逻辑函数 ====================

def copy_module_template(output_dir):
    """准备构建目录：复制 module_template 中的所有模版文件到临时构建目录"""
    # 清理上一次的构建临时目录
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    if os.path.exists(TEMPLATE_DIR):
        print("[i] 正在复制模块模板文件...")
        for item in os.listdir(TEMPLATE_DIR):
            s = os.path.join(TEMPLATE_DIR, item)
            d = os.path.join(output_dir, item)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)


def generate_module_prop(output_dir):
    """生成 module.prop 文件到目标构建目录（覆盖模版中的同名文件）"""
    os.makedirs(output_dir, exist_ok=True)
    target_file_path = os.path.join(output_dir, "module.prop")
    
    git_count = get_git_commit_count()
    git_hash = get_git_short_hash()
    build_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    version_code = os.environ.get("VERSION_CODE", git_count)
    env_version_name = os.environ.get("VERSION_NAME")
    
    if env_version_name and env_version_name.strip():
        version_name = env_version_name.strip()
    else:
        version_name = f"{BASE_VERSION}-{git_hash}"
    
    full_description = f"{MODULE_DESCRIPTION} [构建时间: {build_time}]"
    
    prop_lines = [
        f"id={MODULE_ID}",
        f"name={MODULE_NAME}",
        f"version={version_name}",
        f"versionCode={version_code}",
        f"author={MODULE_AUTHOR}",
        f"description={full_description}"
    ]
    
    if UPDATE_JSON_URL.strip():
        prop_lines.append(f"updateJson={UPDATE_JSON_URL.strip()}")
    
    final_content = "\n".join(prop_lines) + "\n"
    
    with open(target_file_path, "w", encoding="utf-8") as f:
        f.write(final_content)
        
    print(f"[✓] 成功生成 module.prop：")
    print(f"    ├─ 目标路径: {target_file_path}")
    print(f"    ├─ 版本名称: {version_name}")
    print(f"    └─ 版本代码: {version_code}")
    return version_name


def build_overlay_apk():
    """使用 aapt2 编译资源、zipalign 对齐并进行 V2 签名生成 WetypeMonet.apk"""
    fix_java_environment()
    aapt2_exe, zipalign_exe, apksigner_exe, android_jar = find_sdk_tools()
    
    print(f"[i] AAPT2 路径: {aapt2_exe}")
    print(f"[i] ZIPALIGN 路径: {zipalign_exe}")
    print(f"[i] APKSIGNER 路径: {apksigner_exe}")
    print(f"[i] ANDROID_JAR 路径: {android_jar}")

    # 目标输出位置设为 out/build_tmp/files/
    target_apk_dir = os.path.join(BUILD_TMP_DIR, "files")
    os.makedirs(target_apk_dir, exist_ok=True)
    
    compiled_zip = os.path.join(OUT_DIR, "compiled.zip")
    unsigned_apk = os.path.join(OUT_DIR, "unsigned.apk")
    aligned_apk = os.path.join(OUT_DIR, "aligned.apk")
    final_apk = os.path.join(target_apk_dir, "WetypeMonet.apk")
    
    res_dir = os.path.join(SRC_DIR, "res")
    manifest_xml = os.path.join(SRC_DIR, "AndroidManifest.xml")

    # 1. 资源编译 (aapt2 compile)
    print("[1/4] 正在编译资源 (aapt2 compile)...")
    compile_cmd = [
        aapt2_exe, "compile",
        "--dir", res_dir,
        "-o", compiled_zip
    ]
    res_compile = subprocess.run(compile_cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if res_compile.returncode != 0:
        print("\n[!] aapt2 compile 编译错误：")
        print(res_compile.stderr or res_compile.stdout)
        raise RuntimeError("aapt2 compile 失败")

    # 2. 资源链接 (aapt2 link)
    print("[2/4] 正在链接生成 Unsigned APK (aapt2 link)...")
    link_cmd = [
        aapt2_exe, "link",
        "-I", android_jar,
        "--manifest", manifest_xml,
        "-o", unsigned_apk,
        compiled_zip,
        "--auto-add-overlay",
        "--min-sdk-version", "26",
        "--target-sdk-version", "35"
    ]
    res_link = subprocess.run(link_cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if res_link.returncode != 0:
        print("\n" + "=" * 50)
        print("[!] AAPT2 Link 详细报错信息如下：")
        print("=" * 50)
        print(res_link.stderr or res_link.stdout)
        print("=" * 50 + "\n")
        raise RuntimeError("aapt2 link 失败，请检查上方具体的 AAPT2 报错提示。")

    # 3. 4 字节对齐 (zipalign)
    print("[3/4] 正在进行 4 字节对齐 (zipalign)...")
    if os.path.exists(aligned_apk):
        os.remove(aligned_apk)
    align_cmd = [zipalign_exe, "-p", "-f", "4", unsigned_apk, aligned_apk]
    subprocess.run(align_cmd, check=True)

    # 4. APK 签名 (仅使用 V2 签名，使用固定 debug.keystore，禁用 V4 签名以阻止生成 .idsig)
    print("[4/4] 正在使用 Debug Key 进行 V2 签名 (apksigner)...")
    keystore = ensure_debug_keystore()
    if os.path.exists(final_apk):
        os.remove(final_apk)

    sign_cmd = [
        apksigner_exe, "sign",
        "--ks", keystore,
        "--ks-pass", "pass:android",
        "--key-pass", "pass:android",
        "--ks-key-alias", "androiddebugkey",
        "--v1-signing-enabled", "false",
        "--v2-signing-enabled", "true",
        "--v3-signing-enabled", "false",
        "--v4-signing-enabled", "false",
        "--out", final_apk,
        aligned_apk
    ]
    subprocess.run(sign_cmd, check=True)

    # 5. 清理编译中间临时文件
    for temp_file in [compiled_zip, unsigned_apk, aligned_apk]:
        if os.path.exists(temp_file):
            os.remove(temp_file)

    # 清理可能产生的 .idsig 文件
    idsig_file = f"{final_apk}.idsig"
    if os.path.exists(idsig_file):
        os.remove(idsig_file)

    print(f"[✓] Overlay APK 编译并成功完成 V2 签名: {final_apk}")


def create_module_zip(version_name):
    """将 BUILD_TMP_DIR 内的所有内容打包为标准的 Magisk/KernelSU 模块 ZIP 刷机包"""
    print("\n[i] 正在打包模块 Zip 文件...")
    os.makedirs(OUT_DIR, exist_ok=True)
    
    zip_filename = f"{MODULE_ID}_{version_name}.zip"
    zip_path = os.path.join(OUT_DIR, zip_filename)

    if os.path.exists(zip_path):
        os.remove(zip_path)

    # 预压缩格式采用 ZIP_STORED，避免破坏已对齐的 APK 结构并提升打包效率
    NO_COMPRESS_EXTS = ('.apk', '.zip', '.png', '.jpg', '.so')

    with zipfile.ZipFile(zip_path, "w") as zf:
        for root, _, files in os.walk(BUILD_TMP_DIR):
            for file in files:
                abs_file_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_file_path, BUILD_TMP_DIR)
                
                if file.lower().endswith(NO_COMPRESS_EXTS):
                    zf.write(abs_file_path, rel_path, compress_type=zipfile.ZIP_STORED)
                else:
                    zf.write(abs_file_path, rel_path, compress_type=zipfile.ZIP_DEFLATED)

    print(f"[✓] 模块 ZIP 包制作完成：")
    print(f"    └─ 输出路径: {zip_path}")


# ==================== 执行入口 ====================

if __name__ == "__main__":
    try:
        print("=== 开始执行构建流程 ===")
        
        # 1. 初始化 build_tmp 并复制 module_template 框架文件
        copy_module_template(BUILD_TMP_DIR)
        
        # 2. 生成 module.prop 到 out/build_tmp/
        version_name = generate_module_prop(BUILD_TMP_DIR)
        
        # 3. 编译资源并签名生成 WetypeMonet.apk 到 out/build_tmp/files/
        build_overlay_apk()
        
        # 4. 将 out/build_tmp 整体打包为 ZIP 刷机包
        create_module_zip(version_name)
        
        print("\n[✓] 所有构建与打包步骤已顺利完成！\n")
    except Exception as e:
        print(f"\n[!] 构建失败: {e}")