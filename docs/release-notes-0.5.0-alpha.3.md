# Moltskin 0.5.0-alpha.3 内测版（macOS Apple Silicon）

> **这是早期内测版，目前不推荐使用。**
>
> 仅用于内测、技术验证和反馈收集。不要用于日常工作、生产环境或重要账号。后续会持续升级与维护，功能和数据格式都可能变化。

## 下载

- `Moltskin-0.5.0-alpha.3-macOS-arm64.zip`
- SHA-256：`ddbb8c2f82ef0de0a71cd659058c17c900d854da337a077a7454102b2f99f455`

## 适用平台

- macOS 14 及以上
- Apple Silicon（arm64）
- 应用未签名、未公证

## 安装

1. 解压 ZIP，把整个文件夹放到固定位置，例如 `~/Applications/Moltskin`。
2. 右键 `Setup.command` → **打开**。首次运行会创建本地 Python 虚拟环境并安装依赖。
3. 双击 `Start Moltskin.command` 启动管理面板。
4. 可选：双击 `Install Moltskin.command`，在 `~/Applications` 创建启动器。

如果 Gatekeeper 拦截 Chromium：

```bash
xattr -dr com.apple.quarantine "/你的路径/Moltskin-0.5.0-alpha.3-macOS-arm64/Chromium.app"
```

## 本版更新

- Canvas 噪声只扰动文字、路径、渐变和抗锯齿边缘；纯色块、单像素探针、`putImageData` 区域和纯色 `gl.clear()` 保持原样。
- `getImageData`、裁剪读取、`toDataURL`、`toBlob`、`OffscreenCanvas` 与 WebGL 导出改为同一套坐标和 unpremultiplied 像素，解码后逐像素一致。
- 修复 macOS BGRA 读回的 R/B 通道反转，以及 WebGL `readPixels` 与 `toDataURL` 上下翻转错位。
- 音频噪声改为按信号幅值缩放的连续微扰；纯静音、前导静音和 `ConstantSourceNode` 直流信号保持精确值。
- `AnalyserNode` 浮点与字节读数共用同一扰动值，字节结果严格等于浮点读数的量化结果。
- 继续包含 alpha.2 的压缩包安全加固与数据目录 `0700` 权限收紧。

## 本版内容

- Moltskin Chromium 内核 `158.0.8127.88`，补丁系列 `0001`–`0023`
- 指纹能力：屏幕与窗口、平台/UA/Client Hints、WebGL 厂商与参数、Canvas/WebGL 像素噪声、Audio/Analyser 噪声、时区与语言、语音列表、字体与字体 API 过滤、硬件报告限制、`navigator.webdriver` 隐藏
- 工具栏变色龙按钮打开当前环境的工作台，已有工作台标签时直接切回；无会话时回退管理首页
- 管理面板、本地 API、环境导入导出
- 变色龙 App 图标（Dock / Finder）

## 已知限制

- 仅提供 macOS arm64 包；Windows 和 Linux 版本尚未发布
- 应用未签名、未公证，首次打开需要右键 → 打开
- 跨系统画像只用于兼容性测试；生产环境请使用同系统画像
- 内测阶段数据库结构和接口可能调整，重要数据请自行备份
- 暂不提供自动更新

## 数据位置

```text
~/Library/Application Support/Moltskin
```

安装包不包含数据库、环境数据或 API Token。

## 校验

```bash
shasum -a 256 Moltskin-0.5.0-alpha.3-macOS-arm64.zip
```
