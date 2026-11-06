# Changelog

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
