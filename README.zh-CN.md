# Moltskin

<p align="center"><img src="docs/logo.svg" width="76" alt="Moltskin logo"></p>

基于 Camoufox 的本地多环境指纹浏览器。

[English](README.md)

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

浏览器内核、指纹生成、代理地理定位全部直接调用 Camoufox 的公开组件：

- `Camoufox()` / `persistent_context`：启动与持久化
- `camoufox.ip.Proxy` / `proxy_exit_geo`：代理出口 IP 与时区
- `camoufox.geolocation.get_geolocation`：国家、语言、经纬度
- `camoufox.multiversion.list_installed`：内核版本

本项目只提供产品层：环境管理、进程监管、本地 API、UI、数据库。

## 当前能力

- 创建 / 删除 / 复制环境
- 一键启动 Camoufox（持久化 profile，登录态保留）
- **同一环境指纹固定**（创建时生成并持久化预设）
- 指纹预览（UA、屏幕、WebGL、CPU 核数）
- 启动前一致性检查（系统/UA、代理、时区）
- CreepJS 指纹自检 + 截图
- 分组、标签、批量打开/关闭/删除/移动
- JSON 导入 / 导出
- MCP Server（15 个工具）
- Python SDK
- 一键关闭，状态自动回收
- 代理池：新增、删除、检测（出口 IP / 国家 / 时区 / 延迟）
- 环境列表、搜索、状态筛选
- 并发上限、无头模式、启动时打开管理页
- 本地 API Token 鉴权
- 管理页可在 Camoufox 内自动打开
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
