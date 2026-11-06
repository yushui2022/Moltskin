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
| GET | `/profiles` | List; `status`, `q`, `page`, `page_size` |
| POST | `/profiles` | Create |
| GET | `/profiles/{id}` | Detail with `fingerprint_preview` |
| PATCH | `/profiles/{id}` | Update name / group / tags / remark / os / headless |
| DELETE | `/profiles/{id}?delete_data=true` | Delete |
| POST | `/profiles/{id}/duplicate` | Duplicate with a new fingerprint |
| POST | `/profiles/{id}/launch` | Launch |
| POST | `/profiles/{id}/stop` | Stop |
| GET | `/profiles/{id}/status` | Live status |
| POST | `/profiles/{id}/verify` | Consistency checks |
| POST | `/profiles/{id}/self-test` | CreepJS screenshot |
| POST | `/profiles/batch` | Batch action |

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

## Proxies

| Method | Path | Description |
|---|---|---|
| GET | `/proxies` | List |
| POST | `/proxies` | Add (`http`, `https`, `socks5`) |
| PATCH | `/proxies/{id}` | Update |
| DELETE | `/proxies/{id}` | Delete and unbind |
| POST | `/proxies/{id}/check` | Check exit IP / country / timezone / latency |
| POST | `/proxies/check-batch` | `{"ids": [...]}` |

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

- `GET /settings`, `PUT /settings`
- `GET /logs?page_size=100`

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
`list_proxies`, `create_proxy`, `check_proxy`, `list_groups`, `create_group`,
`batch_profiles`, `engine_info`.

## Python SDK

```python
from moltskin.client import MoltskinClient

with MoltskinClient() as c:
    profile = c.create_profile("shop-ph-01", proxy_id="px_...")
    c.launch(profile["id"])
    print(c.status(profile["id"]))
    c.stop(profile["id"])
```
