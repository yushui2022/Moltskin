# Moltskin

<p align="center"><img src="docs/logo-wordmark.svg" width="320" alt="Moltskin"></p>

[中文文档](README.zh-CN.md)

Moltskin is a self-hosted browser for managing isolated profiles. Each profile has its own data directory. The **0.4.1 alpha** uses Camoufox for fingerprint presets and per-profile proxies. This development branch also supports a native Chromium preview with per-profile permissions. [Camoufox](https://github.com/daijro/camoufox) · [Changelog](CHANGELOG.md) · [Quick start](#quick-start)

## Chromium roadmap

Open Moltskin on macOS to see the window list in a dedicated desktop manager. Click **Open** on a profile to see its launch status and use its browser in a separate window with tabs and an address bar. With the default settings, each Chromium window opens its own workbench in the first tab.

Select Camoufox or Chromium when creating a profile. Chromium profiles have separate settings for 2D canvas readback, WebGL debug renderer information, reported CPU concurrency and device memory, local fonts, media capture and site geolocation. Open the local runtime report inside a profile to check its graphics privacy settings. Each profile keeps its engine and data directory across restarts. The build runs on Apple Silicon and Ubuntu x86-64. On a dedicated Ubuntu host, one SSH user can open a Linux Chromium window in a desktop browser through noVNC and reconnect to the same running session. [Set up the Linux remote viewer](chromium/remote/README.md) [Build and run the Chromium preview](chromium/README.md) · [Fingerprint surface coverage (中文)](chromium/fingerprint-coverage.md)

Chromium profiles can use the system network or a fixed HTTP, HTTPS or SOCKS5 proxy without stored credentials. The binding persists across restarts. Close the profile before changing its network settings. Fixed proxies also disable non-proxied WebRTC UDP. The native build includes a `navigator.webdriver` patch that keeps the value false even under Playwright/CDP automation. For social-media and e-commerce profiles, prefer the host operating system so fonts, emoji and speech stay coherent; cross-OS presets remain available but are easier to detect. Profiles can also select screen geometry, device pixel ratio, host platform and CPU architecture; the kernel keeps `screen`, media queries, `navigator.platform`, the default User-Agent and User-Agent Client Hints consistent. The selected website language also sets the renderer ICU locale used by `Intl` and a stable Windows-style speech-synthesis voice list. A Windows platform preset applies the cross-platform font allowlist and stable WebGL parameters only when the selected system differs from the host; same-OS profiles keep host fonts, speech and WebGL details. A WebGL graphics preset overrides the unmasked vendor and renderer strings exposed by `WEBGL_debug_renderer_info`. Profiles can enable deterministic Canvas 2D and WebGL pixel noise: each profile gets a stable per-pixel +/-1 color perturbation while alpha stays unchanged. AudioContext noise adds an inaudible, stable sample perturbation to OfflineAudioContext rendering, decoded audio and AnalyserNode float/byte frequency and time-domain readbacks. [Profile and proxy API](docs/API.md) · [WebDriver regression](tests/test_chromium_webdriver.py) · [Platform regression](tests/test_chromium_platform_settings.py) · [WebGL regression](tests/test_chromium_webgl_info.py) · [Canvas noise regression](tests/test_chromium_canvas_noise.py) · [Audio noise regression](tests/test_chromium_audio_noise.py)

| Milestone | What we will verify |
|---|---|
| Build the engine | Build a pinned Chromium revision on Linux x86-64 and macOS arm64. Launch each build from a clean profile and record its version and checksum. |
| Launch both engines | Choose the engine for each new profile in the dashboard. Existing Camoufox profiles keep their data. Separate Chromium profiles can run and restart independently. |
| Ship a native privacy setting | Exercise local font access, media capture and location permissions on the same test origin in two profiles. Swap policies and restart to verify each profile independently. |
| Site compatibility and profile isolation | Check web font loading, fallback and text layout with local font enumeration restricted. On the same origin, verify local storage, IndexedDB and Cache Storage in two profiles. Restart them, clear the test records in one and check that the other still reads its own data. |
| Remote sessions on Linux | Verify that two team members can operate their own sessions on a Linux node. Test reconnection and access revocation. |
| Maintain releases | Track upstream Beta and Stable, apply our patches on upgrade branches, build both platforms and test profile upgrades and recovery before publishing. |

**Planned GitHub automation.** When someone reports a bug, the bot will ask for missing details or reproduce it and open a draft fix with a regression test. On new or updated pull requests, it will report the tested commit, what failed before the change and what passed afterward. Maintainers review and merge. [Contributing](CONTRIBUTING.md)

![Moltskin overview](docs/screenshots/overview.png)

| Dashboard | Create | Batch create |
|---|---|---|
| ![Home](docs/screenshots/home.png) | ![Create](docs/screenshots/create-profile.png) | ![Batch](docs/screenshots/batch-create.png) |

| Extensions | Schedules | Profile detail |
|---|---|---|
| ![Extensions](docs/screenshots/extensions.png) | ![Schedules](docs/screenshots/schedules.png) | ![Detail](docs/screenshots/profile-detail.png) |

## Why Moltskin

- **Local-first** — profiles, cookies and logs stay on your machine. No cloud account.
- **Persistent Camoufox presets** — each Camoufox profile saves its fingerprint preset at creation and reuses it across launches.
- **Isolated by default** — one browser process and one `user_data_dir` per profile.
- **Auditable kernel** — built on Camoufox, an open-source Firefox fork with C++-level fingerprint patches.
- **API-first** — manage profiles, proxies and sessions over HTTP, through the Python SDK or with the MCP server.

## Features

| Area | Status |
|---|---|
| Create, edit and duplicate profiles. Restore individual profiles from the recycle bin or permanently delete their local data. | ✅ |
| Launch / stop, persistent cookies and logins | ✅ |
| One process per profile, duplicate-launch protection | ✅ |
| Camoufox fingerprint preset persisted per profile | ✅ |
| Camoufox fingerprint preview (UA, screen, WebGL, cores) | ✅ |
| Camoufox preset and browser readings compared on launch, with separate configuration and proxy checks | ✅ |
| Camoufox CreepJS self-test with screenshot | ✅ |
| Proxy pool with TXT/CSV or clipboard import, per-line validation, duplicate detection, and exit IP / country / timezone / latency checks | ✅ |
| Camoufox GeoIP alignment (timezone, locale, coordinates) | ✅ |
| Local API with bearer token | ✅ |
| Manager UI: dashboard, window list, groups, proxies, logs, settings | ✅ |
| Sortable columns, pagination, column visibility, table/card views | ✅ |
| Keyword search with field + operator, group/tag filters | ✅ |
| Batch open/stop/delete/move/tag/export, batch create | ✅ |
| Manager page opened inside the browser on launch | ✅ |
| Orphan browser cleanup on startup | ✅ |
| Scheduled proxy health checks | ✅ |
| Groups, tags, JSON import/export | ✅ |
| Cookie import / export (JSON, Netscape), copy between profiles | ✅ |
| Extension center: upload, assign per profile, loaded on launch | ✅ |
| Batch open/stop with per-environment progress | ✅ |
| Scheduled launch / stop tasks (daily or every N minutes) | ✅ |
| In-browser console button (sidebar / toolbar / Alt+Shift+M) | ✅ |
| Environment sync: cookies + localStorage + IndexedDB | ✅ |
| Backup / restore (database, extensions, optional login state) | ✅ |
| MCP server, Python SDK | ✅ |
| macOS desktop manager (local Chromium build) | ✅ |

### Profile startup page

Choose the local dashboard, a blank page, or the engine default when creating or
editing a profile. With the default startup settings, headed Chromium opens that
window's workbench, showing its exit IP, environment and browser readings. Headless
Chromium opens a blank tab. Camoufox follows the dashboard's startup setting.
Both engines can also block webpage images per
profile. Changes apply on the next launch and are included in profile exports.

![Manager inside the browser](docs/screenshots/in-browser.png)

## Quick start

### Docker (recommended)

```bash
git clone https://github.com/yushui2022/Moltskin.git
cd Moltskin
docker compose up -d
```

Open <http://127.0.0.1:8765>.

The first build downloads the Camoufox browser (~1.3 GB) and takes several minutes.
The resulting image is large (several GB) because it bundles the browser.
Profiles and the SQLite database are stored in `./data`.

### Local (macOS / Linux)

Requirements: Python 3.10+, [uv](https://github.com/astral-sh/uv), and macOS or Linux.

```bash
git clone https://github.com/yushui2022/Moltskin.git
cd Moltskin

uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/camoufox fetch

.venv/bin/python run.py
```

Then open <http://127.0.0.1:8765>.

## How it works

```text
┌─────────────────────────────────────────────┐
│  Web UI (React-free, plain HTML/CSS/JS)     │
└──────────────────┬──────────────────────────┘
                   │ HTTP + token
┌──────────────────▼──────────────────────────┐
│  FastAPI service                            │
│  profiles · proxies · launcher · sessions   │
│  SQLite                                     │
└──────────────────┬──────────────────────────┘
                   │ subprocess per profile
┌──────────────────▼──────────────────────────┐
│  Runner (Python)                            │
│  Camoufox(persistent_context=True, ...)     │
└──────────────────┬──────────────────────────┘
                   │
              Firefox / Camoufox
```

- Each profile has its own `user_data_dir`; Firefox locks it, so a profile cannot run twice.
- The fingerprint preset is generated once at profile creation and stored in SQLite.
  Every launch passes the same preset to Camoufox.
- Proxy checks and GeoIP lookups use Camoufox's own components
  (`camoufox.ip`, `camoufox.geolocation`).

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q tests/test_smoke.py
```

The smoke tests use a temporary data directory and do not launch a browser.
The full integration test is `tests/test_m0.py`.

## API

Base URL: `http://127.0.0.1:8765/api/v1`

The token is generated at first start and stored in `data/api.token`.

```bash
TOKEN=$(cat data/api.token)

curl -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8765/api/v1/profiles
```

| Method | Endpoint | Description |
|---|---|---|
| GET | `/profiles` | List profiles |
| POST | `/profiles` | Create a profile |
| POST | `/profiles/{id}/launch` | Launch |
| POST | `/profiles/{id}/stop` | Stop |
| GET | `/profiles/{id}/status` | Status |
| GET/POST | `/proxies` | Proxy pool |
| POST | `/proxies/{id}/check` | Check exit IP / geo |
| POST | `/profiles/{id}/verify` | Fingerprint / proxy consistency checks |
| POST | `/profiles/{id}/self-test` | Open CreepJS, screenshot, save report |
| POST | `/profiles/batch` | Batch launch / stop / delete / move / tag |
| GET | `/export/profiles` | Export profiles as JSON |
| POST | `/export/profiles` | Export selected profile IDs in the same JSON format |
| POST | `/import/profiles` | Import profiles from JSON |
| GET/PUT | `/settings` | Settings |
| GET | `/engine` | Camoufox builds and local Moltskin Chromium availability |

Interactive docs: <http://127.0.0.1:8765/docs>
Full endpoint reference: [docs/API.md](docs/API.md)

## MCP server

Start Moltskin, then add this to Claude Desktop, Cursor or any MCP client:

```json
{
  "mcpServers": {
    "moltskin": {
      "command": "/path/to/Moltskin/.venv/bin/python",
      "args": ["/path/to/Moltskin/moltskin/services/core/mcp_server.py"],
      "env": { "MOLTSKIN_API": "http://127.0.0.1:8765" }
    }
  }
}
```

The server exposes 15 tools: create/list/launch/stop profiles, proxies, groups,
consistency checks, self-test and batch operations.

## Python SDK

```python
from moltskin.client import MoltskinClient

with MoltskinClient() as client:
    profile = client.create_profile("shop-ph-01", proxy_id="px_...")
    client.launch(profile["id"])
    print(client.status(profile["id"]))
    client.stop(profile["id"])
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `MOLTSKIN_DATA_DIR` | `data` | Data directory |
| `MOLTSKIN_HOST` | `127.0.0.1` | Bind host |
| `MOLTSKIN_PORT` | `8765` | Service port |
| `MOLTSKIN_HEADLESS` | unset | Force headless mode (Docker sets `1`) |
| `MOLTSKIN_OPEN_MANAGER` | `1` | Open the manager page on launch |

## Comparison

| | Moltskin | BitBrowser | AdsPower | Multilogin |
|---|---|---|---|---|
| Open source | ✅ | ❌ | ❌ | ❌ |
| Self-hosted / local data | ✅ | ❌ | ❌ | ❌ |
| Browser kernel | Camoufox (Firefox), Chromium development preview | Chromium + Firefox | Multi | Custom |
| Stable fingerprint per profile | Camoufox presets | ✅ | ✅ | ✅ |
| Team & permissions | 🚧 | ✅ | ✅ | ✅ |
| MCP / AI agents | 🚧 | ✅ (7.1.5+) | Beta | Bridge |
| Price | Free | Freemium | Freemium | Paid |

## Disclaimer

Moltskin is a browser profile management tool. It is intended for legitimate
testing, privacy, scraping your own data, and multi-account operations that
comply with the target platform's terms and applicable law.

It does not provide, and must not be used for, bulk account registration,
identity fraud, captcha farming, or bypassing platform KYC. Fingerprint
detection is an ongoing arms race; no tool can guarantee 100% undetectability.

## Credits

The interface design language is inspired by [Twenty](https://github.com/twentyhq/twenty).
Only visual tokens and layout patterns were reimplemented for this project; no
Twenty source code is included.

## Testing

```bash
pytest tests -q                       # unit / smoke
python tests/test_e2e.py              # full loop against a running service
python tests/test_edge.py             # input limits, quotas, error paths
python tests/test_runtime.py          # real browser identity, reaper, scheduler
```

## License

Moltskin is licensed under the Apache License 2.0 — see [LICENSE](LICENSE).
Commercial use, closed-source forks and redistribution are allowed.

It builds on Camoufox: the Python package is MIT, the patched Firefox browser
build is MPL-2.0. Moltskin runs both unmodified, so nothing in this repository
becomes MPL-covered. See [NOTICE](NOTICE) for the full third-party list, the
bundled Noto Sans SC / Inter fonts (SIL OFL 1.1) and what redistribution of the
browser binary requires.
