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

## Profiles

| Method | Path | Description |
|---|---|---|
| GET | `/profiles` | List; `status`, `q`, `field`, `op`, `tag`, `group_id`, `sort`, `order`, `page`, `page_size` |
| POST | `/profiles` | Create |
| GET | `/profiles/{id}` | Detail with `fingerprint_preview` |
| PATCH | `/profiles/{id}` | Update name / group / tags / remark / os / headless |
| DELETE | `/profiles/{id}?delete_data=true` | Delete |
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

```bash
curl -X POST .../profiles/pf_.../launch -H "Authorization: Bearer $TOKEN"
curl -X POST .../profiles/pf_.../stop   -H "Authorization: Bearer $TOKEN"
```

### Verify

```bash
curl -X POST .../profiles/pf_.../verify -H "Authorization: Bearer $TOKEN"
```

```json
{
  "data": {
    "ok": true,
    "checks": [
      {"name": "系统 / UA", "ok": true,  "detail": "配置 windows，UA 显示 windows"},
      {"name": "屏幕",      "ok": true,  "detail": "1920x1080 @ 24bit"},
      {"name": "代理连通",  "ok": true,  "detail": "203.0.113.7 · PH · 180ms"},
      {"name": "时区",      "ok": true,  "detail": "跟随代理出口"}
    ],
    "warnings": []
  }
}
```

### Self-test

```bash
curl -X POST .../profiles/pf_.../self-test -H "Authorization: Bearer $TOKEN"
```

Returns `image_url` (`/selftest/<file>.png`), extracted CreepJS text and timestamp.
The profile must be stopped.

### Batch

```json
{"action": "launch", "ids": ["pf_1", "pf_2"]}
{"action": "move", "ids": ["pf_1"], "group_id": "grp_1"}
{"action": "tag", "ids": ["pf_1"], "tags": ["ph"]}
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

Cookies are read from and written to each profile's Firefox `cookies.sqlite`,
so importing does not require launching the browser. The profile must be
stopped to write.

| Method | Path | Description |
|---|---|---|
| GET | `/profiles/{id}/cookies?domain=` | List cookies |
| PUT | `/profiles/{id}/cookies` | Import |
| GET | `/profiles/{id}/cookies/export?format=json\|netscape` | Download |
| POST | `/profiles/{id}/cookies/copy-from/{source_id}` | Merge from another profile |
| DELETE | `/profiles/{id}/cookies` | Clear |

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
  "time_of_day": "09:00",
  "weekdays": [1, 2, 3, 4, 5],
  "target_type": "all",
  "target_ids": []
}
```

`action`: `launch` | `stop`. `target_type`: `all` | `group` | `ids`.
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
curl -X POST .../import/profiles -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"profiles": [...]}'
```

Export includes groups, tags, fingerprint presets and proxy metadata; proxy
passwords are not exported.

## Settings and logs

`GET /settings`, `PUT /settings`.

| Key | Default | Meaning |
|---|---|---|
| `max_concurrent` | 10 | Maximum simultaneously running environments |
| `profile_limit` | 200 | Environment quota shown in the UI |
| `daily_open_limit` | 20000 | Daily launch quota shown in the UI |
| `headless` | false | Default headless mode for new environments |
| `open_manager_on_launch` | true | Open the manager as the first tab |
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
