# Moltskin 0.7.0-alpha.2 内测版（macOS Apple Silicon）

> **这是早期内测版，目前不推荐使用。**
>
> 仅用于内测、技术验证和反馈收集。不要用于日常工作、生产环境或重要账号。后续会持续升级与维护，功能和数据格式都可能变化。

## 下载

- `Moltskin-0.7.0-alpha.2-macOS-arm64.zip`
- SHA-256：`12ed5ff3e8cdbd2ad86c4f823ed163d77f16c6e492267c188bd14586f1dbff9b`

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
xattr -dr com.apple.quarantine "/你的路径/Moltskin-0.7.0-alpha.2-macOS-arm64/Chromium.app"
```

## 本版更新

- 修正 Chrome 品牌 GREASE 排列和旧版 UA 版本一致性。
- 修正 Windows ANGLE shader 精度表和 WebGL2 uniform components 规范关系。
- 修正 WebGPU Apple/NVIDIA/AMD 架构字符串，并清除宿主驱动描述。
- 品牌补丁 `0027`：UA、`navigator.userAgentData.brands`、`Sec-CH-UA` 与 `fullVersionList` 统一出现 `Google Chrome`，自定义 Chrome UA 时版本同步；Safari/Firefox UA 不伪造。
- 内部包启用 H.264/AAC 编解码，恢复 `video.canPlayType` 与 `MediaSource.isTypeSupported` 的 Chrome 行为。
- WebGPU `adapter.info` 与 WebGL 显卡预设联动，避免 WebGL/WebGPU 互相矛盾。
- WebGL2 补齐 3D/数组纹理、多重采样、UBO、颜色附件、线宽/点大小范围和 shader 精度画像。
- 字体过滤补丁 `0026`：远程 `@font-face`（Inter、Roboto、FontAwesome 等）和图标字体不再被跨系统 Windows 字体的白名单误杀；`PingFang`、`Helvetica` 等本地系统字体仍不可探测。
- Chromium 支持带用户名/密码的 HTTP、HTTPS、SOCKS5 代理。每个窗口使用本地回环代理桥完成上游认证，密码不进入浏览器命令行；上游失败时返回代理错误，不会直连回退。
- 代理检测后自动补齐空白环境的出口时区和网站首选语言，环境编辑器新增「按代理对齐」按钮。
- 代理密码使用本地 `0600` 主密钥加密后写入 SQLite；旧明文密码会在启动时迁移，API、日志和导出不返回明文；备份携带密钥并在恢复时用目标机器密钥重新加密。
- 继续包含 `0.5.0-alpha.3` 的 Canvas/Audio 噪声加固，以及 alpha.2 的压缩包安全加固与数据目录 `0700` 权限。

## 本版内容

- Moltskin Chromium 内核 `158.0.8127.88`，补丁系列 `0001`–`0027`
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
shasum -a 256 Moltskin-0.7.0-alpha.2-macOS-arm64.zip
```
