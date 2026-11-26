# Moltskin API

Base URL: `http://127.0.0.1:8765/api/v1`
Auth: `Authorization: Bearer <token>`, token stored in `data/api.token`.

Interactive OpenAPI docs: <http://127.0.0.1:8765/docs>

---

## Health and engine

```bash
curl http://127.0.0.1:8765/api/v1/health
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/api/v1/engine
```

`data.chromium` reports `available`, `version` and a diagnostic `message` when the local Moltskin build cannot run. Set `MOLTSKIN_CHROMIUM_BINARY` before starting the service to select a custom build path.

## Profiles

| Method | Path | Description |
|---|---|---|
| GET | `/profiles` | List; `status`, `q`, `field`, `op`, `tag`, `group_id`, `sort`, `order`, `page`, `page_size` |
| POST | `/profiles` | Create |
| GET | `/profiles/{id}` | Detail with `fingerprint_preview` |
| PATCH | `/profiles/{id}` | Update metadata or settings for the selected engine |
| POST | `/profiles/{id}/trash` | Move a stopped profile to the recycle bin |
| GET | `/trash/profiles` | List recycled profiles, newest first |
| POST | `/trash/profiles/{id}/restore` | Restore a recycled profile |
| DELETE | `/trash/profiles/{id}` | Permanently delete a recycled profile and its data |
| DELETE | `/profiles/{id}?delete_data=true` | Permanently delete an active-list entry (without the flag, keep the directory) |
| POST | `/profiles/{id}/duplicate` | Duplicate with a new fingerprint |
| POST | `/profiles/{id}/reset-fingerprint` | New fingerprint, keep cookies |
| POST | `/profiles/{id}/launch` | Launch |
| POST | `/profiles/{id}/stop` | Stop |
| GET | `/profiles/{id}/status` | Live status |
| POST | `/profiles/{id}/verify` | Consistency checks |
| POST | `/profiles/{id}/self-test` | CreepJS screenshot |
| POST | `/profiles/batch` | Batch action |

### List filters

| Param | Values | Notes |
|---|---|---|
| `q` | string | Keyword |
| `field` | `all` `name` `remark` `proxy` `group` | Which column the keyword applies to |
| `op` | `contains` `not_contains` `equals` | Comparison |
| `status` | `running` `ready` `crashed` `opened` `unopened` `stopped` | `stopped` = not running |
| `group_id` | id or `__none__` | Un-grouped environments |
| `tag` | string | Exact tag match |
| `sort` | `last_opened` `created_at` `name` `status` `open_count` | Default `last_opened` |
| `order` | `asc` `desc` | Default `desc` |

```bash
# environments opened at least once, newest first, page 2
curl -G .../profiles -H "Authorization: Bearer $TOKEN" \
  -d status=opened -d sort=last_opened -d page=2 -d page_size=20

# names containing "shop", excluding test accounts elsewhere
curl -G .../profiles -H "Authorization: Bearer $TOKEN" \
  -d q=shop -d field=name -d op=contains
```

### Create

Omitting `engine` selects Camoufox, including for profiles saved before dual-engine support. To create a Chromium profile, send `{"name":"local-preview","engine":"chromium","local_fonts":"restricted"}`. Supported font policies are `standard` (default, follows site permissions) and `restricted` (empty local font list). Each profile keeps its engine for its lifetime. Close a Chromium profile before changing its font or media capture policy. The usual launch, stop and status endpoints apply.

Chromium profiles also accept `media_capture`. The default `standard` follows site permissions. Set `blocked` to deny camera and microphone capture, including requests from previously authorized sites. This preference is retained by profile duplication and configuration export/import. `GET /engine` reports `chromium.supports_media_capture_policy`. Launching a blocked profile on a build without this capability returns HTTP 503.

Chromium also accepts `geolocation: "standard"` (default) or `"blocked"`. Blocked profiles deny site geolocation queries and watches even after a prior site grant. `GET /engine` reports `chromium.supports_geolocation_policy`; launching a blocked profile requires a compatible build. Close the profile before editing this setting.

