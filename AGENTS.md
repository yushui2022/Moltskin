# Moltskin 项目架构与交付计划

本文件面向后续参与项目的 agent 和维护者，记录产品定位、代码边界、平台策略、当前阶段和 Windows 移植计划。修改架构级行为前先读本文件。

## 1. 产品定位

Moltskin 是一个面向社媒运营和电商场景的开源指纹浏览器：

- 一个产品代码库
- 一套 Chromium 补丁
- 按平台构建和发布多个二进制产物
- 同系统画像优先：Windows 仿 Windows、macOS 仿 macOS、Linux 仿 Linux
- 跨系统画像只作为兼容性/测试能力，不承诺与宿主系统完全无痕

跨系统伪装仍会暴露字体、Emoji、语音、字体度量和系统字形回退差异。社媒/电商生产环境默认使用同系统画像。

## 2. 总体架构

```text
web/index.html, web/runtime.html, web/workbench.html
        │
        ▼
moltskin/services/core/main.py       FastAPI、环境 CRUD、导入导出、API
        ├── db.py                    SQLite schema、迁移、事务
        ├── launcher.py              启动、停止、状态回收、会话
        ├── chromium.py              Chromium 启动参数与能力校验
        ├── proxy_bridge.py          每环境本地代理桥（HTTP/SOCKS5 认证）
        ├── secret_store.py          代理密码本地加密
        ├── geoip.py                 出口国家到语言/时区的映射
        ├── config.py                预设、路径、产物配置
        ├── observation.py           Camoufox 实测
        ├── platform_preview.py      Chromium 平台预览
        └── ...
        │
        ├── chromium/                Chromium 补丁、构建、能力探测
        │     ├── patches/0001..0022
        │     ├── native.py
        │     ├── build_linux.py
        │     └── ...
        │
        └── Camoufox 路径           现有 Firefox/Camoufox 引擎与产品能力
```

### 2.1 数据与配置

- SQLite 是唯一产品数据库。
- Chromium 环境字段目前包括：
  - 引擎、内核版本、UA
  - 网站首选语言、时区、启动窗口大小
  - 屏幕预设、平台预设、WebGL 显卡预设
  - 字体、媒体、定位、Canvas、WebGL 调试信息
  - Canvas/Audio 噪声
  - 硬件报告上限、渲染进程、JS 堆
- 导入导出格式当前为 **15**。
- 修改环境字段必须同步检查：
  1. `main.py` 模型
  2. `db.py` 新库 schema 和迁移
  3. 三处 `INSERT INTO profiles`
  4. duplicate
  5. import/export
  6. `PROFILE_FORMAT_VERSION`
  7. API 文档和测试

### 2.2 指纹能力

当前已实现的内核能力：

```text
moltskin-disable-local-font-access
moltskin-block-media-capture
moltskin-block-geolocation
moltskin-block-webgl-debug-info
moltskin-block-canvas-readback
moltskin-audio-noise
moltskin-canvas-noise
moltskin-limit-hardware-reporting
moltskin-locale
moltskin-hide-webdriver
moltskin-platform
moltskin-screen-metrics
moltskin-screen-avail
moltskin-speech-voices
moltskin-webgl-info
moltskin-webgl-params
moltskin-font-filter
moltskin-brand
moltskin-webgpu
moltskin-hardware-profile
moltskin-panel
moltskin-toolbar
moltskin-workbench-button
```

关键补丁：

