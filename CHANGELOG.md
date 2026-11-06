# Changelog

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