Chromium profiles accept `hardware_reporting` values `standard` (default) and `limited`. The latter caps reported logical processors at 4 and approximate device memory at 4 GiB, including opted-in memory Client Hints. Close the window before editing this policy. Check `supports_hardware_reporting_policy` on the selected build in `GET /engine`. A profile export containing this restriction uses format version 10.

Chromium profiles accept independent `canvas_readback` and `webgl_debug_info` settings. Both default to `standard`. Set `canvas_readback` to `blocked` to restrict 2D pixel reads and 2D canvas image export. Set `webgl_debug_info` to `blocked` to disable the WebGL debug renderer info extension while keeping WebGL drawing available. The matching capability fields in `GET /engine` are `supports_canvas_readback_policy` and `supports_webgl_debug_info_policy`, on both the default build and each installed version. A restricted profile requires a compatible build. Close the profile before changing either setting.

Each Chromium profile can set `browser_language` (preferred language for websites, the HTTP `Accept-Language` header, `navigator.language`, the renderer ICU default locale used by `Intl`, and the generated speech-synthesis voice list), `browser_timezone` (IANA time zone), and `browser_window_size` (initial outer width and height). For example, use `"ja-JP"`, `"Asia/Tokyo"`, and `"1280x800"`. The lists at `GET /engine` are `chromium.browser_languages`, `chromium.browser_timezones`, and `chromium.browser_window_sizes`. Omitting a field on PATCH preserves its value. Sending an empty string restores the browser default. Close a running profile before changing these settings.

Each Chromium profile can set `chromium_screen` to a preset in `WIDTHxHEIGHTxDEPTH@DPR` form, for example `"1920x1080x24@1.25"`. The launcher appends a Windows-style work area (`|AVAILWIDTHxAVAILHEIGHT`) so `screen.availHeight` is smaller than `screen.height`; the kernel also accepts an explicit `|` suffix. It synchronizes `screen.width`, `screen.height`, `screen.availWidth`, `screen.availHeight`, `screen.colorDepth`, `devicePixelRatio`, and the `device-width`, `device-height` and `resolution` media queries. Read the accepted values from `chromium.chromium_screen_presets` at `GET /engine`. The window's outer size remains independent through `browser_window_size`. A profile export containing this setting uses format version 11.

`chromium_platform` selects a host platform preset in `platform:version:architecture` form, for example `"windows:10.0.0:x86_64"`. A Windows platform preset also enables the CSS font-family allowlist, replacing macOS-only families with generic fallbacks. It synchronizes `navigator.platform`, the default User-Agent's OS token, `navigator.userAgentData.platform`, the high-entropy `architecture`, `bitness` and `platformVersion` values, and the corresponding HTTP `Sec-CH-UA-*` headers. Read the accepted values from `chromium.chromium_platform_presets` at `GET /engine`. A Windows platform preset also applies a stable WebGL parameter and extension profile when `chromium_webgl` is set. A custom `chromium_user_agent` still takes precedence for the UA string, while Client Hints follow the platform preset. A profile export containing this setting uses format version 12.

`chromium_webgl` selects a graphics preset from `chromium.chromium_webgl_presets` at `GET /engine`. Each preset is encoded as `label|vendor|renderer`; the label is for display, while the last two fields replace the values returned by `UNMASKED_VENDOR_WEBGL` and `UNMASKED_RENDERER_WEBGL`. Masked `VENDOR` and `RENDERER` values stay `WebKit` and `WebKit WebGL`, matching Chromium. If `webgl_debug_info` is `blocked`, the extension remains unavailable and no values are exposed. A profile export containing this setting uses format version 13.

