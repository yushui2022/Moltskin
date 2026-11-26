# Moltskin

<p align="center"><img src="docs/logo-wordmark.svg" width="320" alt="Moltskin"></p>

[English](README.md)

Moltskin 是可自行部署的多环境指纹浏览器。每个环境有独立的数据目录。**0.4.1 alpha** 使用 Camoufox（Firefox）提供指纹预设和独立代理。当前开发分支也可启动自编 Chromium 预览版，按环境管理网站权限。[更新记录](CHANGELOG.md) · [快速开始](#快速开始)

## Chromium 开发路线

双击 Mac 上的 Moltskin，先进入独立的管理窗口。在「浏览器窗口」中点击某个环境的「打开」，面板显示启动状态，随后可在该环境自己的浏览器中使用标签页和地址栏。默认设置下，Chromium 的首个标签页显示该窗口的工作台。

创建环境时可以选择 Camoufox 或 Chromium，并在 Chromium 环境中分别限制 Canvas 2D 读取、WebGL 显卡调试信息、网站可见的 CPU 并发数与设备内存、本机字体枚举、摄像头与麦克风采集以及网站定位；还可按窗口设置屏幕、平台、WebGL 显卡，以及 Canvas/WebGL 像素噪声和 AudioContext 指纹噪声。在该窗口打开「查看当前浏览器实测值」可以核对图形隐私设置的效果。重启后沿用该环境的内核和数据目录。Chromium 已在 Apple Silicon 和 Ubuntu x86-64 上运行。在专用 Ubuntu 主机上，单个 SSH 用户可从自己电脑的浏览器打开和操作 Linux Chromium 窗口，断线后可重连仍在运行的会话。[部署 Linux 网页远程窗口](chromium/remote/README.md) · [构建与使用 Chromium 预览版](chromium/README.md) · [逐项指纹能力对照](chromium/fingerprint-coverage.md)

| 里程碑 | 交付时怎样验证 |
|---|---|
| 自编内核 | 在 Linux x86-64 和 macOS arm64 上编译同一份上游源码。两端都能从空环境启动，产物可核对版本和校验值。 |
| 双内核共存 | 新建环境时可在面板选择内核。已有 Camoufox 环境保留原数据，两个 Chromium 环境可以独立运行和重启。 |
| 第一项内核隐私设置 | 用同一个测试页验证两个环境各自的字体、音视频采集和定位权限。交换策略并重启，分别核对网页的读取或采集结果。 |
| 网页兼容性与环境隔离 | 核对开启字体限制后网页字体的加载、回退和排版。在同一站点验证两个环境的本地存储、IndexedDB 与缓存，重启并清除其中一个环境的测试数据后，另一个仍能读回自己的数据。 |
| Linux 云端使用 | 在 Linux 云节点验证两位成员可以分别操控各自的会话，并测试断线重连和撤权。 |
| 跟进上游发布 | 跟踪 Chromium Beta 与 Stable，在升级分支应用补丁、编译双平台，验证环境升级和恢复，再发布稳定版。 |

准备团队试用时，可查阅[企业版能力差距与验收建议](docs/enterprise-readiness.zh-CN.md)，核对 Chromium 环境的网络配置、成员授权、远程操作和数据恢复范围。

**计划中的 GitHub 机器人。** 有人提交普通 Bug 时，机器人会追问缺失信息，或复现问题并提交带回归测试的修复草稿 PR。有人新建或更新 PR 时，机器人会报告验证的提交、修复前失败的用例和修复后的结果，由维护者审核合并。[参与贡献](CONTRIBUTING.md)

## 快速开始

### Docker（推荐）

```bash
git clone https://github.com/yushui2022/Moltskin.git
cd Moltskin
docker compose up -d
```

打开 <http://127.0.0.1:8765>。首次构建会下载 Camoufox 浏览器（约 1.3GB）。

### 本地运行

需要 Python 3.10+ 和 [uv](https://github.com/astral-sh/uv)。

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/camoufox fetch
.venv/bin/python run.py
```

浏览器打开 <http://127.0.0.1:8765>。

## 与 Camoufox 的关系

Camoufox 环境使用以下公开组件来启动浏览器、生成指纹和处理代理地理定位。

- `Camoufox()` / `persistent_context`：启动与持久化
- `camoufox.ip.Proxy` / `proxy_exit_geo`：代理出口 IP 与时区
- `camoufox.geolocation.get_geolocation`：国家、语言、经纬度
- `camoufox.multiversion.list_installed`：内核版本

在 0.4.1 中，Moltskin 负责环境管理、进程监管、本地 API、界面和数据库。

## 当前能力

- 创建和复制环境。面板中移入回收站的环境会保留本地浏览器数据，可单独恢复或永久删除。
- 一键启动 Camoufox（持久化 profile，登录态保留）
- Camoufox 环境创建时保存指纹预设，后续启动复用同一份预设
- Camoufox 环境详情并排显示预设值与最近一次启动的浏览器实测值，可核对 CPU 核数、屏幕、UA 和 WebGL 等字段。缺少读数时显示「未验证」。
- Camoufox 的 CreepJS 指纹自检和截图
- 分组、标签、批量打开/关闭/删除/移动
- 环境配置可全部导出，也可勾选后单独导出。导入结果列出重名后的名称，以及每条跳过记录的原因。
- MCP Server（15 个工具）
- Python SDK
- 一键关闭，状态自动回收
- 在「代理 IP → 批量导入」粘贴列表或选择 TXT、CSV 文件。预览按行标出重复项和错误，每次可导入最多 500 条有效代理。代理池支持检测出口 IP、国家、时区和延迟。
- Chromium 窗口可选系统网络，也可绑定代理库中未设置凭据的 HTTP、HTTPS 或 SOCKS5 代理。重启保留绑定。变更窗口的网络配置前需先关闭窗口。固定代理时禁用非代理 WebRTC UDP。[网络设置与适用范围](chromium/README.md#窗口代理)
- Chromium 内核补丁隐藏 `navigator.webdriver`，即使通过 Playwright/CDP 启动也返回 `false`；屏幕尺寸/像素比预设同步 `screen`、`devicePixelRatio` 和媒体查询；平台预设同步 `navigator.platform`、UA 和 Client Hints；网站首选语言同时同步 `Intl` 默认区域；WebGL 显卡预设覆盖调试扩展读取的厂商和型号；Canvas 与 WebGL 像素噪声为每个环境生成稳定且互不相同的微小颜色扰动；AudioContext 指纹噪声对离线渲染、解码音频和 AnalyserNode 读数加入听感不可闻的稳定扰动。[实机回归](tests/test_chromium_webdriver.py) · [屏幕回归](tests/test_chromium_screen.py) · [平台回归](tests/test_chromium_platform_settings.py) · [显卡回归](tests/test_chromium_webgl_info.py) · [像素噪声回归](tests/test_chromium_canvas_noise.py) · [音频噪声回归](tests/test_chromium_audio_noise.py)
- 环境列表、搜索、状态筛选
- 单个或批量创建时可选「管理首页（全部环境）」「空白页」或「沿用默认设置」，也可禁止加载网页图片。两个内核均支持，修改在下次启动时生效，配置可随环境导出。
- 并发上限、无头模式
- 本地 API Token 鉴权
- 默认情况下，有头 Chromium 打开该窗口自己的工作台，显示出口 IP、环境信息和浏览器实测值。无头 Chromium 打开空白页，Camoufox 遵循面板的启动设置。
- 启动时清理孤儿浏览器进程
- 后台定时巡检过期代理
- CI 冒烟测试

## 目录

```
Moltskin/
├── moltskin/             # Python 包
│   ├── services/core/    # FastAPI、启动器、Runner、代理检测
│   └── client.py         # Python SDK
├── web/index.html        # 管理界面
├── tests/                # 测试
├── run.py                # 启动入口
└── data/                 # 运行时（数据库、profile、日志、token）
```

## 测试

```bash
.venv/bin/python tests/test_m0.py            # 无头
.venv/bin/python tests/test_m0.py --headful  # 有头
```

## API

Base：`http://127.0.0.1:8765/api/v1`
Token：`data/api.token`

```bash
TOKEN=$(cat data/api.token)
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/api/v1/profiles
```

- `GET /profiles`
- `POST /profiles`
- `POST /profiles/{id}/launch`
- `POST /profiles/{id}/stop`
- `GET /profiles/{id}/status`
- `GET/POST /proxies`、`POST /proxies/{id}/check`
- `GET/PUT /settings`

完整定义见 OpenAPI：<http://127.0.0.1:8765/docs>

## 说明

- 每个环境的浏览器数据存放在 `data/profiles/<id>/`。
- 同一环境同时只能运行一个进程（runner 内文件锁）。
- 关闭浏览器窗口后，服务会在几秒内更新状态。
- 数据默认全部在本地，不上传。

## 许可证

Moltskin 使用 Apache License 2.0，见 [LICENSE](LICENSE)。

Camoufox 是独立项目，使用 MPL-2.0，见 [NOTICE](NOTICE)。
内置的 Noto Sans SC 字体使用 SIL OFL 1.1（`web/fonts/OFL.txt`）。

## 免责声明

Moltskin 是浏览器环境管理工具，仅用于合规的测试、隐私、自有数据采集和多账号运营。
不提供批量注册、身份欺诈、接码、绕过实名等能力。指纹检测是持续的对抗过程，
不承诺 100% 不被检测。
