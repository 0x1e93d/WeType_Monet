
import os
import subprocess
from datetime import datetime

# 获取当前脚本（比如 build.py）所在的文件夹绝对路径
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
# 获取项目的根目录
PROJECT_ROOT = os.path.dirname(SCRIPTS_DIR)
# 源码与资源目录：存放 res/ 资源和 AndroidManifest.xml
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
# 最终产物输出目录：用来存放编译出来的 apk 和打包好的 zip 刷机包
OUT_DIR = os.path.join(PROJECT_ROOT, "out")
# 临时打包构建目录：放在 out 目录里面 (WeType_Gboard/out/build_tmp)
BUILD_TMP_DIR = os.path.join(OUT_DIR, "build_tmp")
# 模块模板目录：存放 META-INF、customize.sh 等模版文件
TEMPLATE_DIR = os.path.join(PROJECT_ROOT, "module_template")

# 块 module.prop 基础配置常量

MODULE_ID = "Wetype_Monet"
MODULE_NAME = "微信输入法 Monet"
MODULE_AUTHOR = "酷安@1e93d"
MODULE_DESCRIPTION = "为微信输入法提供 Monet 动态色彩主题。"
# 默认基础版本号
BASE_VERSION = "v1.0.0"
# 预留更新链接
UPDATE_JSON_URL = ""


# Git 版本工具函数

def get_git_commit_count():
    """获取 Git 的总提交次数，用作 versionCode（必须是纯数字且递增）"""
    try:
        count = subprocess.check_output(["git", "rev-list", "--count", "HEAD"], stderr=subprocess.DEVNULL)
        return count.decode("utf-8").strip()
    except Exception:
        return "1"  # 如果没安装 git 或不在 git 仓库中，默认回退为 1


def get_git_short_hash():
    """获取当前提交的 short hash (如 a1b2c3d)，用来区分每次打包的微小修改"""
    try:
        git_hash = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL)
        return git_hash.decode("utf-8").strip()
    except Exception:
        return "dev"


# 生成 module.prop 逻辑

def generate_module_prop(output_dir):
    """
    直接在 Python 中拼接并生成 module.prop 文件
    :param output_dir: 输出的文件夹路径（如 BUILD_TMP_DIR）
    """
    # 1. 确保目标文件夹存在（如果 out/build_tmp 不存在，Python 会自动递归创建）
    os.makedirs(output_dir, exist_ok=True)
    
    # 2. 拼接最终输出的文件绝对路径
    target_file_path = os.path.join(output_dir, "module.prop")
    
    # 3. 计算动态变量
    git_count = get_git_commit_count()                       # Git 提交总数
    git_hash = get_git_short_hash()                           # Git Commit Hash 缩写
    build_time = datetime.now().strftime("%Y-%m-%d %H:%M")   # 构建时间戳
    
    # 4. 版本代码 (versionCode) 逻辑：
    # 优先使用 Actions 传入的环境变量，如果没有就使用 git 提交次数
    version_code = os.environ.get("VERSION_CODE", git_count)
    
    # 5. 版本名称 (version) 逻辑：
    # 优先使用 Actions 传入的环境变量（比如发布 Tag v1.1.0 时传入），否则拼接为 v1.0.0-a1b2c3d
    env_version_name = os.environ.get("VERSION_NAME")
    if env_version_name and env_version_name.strip():
        version_name = env_version_name.strip()
    else:
        version_name = f"{BASE_VERSION}-{git_hash}"
    
    # 6. 组合描述内容（带打包时间戳）
    full_description = f"{MODULE_DESCRIPTION} [构建时间: {build_time}]"
    
    # 7. 组装 module.prop 行内容
    prop_lines = [
        f"id={MODULE_ID}",
        f"name={MODULE_NAME}",
        f"version={version_name}",
        f"versionCode={version_code}",
        f"author={MODULE_AUTHOR}",
        f"description={full_description}"
    ]
    
    # 如果配置了更新链接，则追加 updateJson 行
    if UPDATE_JSON_URL.strip():
        prop_lines.append(f"updateJson={UPDATE_JSON_URL.strip()}")
    
    # 8. 换行符拼接并写入文件
    final_content = "\n".join(prop_lines) + "\n"
    
    with open(target_file_path, "w", encoding="utf-8") as f:
        f.write(final_content)
        
    print(f"[✓] 成功生成 module.prop：")
    print(f"    ├─ 目标路径: {target_file_path}")
    print(f"    ├─ 版本名称: {version_name}")
    print(f"    └─ 版本代码: {version_code}")


# ==================== 执行入口 ====================

if __name__ == "__main__":
    # 传入 BUILD_TMP_DIR 文件夹路径，自动生成 module.prop 到 out/build_tmp/ 目录下
    generate_module_prop(BUILD_TMP_DIR)