`chromium_canvas_noise` is a boolean, default `false`. When true, the profile receives a stable seed derived from its profile id. Canvas 2D `getImageData`, `toDataURL` and `toBlob` results, plus WebGL `readPixels` and canvas exports, get deterministic +/-1 noise in the color channels; alpha is unchanged. Single-pixel 2D readbacks are left untouched to avoid common noise probes; larger readbacks remain noised. The same profile produces the same pattern across launches, while a duplicated or newly imported profile gets a different pattern. Check `supports_canvas_noise_policy` at `GET /engine`; a profile with noise enabled requires a compatible build. A profile export containing this setting uses format version 14.

`chromium_audio_noise` is a boolean, default `false`. When true, the profile gets a separate stable seed. `OfflineAudioContext` render results and buffers returned by `decodeAudioData` receive deterministic +/-0.0001 sample perturbation. `AnalyserNode` readbacks are also perturbed: float frequency data by +/-0.01 dB, byte frequency and byte time-domain data by +/-1, and float time-domain data by +/-0.0001. The changes are inaudible but alter float and quantized fingerprint hashes. The seed is stable per profile and differs from the Canvas seed. Check `supports_audio_noise_policy` at `GET /engine`. A profile export containing this setting uses format version 15.

`chromium_user_agent` sets the HTTP User-Agent and `navigator.userAgent` for one Chromium profile. Set it to an empty string to use the selected build's default. A custom value accepts up to 512 printable ASCII characters with no leading or trailing spaces. Close the window before editing this field and relaunch to apply it. Omitting it on PATCH preserves the saved value. The local `/runtime.html` report shows the request header received by the service alongside the string read by the page.

`chromium_version` pins a profile to an installed build. An empty string follows the host default. `GET /engine` reports the default in `chromium.version` and the installed choices in `chromium.installed_versions`, each with its full version and permission capabilities. The host OS and architecture are available as `chromium.host_os` and `chromium.host_arch`. An unavailable pinned version produces an error at launch. A profile already used by a newer major version requires that major or a later one.

Chromium profiles also accept `chromium_mute_audio` (default `false`), `chromium_renderer_limit` (soft limit on the number of renderer processes), and `chromium_js_heap_mb` (V8 old-generation heap limit in MiB). Zero restores Chromium's default for either limit. Read the supported nonzero values from `chromium.chromium_renderer_limits` and `chromium.chromium_js_heap_limits` at `GET /engine`. The local `/runtime.html` page shows measurements from the window that opens it.

Profiles accept `startup_page` and `block_images` on creation and update. Changes take effect at the next launch. `startup_page` accepts the following values.

| Value | First tab |
|-------|-----------|
| `default` | Camoufox follows the global manager setting. Headed Chromium opens the session's window workbench when both `MOLTSKIN_OPEN_MANAGER` and the global `open_manager_on_launch` setting allow it. Otherwise Chromium opens a blank tab. |
| `dashboard` | Open the local dashboard home on the configured service port. Hosts with `MOLTSKIN_OPEN_MANAGER=0` open a blank tab. |
| `blank` | Open a blank tab in either engine. |

`block_images` defaults to `false`. Set it to `true` to disable page image loading. Existing profiles keep `default` and continue loading images. Both preferences travel with configuration exports and duplicates.

The Chromium workbench URL is scoped to one active session. It displays the window's own environment metadata and reads platform hints and the User-Agent in the viewing browser. The browser asks IPinfo for its exit IP. If that fails, it asks ipify for its address and posts the resulting public IP to the session URL's `/location` endpoint for an optional lookup in the host's installed geolocation database. IP geolocation and the browser's configured time zone are displayed separately.

```bash
curl -X POST http://127.0.0.1:8765/api/v1/profiles \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "shop-ph-01",
    "os": "windows",
    "proxy_id": "px_...",
    "tags": ["ph", "shop"],
    "remark": ""
  }'
```

A fingerprint preset is generated immediately and reused on every launch.

### Local platform compatibility preview

