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

Profiles accept `startup_page` and `block_images` on creation and update. Changes take effect at the next launch. `startup_page` accepts the following values.

| Value | First tab |
|-------|-----------|
| `default` | Camoufox follows the global manager setting. Chromium opens a blank tab. |
| `dashboard` | Open the local dashboard home on the configured service port. Hosts with `MOLTSKIN_OPEN_MANAGER=0` open a blank tab. |
| `blank` | Open a blank tab in either engine. |

`block_images` defaults to `false`. Set it to `true` to disable page image loading. Existing profiles keep `default` and continue loading images. Both preferences travel with configuration exports and duplicates.

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
| PATCH | `/proxies/{id}` | Update |
| DELETE | `/proxies/{id}` | Delete and unbind |
| POST | `/proxies/{id}/check` | Check exit IP / country / timezone / latency |
| POST | `/proxies/check-batch` | `{"ids": [...]}` |

Each proxy includes `used_by`, the number of environments bound to it.

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

Pass the entire export to the importer to preserve its format version. If a selected profile blocks geolocation, the export uses version `3`. Otherwise it uses version `2` if media capture is blocked, or `1` for standard policies. A version-aware older importer rejects an unsupported file before writing profiles. This release reads all three versions.
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
