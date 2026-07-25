
import os
import subprocess
from datetime import datetime

# 获取当前脚本（比如 build.py）所在的文件夹绝对路径
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
# 获取项目的根目录
PROJECT_ROOT = os.path.dirname(SCRIPTS_DIR)
# 源码与资源目录：存放 res/ 资源和 AndroidManifest.xml
# 路径：WeType_Gboard/src
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
# 最终产物输出目录：用来存放编译出来的 apk 和打包好的 zip 刷机包
# 路径：WeType_Gboard/out
OUT_DIR = os.path.join(PROJECT_ROOT, "out")
# 临时打包构建目录：放在 out 目录里面
# 路径：WeType_Gboard/out/build_tmp
BUILD_TMP_DIR = os.path.join(OUT_DIR, "build_tmp")
# 模块模板目录：存放 META-INF、customize.sh、module.prop.template 等模版文件
# 路径：WeType_Gboard/module_template
TEMPLATE_DIR = os.path.join(PROJECT_ROOT, "module_template")

# 模块 module.prop 基础配置常量
MODULE_ID = "Wetype_Monet"
MODULE_NAME = "微信输入法 Monet"
MODULE_AUTHOR = "酷安@1e93d"
MODULE_DESCRIPTION = "为微信输入法提供 Monet 动态色彩主题。"
BASE_VERSION = "v1.0.0"
UPDATE_JSON_URL = ""

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

def generate_module_prop(output_path):
    """直接在 Python 中拼接并生成 module.prop 文件"""
    
    # 1. 计算动态变量
    version_code = get_git_commit_count()                  # 自动递增的版本代码，如: 25
    git_hash = get_git_short_hash()                        # Git Hash 缩写，如: a1b2c3d
    build_time = datetime.now().strftime("%Y-%m-%d %H:%M")  # 打包时间
    
    # 2. 组合最终展示的版本名，格式如: v1.0.0-a1b2c3d
    version_name = f"{BASE_VERSION}-{git_hash}"
    
    # 3. 组合 description 描述（加上构建时间，方便在 Magisk/KernelSU 界面看安装的是哪次刷机包）
    full_description = f"{MODULE_DESCRIPTION} [构建时间: {build_time}]"
    
    # 4. 按顺序组装 module.prop 的每一行内容
    prop_lines = [
        f"id={MODULE_ID}",
        f"name={MODULE_NAME}",
        f"version={version_name}",
        f"versionCode={version_code}",
        f"author={MODULE_AUTHOR}",
        f"description={full_description}"
    ]
    
    # 5. 如果配置了更新链接，则追加 updateJson 行
    if UPDATE_JSON_URL.strip():
        prop_lines.append(f"updateJson={UPDATE_JSON_URL.strip()}")
    
    # 6. 把多行文本用换行符拼接起来
    final_content = "\n".join(prop_lines) + "\n"
    
    # 7. 写入到目标文件 (例如 build_tmp/module.prop)
    # utf-8 编码可以确保中文（如“微信输入法 Monet”）不会变成乱码
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(final_content)
        
    print(f"[✓] 成功生成 module.prop：")
    print(f"    ├─ 版本名称: {version_name}")
    print(f"    ├─ 版本代码: {version_code}")
    print(f"    └─ 目标路径: {output_path}")

# ==================== 测试调用 ====================
if __name__ == "__main__":
    # 测试生成到当前目录下
    generate_module_prop("module.prop")