Chromium profiles accept `preview_platform` (Windows, macOS, Linux), `preview_architecture` (arm64 or x86_64) and `preview_platform_version` (one to three dot-separated numeric components of up to five digits each). All three default to empty. The editor reads platform and architecture choices from `chromium.preview_platforms` and `chromium.preview_architectures` in the engine response.

An authenticated `POST /api/v1/chromium/platform-preview` accepts these three optional values and an optional installed `chromium_version`. It runs a fresh offline diagnostics document and returns its observed UA Client Hints as `data.platform`, `data.platform_version`, `data.architecture`, and `data.bitness`. An empty version follows the native value when the selected platform matches the host, and yields an empty reading when switching to another platform. Working windows use their ordinary browser launch settings independently of the preview.

### Launch / stop

Profiles return `browser_version`, the engine version recorded at their last successful launch.
Session records carry the version for that particular run. Both can be `null` for historical data.
Copies and imported configurations start with an empty launch history.
The launch response and the status of an active session also include its `browser_version`.
With no active session the status endpoint returns `null` for this field.

```bash
curl -X POST .../profiles/pf_.../launch -H "Authorization: Bearer $TOKEN"
curl -X POST .../profiles/pf_.../stop   -H "Authorization: Bearer $TOKEN"
```

The stop response reports the saved session state. After a service restart, Moltskin checks process ownership before stopping a browser and uses the Camoufox runner's recorded outcome when available. If a Chromium process ended while its supervisor was offline, session history reports `stopped` with `EXIT_UNCONFIRMED` because its exit code was unavailable.

### Verify

```bash
curl -X POST .../profiles/pf_.../verify -H "Authorization: Bearer $TOKEN"
```

For a Camoufox profile, `data.measured_at` is the UTC time of the most recent launch sample that matches the current profile settings. A new profile or one with changed settings can have a null timestamp. Start the environment to collect a new sample. Rechecking the report reads the saved sample and reruns the existing proxy check if a proxy is bound.

Each item in `checks` identifies its source. A `runtime` row contains the saved preset value in `expected` and the value read on a blank page in `observed`. Its `status` is `match`, `mismatch` or `unverified`. A missing value gives `ok: null`. For example, a CPU row with `expected: 4` and `observed: 18` has `status: "mismatch"` and `ok: false`. The other sources are `configuration` for saved settings and `proxy` for the proxy check. The overall `data.ok` is true only when every row passes.

### Self-test

```bash
curl -X POST .../profiles/pf_.../self-test -H "Authorization: Bearer $TOKEN"
```

Returns `image_url` (`/selftest/<file>.png`), extracted CreepJS text and timestamp.
The profile must be stopped.

### Recycle bin

Moving a profile to the bin keeps its data directory, settings and session history.
The profile disappears from the active list, exports and scheduled targets until restored.
There is no automatic expiry. The list endpoint returns `id`, `name`, `engine`,
`trashed_at` and `purge_started` for each entry.

Restoring returns the standard profile detail with the same ID. If another environment
has taken the name, the restored one gets a numbered suffix such as ` (2)`. Group,
proxy and extension assignments are restored when their referenced records still
exist. Restoring respects the active profile limit. A database-only backup restored
on another machine can recover the settings and session records even when the
original browser files are absent.

Permanent deletion removes the environment directory. An interrupted deletion stays
in the bin with `purge_started: true`; retry the same DELETE to finish. Restore
is disabled once permanent deletion starts.

### Batch

```json
{"action": "launch", "ids": ["pf_1", "pf_2"]}
{"action": "move", "ids": ["pf_1"], "group_id": "grp_1"}
{"action": "tag", "ids": ["pf_1"], "tags": ["ph"]}
{"action": "trash", "ids": ["pf_1", "pf_2"]}
{"action": "delete", "ids": ["pf_1"], "delete_data": true}
```

Response: `{ "ok": 2, "failed": 0, "results": [...] }`.

## Groups

`GET /groups`, `POST /groups`, `PATCH /groups/{id}`, `DELETE /groups/{id}`.

