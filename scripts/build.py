import os
import shutil
import subprocess
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

# 临时打包构建目录 (WeType_Gboard/out/build_tmp)
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


# ==================== 第四步：跨平台查找 aapt2 与 android.jar ====================

def find_aapt2_and_jar():
    """
    自动查找 aapt2 和 android.jar 路径
    兼容 Windows 本地环境与 GitHub Actions (Ubuntu / macOS / Windows) 环境
    """
    # 1. 优先允许环境变量直接指定
    aapt2 = os.environ.get("AAPT2_PATH")
    android_jar = os.environ.get("ANDROID_JAR_PATH")

    if aapt2 and android_jar and os.path.exists(aapt2) and os.path.exists(android_jar):
        return aapt2, android_jar

    # 2. 获取 Android SDK 根路径
    # GitHub Actions 会预装 SDK 并自动提供 ANDROID_HOME 或 ANDROID_SDK_ROOT
    sdk_root = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not sdk_root:
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if local_appdata:
            sdk_root = os.path.join(local_appdata, "Android", "Sdk")

    # 3. 查找 aapt2 可执行程序
    if not aapt2:
        # 优先从系统 PATH 中寻找
        aapt2 = shutil.which("aapt2")
        
        # 若 PATH 未找到，去 build-tools 检索最高版本的 aapt2
        if not aapt2 and sdk_root and os.path.exists(os.path.join(sdk_root, "build-tools")):
            build_tools_dir = os.path.join(sdk_root, "build-tools")
            versions = sorted(os.listdir(build_tools_dir), reverse=True)
            exe_name = "aapt2.exe" if os.name == "nt" else "aapt2"
            
            for ver in versions:
                candidate = os.path.join(build_tools_dir, ver, exe_name)
                if os.path.exists(candidate):
                    aapt2 = candidate
                    break

    # 4. 查找 android.jar
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
    if not android_jar or not os.path.exists(android_jar):
        raise RuntimeError("未找到 android.jar！请确认已安装 Android SDK 或手动配置 ANDROID_JAR_PATH 环境变量。")

    return aapt2, android_jar


# ==================== 第五步：核心逻辑函数 ====================

def generate_module_prop(output_dir):
    """生成 module.prop 文件到目标构建目录"""
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


def build_overlay_apk():
    """使用 aapt2 编译资源并生成 WetypeMonet.apk 到 out/build_tmp/files/ 目录下"""
    aapt2_exe, android_jar = find_aapt2_and_jar()
    print(f"[i] AAPT2 路径: {aapt2_exe}")
    print(f"[i] ANDROID_JAR 路径: {android_jar}")

    # 目标输出位置设为 out/build_tmp/files/
    target_apk_dir = os.path.join(BUILD_TMP_DIR, "files")
    os.makedirs(target_apk_dir, exist_ok=True)
    
    compiled_zip = os.path.join(OUT_DIR, "compiled.zip")
    unsigned_apk = os.path.join(target_apk_dir, "WetypeMonet.apk")
    
    res_dir = os.path.join(SRC_DIR, "res")
    manifest_xml = os.path.join(SRC_DIR, "AndroidManifest.xml")

    # 1. 资源编译 (aapt2 compile)
    print("[1/2] 正在编译资源 (aapt2 compile)...")
    compile_cmd = [
        aapt2_exe, "compile",
        "--dir", res_dir,
        "-o", compiled_zip
    ]
    subprocess.run(compile_cmd, check=True)

    # 2. 资源链接 (aapt2 link)
    print("[2/2] 正在链接生成 Overlay APK (aapt2 link)...")
    link_cmd = [
        aapt2_exe, "link",
        "-I", android_jar,
        "--manifest", manifest_xml,
        "-o", unsigned_apk,
        compiled_zip,
        "--auto-add-overlay"
    ]
    subprocess.run(link_cmd, check=True)

    # 3. 清理编译临时文件
    if os.path.exists(compiled_zip):
        os.remove(compiled_zip)

    print(f"[✓] Overlay APK 生成成功: {unsigned_apk}")


# ==================== 执行入口 ====================

if __name__ == "__main__":
    try:
        print("=== 开始执行构建流程 ===")
        # 1. 生成 module.prop 到 out/build_tmp/
        generate_module_prop(BUILD_TMP_DIR)
        
        # 2. 编译资源并生成 WetypeMonet.apk 到 out/build_tmp/files/
        build_overlay_apk()
        
        print("\n[✓] 所有构建步骤已顺利完成！构建文件置于 out/build_tmp\n")
    except Exception as e:
        print(f"\n[!] 构建失败: {e}")