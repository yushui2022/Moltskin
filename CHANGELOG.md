# Changelog

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