Each group carries `profile_count`. Deleting a group moves its environments to
未分组 instead of deleting them.

## Tags

`GET /tags` returns every tag with how many environments use it:

```json
{"data": [{"tag": "ph", "count": 12}, {"tag": "shop", "count": 4}]}
```

## Proxies

| Method | Path | Description |
|---|---|---|
| GET | `/proxies` | List |
| POST | `/proxies` | Add (`http`, `https`, `socks5`) |
| POST | `/proxies/bulk` | Import up to 500 proxy records |
| PATCH | `/proxies/{id}` | Update |
| DELETE | `/proxies/{id}` | Delete and unbind |
| POST | `/proxies/{id}/check` | Check exit IP / country / timezone / latency |
| POST | `/proxies/check-batch` | `{"ids": [...]}` |

Each proxy includes `used_by`, the number of environments bound to it.

`POST /proxies/bulk` accepts a JSON array under `proxies`. Each record requires
`host` and `port`. Optional fields are `protocol` (defaults to `http`), `name`,
`username`, `password`, and a positive `line` number from the source file. If
omitted, the line number is the record’s one-based position in the request.

```json
{"proxies":[{"line":3,"protocol":"socks5","host":"192.0.2.1","port":1080}]}
```

The response includes `data.created` (count), `data.items` (created records
with IDs, names and line numbers), and `data.skipped` (line, reason and
`duplicate` or `invalid` status). Valid records can be saved alongside rejected
rows. An existing protocol, normalized host and port counts as a duplicate
even if the incoming credentials differ. The stored credentials are preserved.
An empty list or a batch over 500 records returns HTTP 422 before any writes.

## Cookies

Import into a stopped Camoufox profile. Supply one Hubstudio environment export per destination, as a JSON array or a Hubstudio response with a JSON-encoded `data` array. Netscape and Playwright files are also supported. Moltskin validates the entire file before replacing anything. Invalid or empty input returns HTTP 422 without changing stored cookies. Files declaring partitioned or first-party-only cookies are also rejected. Use the DELETE endpoint to clear a profile intentionally. Persistent cookies go into the profile’s Firefox database. Imported session cookies are held for delivery to that profile on its next launch.

| Method | Path | Description |
|---|---|---|
| GET | `/profiles/{id}/cookies?domain=` | List cookies |
| PUT | `/profiles/{id}/cookies` | Import |
| GET | `/profiles/{id}/cookies/export?format=json\|netscape` | Download |
| POST | `/profiles/{id}/cookies/copy-from/{source_id}` | Merge from another profile |
| DELETE | `/profiles/{id}/cookies` | Clear |

Cookie listings and JSON exports retain Firefox’s `originAttributes` and `isPartitionedAttributeSet` when those fields restrict a cookie’s scope. Netscape export returns HTTP 422 (`UNSUPPORTED_COOKIE_SCOPE`) if any row requires this metadata. Importing, copying, or syncing such a collection returns HTTP 422 (`INVALID_COOKIES`) and preserves the destination.

```bash
# Netscape cookies.txt
curl -X PUT .../profiles/pf_1/cookies -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text": "# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tFALSE\t1893456000\tsid\tabc", "mode": "replace"}'

# JSON array or Playwright storage_state
curl -X PUT .../profiles/pf_1/cookies -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"cookies": [{"name": "sid", "value": "abc", "domain": ".example.com", "path": "/"}], "mode": "merge"}'
```

`mode` is `merge` (default) or `replace`.

## Extensions

| Method | Path | Description |
|---|---|---|
| GET | `/extensions` | List uploaded extensions |
| POST | `/extensions` | Upload `{filename, content(base64)}` |
| PATCH | `/extensions/{id}` | Toggle `enabled` |
| DELETE | `/extensions/{id}` | Delete |
| GET | `/profiles/{id}/extensions` | Assigned extension ids |
| PUT | `/profiles/{id}/extensions` | `{ids: [...]}`, profile must be stopped |

