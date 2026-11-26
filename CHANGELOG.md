# Changelog

## Unreleased

### Added

- Chromium 内核补丁 `0009-hide-webdriver`：即使通过 Playwright/CDP 启动，`navigator.webdriver` 仍返回 `false`。启动器默认传入 `--moltskin-hide-webdriver`，并登记内核能力。
- Chromium 内核补丁 `0010-screen-metrics`：窗口可选择屏幕尺寸/色深/像素比预设，同步 `screen`、`devicePixelRatio` 和相关媒体查询。
- Chromium 内核补丁 `0011-platform`：窗口可选择运行平台与架构预设，同步 `navigator.platform`、默认 User-Agent、User-Agent Client Hints 高熵值和 HTTP `Sec-CH-UA-*` 请求头。
- Chromium 内核补丁 `0012-webgl-info`：窗口可选择 WebGL 显卡预设，覆盖 `WEBGL_debug_renderer_info` 读取的厂商和型号。
- Chromium 内核补丁 `0013-canvas-noise`：窗口可开启 Canvas 2D 与 WebGL 像素噪声，按环境生成稳定且互不相同的 ±1 颜色噪声，Alpha 保持不变。
- Chromium 内核补丁 `0014-audio-noise`：窗口可开启 AudioContext 指纹噪声，对 OfflineAudioContext 渲染结果和解码音频加入稳定、听感不可闻的样本扰动。
- Chromium 内核补丁 `0015-analyser-noise`：同一音频开关也覆盖 `AnalyserNode` 的浮点/字节频域和时域读数。
- Chromium 内核补丁 `0016-locale`：网站首选语言同时设置渲染进程的 ICU 默认区域，`Intl` 格式化与 `navigator.language` 保持一致。
- Chromium 内核补丁 `0017-speech-voices`：按网站首选语言生成稳定的 Windows 风格语音列表，替换宿主机的 macOS 语音。
- Chromium 内核补丁 `0018-screen-avail`：屏幕预设支持单独设置 `availWidth/availHeight`，产品默认模拟 Windows 任务栏占用。
- Chromium 内核补丁 `0019-font-filter`：Windows 平台预设启用 CSS 字体白名单，隐藏 macOS 专有字体族。
- Chromium 窗口绑定固定代理时加入 `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`，避免 WebRTC 绕过代理使用非代理 UDP。

## 0.4.1 — 2026-11-07

Hardening pass driven by end-to-end testing. 90 checks across four suites
(`tests/test_e2e.py`, `test_edge.py`, `test_runtime.py` + unit tests).

### Fixed

- **无法解绑代理 / 移出分组**：`PATCH` 把 `null` 当成"没传"，在编辑弹窗里选
  「不使用代理」或「未分组」实际不会生效。改用 `exclude_unset` 区分两者。
  代理的用户名密码、分组的 parent_id 同样受益。
- **`require_proxy_ok` 是死设置**：从来没有被读取。现在严格模式会拦下未检测的
  代理；坏代理始终拦截（避免带坏代理启动暴露本机 IP）。
- **环境上限 / 每日打开上限只显示不生效**：现在创建超限返回 409，打开超限返回
  429，错误码 `PROFILE_LIMIT` / `DAILY_LIMIT`。
- **"今日打开"跨时区算错**：本地 0 点直接和 UTC 时间戳比字符串，UTC+8 会漏算。
  改为把本地 0 点换算成 UTC 再比较。
- **定时任务可能重复触发**：用 `last_run_at[:10]`（UTC 日期）判断"今天跑过没"，
  本地凌晨的任务会重复执行整个 10 分钟宽限窗口。改为按本地日期比较。
- **改操作系统不换指纹预设**：`os` 变了但预设还是旧的，UA 与系统对不上。
  现在换系统会自动重新生成预设。
- **运行中导出 Cookie 可能读到旧数据**：只读连接用了 `immutable=1`，会忽略
  WAL。现在优先普通只读，失败才退回。
- **备份临时目录泄漏**：只删了 zip，父目录留在 /tmp。现在随响应一起清理。
- **导入重名静默改名**：现在返回 `renamed` 列表并在提示里说明。

### Tests

- `tests/test_e2e.py` — 全链路 34 项
- `tests/test_edge.py` — 边界与回归 31 项
- `tests/test_runtime.py` — 真实浏览器身份一致、进程回收、调度触发 10 项


## 0.4.0 — 2026-11-06

### Added

- **浏览器内控制台入口** — 每个环境加载一个内置扩展：左侧栏图标、
  工具栏按钮和 `Alt+Shift+M` 都能跳回管理页（图标会出现在浏览器左侧栏）。
- **环境同步** — 把一个环境的 Cookie + localStorage + IndexedDB 覆盖到另一个
  环境，可只同步其中一部分；两个环境都需要关闭。
- **定时任务支持固定间隔** — 除了每天定时，还可以「每 N 分钟」执行，
  新建任务不会立刻触发，从下一个周期开始。
- **备份 / 恢复** — 导出数据库与扩展，可选打包每个环境的登录态；
  恢复前自动把当前数据库留成 `.moltskin.db.bak`。
- 设置页新增「环境内显示控制台按钮」开关。

### Fixed

- runner 被强杀后环境会永远停在「运行中」，卡住并发上限、备份和批量操作：
  新增 15 秒一次的会话回收（`reap_stale`），在列表、统计、启动前也会先校验。
- 新建环境首次打开时左侧栏没有入口：内置扩展会携带管理器地址与水印。


## 0.3.0 — 2026-11-06

The four gaps that mattered most for daily multi-account work.

### Added

