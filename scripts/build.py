
import os

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