Uploads are unpacked to `data/extensions/<id>/` and passed to Camoufox as
temporary add-ons on launch. A `browser_specific_settings.gecko.id` is injected
when the manifest has none.

## Environment sync

Copy cookies + localStorage + IndexedDB from one profile to another. Both
profiles must be stopped.

| Method | Path | Description |
|---|---|---|
| GET | `/profiles/{id}/state-size` | Cookie and storage size |
| POST | `/profiles/{id}/sync-from/{source_id}?parts=cookies,storage` | Overwrite target state |

```bash
curl -X POST .../profiles/pf_b/sync-from/pf_a?parts=cookies,storage \
  -H "Authorization: Bearer $TOKEN"
# -> {"data": {"parts": ["cookies", "storage"], "cookies": 12, "storage": true, "bytes": 39090710}}
```

## Backup / restore

| Method | Path | Description |
|---|---|---|
| GET | `/backup?include_profiles=true` | Zip: `moltskin.db`, `extensions/`, optional `profiles/*/cookies.sqlite` + `storage/` |
| POST | `/backup/restore` | Multipart `file=@backup.zip` |

A database-only backup uses SQLite's online snapshot, so profiles may keep
running. `include_profiles=true` requires everything stopped. It includes Camoufox login state for active and recycled profiles. The manifest lists recycled entries in `trashed_profiles`. Chromium profiles and interrupted permanent deletions appear in `profile_data_excluded` when profile data is requested. Their settings are included in the database snapshot. Restore keeps the
previous database as `moltskin.db.bak`, including its committed WAL changes. Restored environments use the current installation's profile directory. Sessions that were active when the backup was taken are archived as stopped, and historical session records remain available.

## Schedules

| Method | Path | Description |
|---|---|---|
| GET | `/schedules` | List with `next_run_at` |
| POST | `/schedules` | Create |
| PATCH | `/schedules/{id}` | Update / enable / disable |
| DELETE | `/schedules/{id}` | Delete |
| POST | `/schedules/{id}/run` | Run now |

```json
{
  "name": "早班自动开机",
  "action": "launch",
  "mode": "daily",
  "interval_minutes": 60,
  "time_of_day": "09:00",
  "weekdays": [1, 2, 3, 4, 5],
  "target_type": "all",
  "target_ids": []
}
```

`action`: `launch` | `stop`. `mode`: `daily` (time + weekdays) or `interval`
(every `interval_minutes`, 1–1440). `target_type`: `all` | `group` | `ids`.

## In-browser console button

Camoufox profiles can load the built-in `moltskin/builtin/manager-button` extension with the `manager_button` setting. It adds a sidebar entry, a toolbar button and `Alt+Shift+M`. In Moltskin Chromium the native chameleon button beside the address bar opens the local dashboard home.
The scheduler runs on the service, catches up tasks missed by up to 10 minutes,
and records `last_run_at` / `last_result`.

## Batch progress

`POST /profiles/batch` stays synchronous. For long batches use:

```bash
curl -X POST .../profiles/batch-async -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action": "launch", "ids": ["pf_1", "pf_2"]}'
# -> {"data": {"id": "job_...", "total": 2, "state": "running", ...}}

curl .../jobs/job_... -H "Authorization: Bearer $TOKEN"
# -> per-environment state: pending / running / done / failed + error
```

## Import / export

```bash
curl -H "Authorization: Bearer $TOKEN" .../export/profiles -o profiles.json
curl -X POST .../export/profiles -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"ids":["pf_example"]}' -o selected-profiles.json
curl -X POST .../import/profiles -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --data-binary @profiles.json
```

GET exports every environment. POST exports only the requested profile IDs in the same file format. A rename preserves the selection. If an ID no longer exists, the server returns HTTP 409 with `PROFILE_SELECTION_CHANGED` and `missing_ids` so the caller can refresh the selection.

