# WeType Monet

为安卓版微信输入法提供 Monet 动态色彩 Overlay 的 Magisk/KernelSU 模块。

## 安装

1. 在 [Releases](https://github.com/0x1e93d/WeType_Monet/releases) 下载最新的模块 ZIP。
2. 使用 Magisk 或 KernelSU 安装 ZIP。

需要 Android 14 及以上和已启用的 Magisk 或 KernelSU 环境。

## 发布产物

- `Wetype_Monet_*.zip`：用于 Magisk/KernelSU 的 Overlay 模块。
- `版本号(版本代码).json`：该微信输入法版本的资源映射结果。

Release 页面会记录模块版本、发布序号、适配的微信输入法版本和本次提交摘要。

## 自动构建

GitHub Actions 每天北京时间 06:00 检查微信输入法更新，也会在核心文件提交到 `main` 时触发。


## 推荐搭配

- [WeType-Swipe](https://github.com/waoui/WeType-Swipe)：LSPosed 模块，为微信输入法的 26 键和九宫格提供可配置的按键下滑快捷操作，例如全选、剪切、复制和粘贴。