| 补丁 | 能力 |
|---|---|
| `0009-hide-webdriver` | `navigator.webdriver` 始终为 false |
| `0010-screen-metrics` | 屏幕尺寸、色深、DPR |
| `0011-platform` | `navigator.platform`、UA、Client Hints |
| `0012-webgl-info` | 未遮蔽 WebGL 厂商/型号 |
| `0013-canvas-noise` | Canvas/WebGL 稳定像素噪声 |
| `0014-audio-noise` | 离线音频和解码音频噪声 |
| `0015-analyser-noise` | AnalyserNode 浮点/字节频域和时域噪声 |
| `0016-locale` | 网站语言同步 Intl/ICU 区域 |
| `0017-speech-voices` | 跨系统 Windows 画像的稳定语音列表 |
| `0018-screen-avail` | 独立 `availWidth`/`availHeight` |
| `0019-font-filter` | Windows 跨系统 CSS 字体白名单 |
| `0020-webgl-params` | Windows 跨系统 WebGL 参数/扩展画像 |
| `0021-font-api-filter` | `document.fonts.check` / `FontFace(local)` 过滤 |
| `0022-canvas-noise-stealth` | 跳过单像素读数探针 |
| `0023-workbench-toolbar` | 工具栏按钮打开当前会话工作台 |
| `0024-canvas-noise-hardening` | 纯色探针、putImageData 与跨 API 像素一致 |
| `0025-audio-noise-hardening` | 静音/直流保护与 Analyser Float/Byte 一致 |
| `0026-web-font-filter` | 保留远程 Web Font，只过滤本地系统字体 |
| `0027-chrome-brand-and-gpu-profile` | Chrome 品牌、WebGPU 与 WebGL2 画像 |
| `0028-hardware-and-anticorrelation` | 硬件参数、配额、电池、音频与设备防关联 |

### 2.3 平台差异边界

Chromium 二进制本身必须按平台分别构建。产品代码不应复制成三套。

目标平台适配层：

```text
platforms/
  __init__.py
  base.py       统一接口
  macos.py      .app、Keychain、POSIX 进程、macOS 打包
  linux.py      chrome、POSIX 进程、tar/deb 打包
  windows.py    chrome.exe、Job Object、DPAPI、zip/安装器
```

统一接口至少包括：

- 查找 Chromium 可执行文件
- 启动进程
- 停止进程树
- 判断进程是否存活
- 时区传递
- 面板安装入口
- 打包/发布路径

现有代码中的 `sys.platform` 判断只能作为过渡，不应继续扩散到业务层。

## 3. 当前支持状态

| 项目 | macOS | Linux | Windows |
|---|---|---|---|
| Chromium 构建 | 已构建并实测 | 已构建并实测 | 未支持 |
| 产品启动器 | 已支持 | 已支持 | 未支持 |
| 面板/工具栏 | 已支持 | 已支持 | 待编译验证 |
| 同系统画像 | 支持 | 支持 | 计划中 |
| 跨系统 Windows 画像 | 部分支持 | 部分支持 | 不适用 |

当前 `chromium.prepare_launch()` 明确拒绝 `win32`。Windows 构建产物不能直接用于 macOS，macOS `.app` 也不能在 Windows 运行。

## 4. 当前阶段：仓库健壮性

Windows 编译暂时不做。先把仓库质量和跨平台边界做好，再启动 Windows 构建。

### 4.1 P0：数据与生命周期

- [ ] SQLite 迁移幂等、可回滚、字段约束完整
- [ ] 所有 profile INSERT/UPDATE/duplicate/import/export 字段守恒
- [ ] 导入导出版本升级和旧版本兼容测试
- [ ] 启动预留、单实例锁、PID 复用、崩溃回收
- [ ] 停止 Chromium 时优雅退出并清理进程树
- [ ] 环境目录权限、符号链接、路径穿越防护
- [ ] 备份/恢复覆盖 Chromium 及 Camoufox 数据边界

### 4.2 P0：平台抽象

- [ ] 抽出 `platforms/base.py` 接口
- [ ] macOS/Linux 行为迁移到适配器，不改变现有行为
- [ ] 去掉业务层重复的 `sys.platform` 分支
- [ ] Windows 适配器只实现接口，不复制产品逻辑
- [ ] 为平台无关行为补单元测试

### 4.3 P0：网络与代理

- [ ] 代理密码不明文泄漏到日志/API
- [ ] 固定代理、WebRTC、DNS 的边界行为验收
- [ ] 代理地区、时区、语言的一致性提示
- [ ] 代理断开时禁止静默回退系统网络
- [ ] WebRTC 候选地址策略明确（当前仅非代理 UDP 限制，不等同地址替换）