Pass the entire export to the importer to preserve its format version. A Chromium profile with either graphics restriction requires format `9`. A fixed Chromium proxy requires format `8`. Saved platform compatibility preview settings require format `7`. A custom Chromium User-Agent requires format `6`. A pinned Chromium version, audio muting, renderer limit, or JS heap limit requires format `5`. A custom website language, time zone, or initial window size requires format `4`. Geolocation blocking requires `3` and media capture blocking requires `2`. The exporter selects the highest format needed by the selected profiles, starting at `1`. This release reads formats `1` through `9`.

For Chromium, set `proxy_id` on profile creation or patch a stopped profile. An explicit `null` restores the system network. Entries from `GET /api/v1/proxies` with `chromium_compatible: true` have a supported protocol, a valid endpoint and no stored credentials. While a Chromium profile has the proxy assigned, deleting that proxy returns HTTP 409. This also covers profiles in the recycle bin.
Legacy unversioned files remain compatible. An unsupported version returns HTTP 422 before any profiles are created.

The file contains configuration such as the browser engine, group, tags, headless setting, Camoufox preset, Chromium font, media and location policies, and proxy metadata. Proxy passwords and site login data are excluded. Import returns `created`, `renamed` and `skipped`. Each skipped entry identifies its original input row (starting at 1) and the reason.

## Settings and logs

`GET /settings`, `PUT /settings`.

| Key | Default | Meaning |
|---|---|---|
| `max_concurrent` | 10 | Maximum simultaneously running environments |
| `profile_limit` | 200 | Environment quota shown in the UI |
| `daily_open_limit` | 20000 | Daily launch quota shown in the UI |
| `headless` | false | Default headless mode for new environments |
| `open_manager_on_launch` | true | Open the manager as the first tab for Camoufox profiles using the default startup page |
| `proxy_check_url` | `https://ipinfo.io/json` | Proxy check endpoint |
| `proxy_health_check_minutes` | 30 | Background proxy re-check interval, `0` = off |
| `require_proxy_ok` | false | Refuse to launch when the proxy is not healthy |

`GET /logs?page=1&page_size=50&action=profile` — the total row count is returned
in the `X-Total-Count` header so the body stays a plain array.

## Stats

```bash
curl -H "Authorization: Bearer $TOKEN" .../stats
```

```json
{
  "data": {
    "profiles_total": 4, "profiles_running": 1, "profiles_opened": 4,
    "profiles_crashed": 0, "today_opened": 12,
    "max_concurrent": 10, "profile_limit": 200, "daily_open_limit": 20000,
    "groups_total": 2, "tags_total": 3,
    "proxies_total": 5, "proxies_ok": 4, "proxies_failed": 1
  }
}
```

## MCP

Start the service, then configure your MCP client:

```json
{
  "mcpServers": {
    "moltskin": {
      "command": "/path/to/Moltskin/.venv/bin/python",
      "args": ["/path/to/Moltskin/moltskin/services/core/mcp_server.py"],
      "env": {"MOLTSKIN_API": "http://127.0.0.1:8765"}
    }
  }
}
```

Tools: `list_profiles`, `get_profile`, `create_profile`, `launch_profile`,
`stop_profile`, `profile_status`, `verify_profile`, `run_self_test`,
`reset_fingerprint`, `get_cookies`, `set_cookies`, `copy_cookies`,
`list_extensions`, `set_profile_extensions`, `list_schedules`,
`create_schedule`, `run_schedule`, `list_proxies`, `create_proxy`,
`check_proxy`, `list_groups`, `create_group`, `batch_profiles`, `list_tags`,
`stats`, `engine_info`.

## Python SDK

```python
from moltskin.client import MoltskinClient

with MoltskinClient() as c:
    profile = c.create_profile("shop-ph-01", proxy_id="px_...")
    c.launch(profile["id"])
    print(c.status(profile["id"]))
    c.stop(profile["id"])
```