- **Cookie 导入 / 导出** — read and write each profile's `cookies.sqlite`
  without launching the browser. Accepts a JSON array, Playwright
  `storage_state`, or Netscape `cookies.txt`; merge or replace; export JSON or
  `cookies.txt`; copy cookies from another profile; clear all.
- **扩展中心** — upload `.xpi` / `.zip`, unpacked into `data/extensions/`,
  assigned per profile and loaded by Camoufox on launch
  (`browser_specific_settings.gecko.id` is injected when missing, otherwise
  Firefox silently drops the add-on).
- **批量进度** — batch open/close now returns a job id and the UI polls
  `/jobs/{id}`, showing per-environment state and failure reasons instead of a
  single toast.
- **定时任务** — daily launch/stop tasks with weekday selection and target
  (all / group / environments), a background scheduler with a 10-minute grace
  window, "run now", enable toggle and last-run result.
- Profile detail now has 扩展 and Cookie sections alongside the fingerprint
  checks.

### API

- `GET|PUT|DELETE /profiles/{id}/cookies`,
  `GET /profiles/{id}/cookies/export?format=json|netscape`,
  `POST /profiles/{id}/cookies/copy-from/{source_id}`.
- `GET|POST /extensions`, `PATCH|DELETE /extensions/{id}`,
  `GET|PUT /profiles/{id}/extensions`.
- `POST /profiles/batch-async`, `GET /jobs`, `GET /jobs/{id}`.
- `GET|POST /schedules`, `PATCH|DELETE /schedules/{id}`,
  `POST /schedules/{id}/run`.
- SDK: cookies / extensions / schedules / jobs helpers.
- MCP: `get_cookies`, `set_cookies`, `copy_cookies`, `list_extensions`,
  `set_profile_extensions`, `list_schedules`, `create_schedule`, `run_schedule`.

### Fixed

- 配置列在 Camoufox 随机字体下出现乱码：混排中文不再使用等宽字体，
  等宽字体链补齐中文回退。
- 窄窗口下表格操作列固定在右侧，工具栏与筛选条件分两行，不再挤压。


## 0.2.0 — 2026-11-06

Management-console pass: the UI now has the density and control surface of a
commercial anti-detect browser.

### Added

- **Pages instead of modals**: 首页 dashboard, 浏览器窗口, 分组管理, 代理 IP,
  操作日志, 设置 — each with its own layout.
- **Home dashboard**: environment / running / today-opened / proxy-health KPIs,
  live running list with inline stop, recent activity feed.
- **Window list**: sortable columns, page-size + jump pagination, column
  visibility, table ⇄ card view, group and tag filters, keyword search with
  field (全部/名称/备注/IP/分组) and operator (包含/不包含/等于).
- **Row menu**: open/close, detail, edit, duplicate, reset fingerprint, delete.
- **Batch bar**: launch, stop, move group, edit tags, export, delete, clear.
- **Batch create**: N environments from one prefix with sequential numbering.
- **Quota strip**: used / total environments, today's opens / daily limit,
  running / concurrency limit.
- **Config column**: fingerprint summary, open count and last-open time.
- **Proxy page**: inline add form, batch check, exit IP with country flag,
  latency, bound-environment count.
- **Groups page**: counts, rename, delete, jump to filtered window list.
- **Logs page**: pagination, action filter, human-readable labels.
- **Settings page**: concurrency, headless, manager tab, environment and daily
  open limits, proxy check URL / interval / requirement, data directory,
  engine versions.

### API

- `GET /api/v1/profiles` gains `sort`, `order`, `field`, `op`, `tag`,
  `opened`/`status` values (`opened`, `unopened`, `stopped`) and
  `group_id=__none__`.
- `GET /api/v1/stats` adds `profiles_opened`, `profiles_crashed`,
  `today_opened`, `profile_limit`, `daily_open_limit`, `groups_total`,
  `tags_total`, `proxies_total`, `proxies_ok`, `proxies_failed`.
- `GET /api/v1/groups` returns `profile_count`.
- `GET /api/v1/proxies` returns `used_by`.
- `GET /api/v1/logs` sets `X-Total-Count`.
- New `GET /api/v1/tags`.
- New `POST /api/v1/profiles/{id}/reset-fingerprint`.
- New settings: `profile_limit`, `daily_open_limit`, `data_dir`.

### Changed

- `profiles.open_count` is tracked and incremented on every launch.
- Version bumped to 0.2.0.

## 0.1.0 — 2026-11-06

First public alpha.

### Added

- Profile create / edit / duplicate / delete with isolated `user_data_dir`.
- Stable fingerprint preset generated once per profile and reused on every launch.
- Fingerprint preview: OS, user agent, platform, CPU cores, screen, WebGL.
- Pre-launch consistency checks: OS/UA, screen, WebGL, proxy, timezone.
- CreepJS self-test with screenshot report.
- Proxy pool with exit IP, country, timezone and latency checks (Camoufox components).
- GeoIP alignment through Camoufox (`geoip=True`).
- Start / stop, persistent cookies, duplicate-launch protection.
- Group, tags, batch launch / stop / delete / move / tag.
- JSON import / export.
- Local REST API with bearer token.
- MCP server with 15 tools.
- Python SDK (`moltskin.client.MoltskinClient`).
- Manager page opened inside the browser on launch.
- Docker image and Compose file.
- Orphan browser cleanup and scheduled proxy health checks.
- Smoke tests and GitHub Actions CI.

### Known limits

- Headful rendering is verified on macOS; Linux is verified through Docker headless.
- No page-level agent control yet (no click / type / extract over MCP).
- No desktop app or auto-update.
- Proxy passwords are stored in the local SQLite database; encryption at rest is planned.
