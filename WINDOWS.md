# Windows 10/11 使用方法

本项目支持由 Codex 自动下载、初始化并启动，不要求用户手动配置开发环境。

## 直接交给 Codex

在 Windows 版 Codex 中发送：

> 请克隆这个仓库到我的 Documents 目录，然后在仓库根目录运行
> `powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1`。
> 安装缺失依赖、构建工作台和采集插件，并保持工作台运行。

把上面的“这个仓库”替换为实际 GitHub 仓库地址即可。

初始化脚本会通过 `winget` 检查或安装 Git、Python 3.12、Node.js LTS 和 Codex CLI，随后：

1. 创建 `.venv` 并安装 Python 依赖；
2. 从当前源码构建工作台界面，避免回退到旧采集页面；
3. 构建 Edge 采集插件；
4. 启动 `http://127.0.0.1:8765`。

首次安装系统依赖后，如果当前 Codex 终端尚未识别新命令，关闭并重新打开 Codex，再执行同一条命令即可。Ozon 店铺密钥属于本机配置，不包含在 GitHub 源码中。