### 4.4 P1：指纹一致性

- [ ] 同系统画像默认不启用跨系统字体/语音/WebGL 辅助项
- [ ] 屏幕工作区按平台区分（Windows/Linux 任务栏，macOS 菜单栏/Dock）
- [ ] 字体 API、Canvas、WebGL、Audio 的稳定性回归
- [ ] 文档明确说明跨系统 Emoji、字体度量、ClientRects 仍可能泄漏
- [ ] 所有噪声开关都有稳定性和可检测性测试

### 4.5 P1：工程与发布

- [ ] 单元测试与原生浏览器测试分层
- [ ] CI 在 macOS/Linux 上运行基础测试
- [ ] 发布产物带平台标识和校验和
- [ ] CHANGELOG、README、API 文档与行为同步
- [ ] 安全报告入口和依赖更新流程

## 5. Windows 阶段计划（后续）

Windows 不是“复制一份代码”，而是：

1. 同一 Chromium commit `0cefb18236a23db1ebb6b2bc040fbbaf8cbe759e`
2. 同一补丁系列 `0001..0022`
3. Windows x64 原生编译
4. 新增 `platforms/windows.py`
5. 新增 `chromium/build_windows.py`
6. 完整运行目录打包
7. Windows 实机验收

### 5.1 构建环境

- Windows 10/11 x64
- Visual Studio 2022，Desktop development with C++
- Windows 11 SDK
- depot_tools、Python 3、Git
- 推荐 32 GB 内存、8 核以上、100 GB 以上空闲磁盘
- 不建议依赖 GitHub 托管 runner 做完整 Chromium 构建；使用自托管 Windows runner 或专用机器

### 5.2 Windows 适配点

- `chrome.exe` 路径探测
- Job Object / `taskkill /T` 进程树管理
- Windows 时区传递和 Chromium 参数
- DPAPI 替代 Keychain
- 面板/工具栏补丁的 Windows 编译验证
- 安装器/快捷方式
- 完整运行目录打包（`chrome.exe`、DLL、PAK、locales、icudtl.dat 等）

### 5.3 Windows 发布产物

```text
Moltskin-Windows-x64.zip
```

包内是完整 Chromium 运行目录，不是只有 `chrome.exe`。

## 6. 构建与测试命令

### 普通单元测试

```bash
cd Moltskin
python -m pytest -q
```

### 原生 Chromium 回归

```bash
cd Moltskin
MOLTSKIN_TEST_NATIVE_BROWSERS=1 \
MOLTSKIN_CHROMIUM_BINARY="$HOME/chromium-baseline/src/out/MoltskinHardwarePrivacy/Chromium.app/Contents/MacOS/Chromium" \
python -m pytest -q
```

### 增量构建 Chromium

```bash
cd "$HOME/chromium-baseline/src"
export PATH="$HOME/depot_tools:$PATH"
autoninja -C out/Moltskin -j 12 chrome
```

### 验证补丁序列

```bash
cd Moltskin
python - <<'PY'
from pathlib import Path
from chromium.build_linux import apply_series, MODULE
apply_series(Path.home() / "chromium-baseline" / "src",
             sorted((MODULE / "patches").glob("*.patch")))
PY
```

## 7. Agent 工作约定

- 不创建 `mac/`、`windows/`、`linux/` 三套完整产品代码。
- 不按平台分叉 Git 分支。
- 平台差异只进入适配层、构建脚本、打包脚本和条件编译补丁。
- 修改产品字段时同时更新 DB、API、导入导出、UI、文档和测试。
- 修改内核行为时同时更新补丁、能力列表、启动器和真实浏览器回归。
- 跨系统能力默认不污染同系统画像。
- 不把生成的 Chromium 二进制提交进仓库。
- 大改动后运行单元测试和原生回归，并在最终回复中报告真实结果。
- 当前阶段优先修复健壮性，不提前开始 Windows 编译。
