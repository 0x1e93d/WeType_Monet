# WeType Monet

为安卓版微信输入法提供 Monet 动态色彩的 Magisk/KernelSU Overlay 模块，以及保持原包名的独立 Monet 安装包。

## 安装

### Magisk/KernelSU Overlay 模块

1. 在 [Releases](https://github.com/0x1e93d/WeType_Monet/releases) 下载最新的 `Wetype_Monet_vN.zip`。
2. 使用 Magisk 或 KernelSU 安装 ZIP。
3. 重启设备后使用微信输入法。

需要 Android 14 及以上和已启用的 Magisk 或 KernelSU 环境。Overlay 模块依赖已安装的官方微信输入法。

### 独立 Monet APK

`Wetype_Monet_<版本名称>(<版本号>)_vN.apk` 保留官方包名 `com.tencent.wetype`，但使用本项目的公开发布签名，因此不能与腾讯官方微信输入法共存。

1. 首次安装前，先备份需要保留的输入法数据。
2. 卸载当前官方微信输入法。
3. 安装 Release 中对应版本的 Monet APK。
4. 后续更新直接安装新的 Monet APK；必须持续使用本项目发布的同一签名版本。

如需回到官方版本，先卸载 Monet APK，再安装同一 Release 提供的官方原始 APK，或从微信输入法官网下载官方安装包。

## 发布产物

- `Wetype_Monet_vN.zip`：用于 Magisk/KernelSU 的 Overlay 模块。
- `Wetype_<版本名称>(<版本号>).apk`：构建时下载并归档的官方微信输入法原始安装包。
- `Wetype_Monet_<版本名称>(<版本号>)_vN.apk`：写入 Monet 资源、保持 `com.tencent.wetype` 包名并使用公开发布签名的独立安装包。
- `wetype_monet.json`：KernelSU/Magisk 在线更新清单，会在每次成功发布后更新。
- `config/targets/<版本名称>(<版本号>).json`：该微信输入法版本的资源映射结果，仅保存于仓库，不作为 Release 附件。

Release 页面会记录模块版本、适配的微信输入法版本和本次提交摘要。

## 自动构建

GitHub Actions 每天北京时间 06:00 检查微信输入法更新，也会在核心文件提交到 `main` 时触发。上游 APK 或 `config/base.json` 的有效内容发生变化时，流水线会递增模块版本，并生成对应的 ZIP、官方 APK 归档和 Monet APK。

## 推荐搭配

- [WeType-Swipe](https://github.com/waoui/WeType-Swipe)：LSPosed 模块，为微信输入法的 26 键和九宫格提供可配置的按键下滑快捷操作，例如全选、剪切、复制和粘贴。
