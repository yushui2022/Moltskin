# Launch Playbook

## Positioning

> **Moltskin — self-hosted multi-profile browser with stable fingerprints, proxies and an MCP server.**

For developers, scraping teams and AI-agent builders who need isolated browser
profiles without a cloud account or a per-profile subscription.

## Before posting

- [ ] Repository is public and `README.md` renders correctly.
- [ ] `docker compose up -d` works from a clean machine.
- [ ] Screenshots are current (`docs/screenshots/`).
- [ ] License, NOTICE, CONTRIBUTING and SECURITY are present.
- [ ] Issues and Discussions are enabled.
- [ ] A pinned issue lists known limitations.
- [ ] Version tag `v0.1.0` exists.
- [ ] No real cookies, proxy credentials or tokens in the repo.
- [ ] Demo data uses synthetic names.

## 60-second demo

1. `docker compose up -d` (fast cut).
2. Open `http://127.0.0.1:8765`.
3. Create a profile with a proxy.
4. Open the detail modal: fingerprint preview, checks, self-test screenshot.
5. Create two more profiles, select all, batch launch.
6. Show the three browser windows.
7. Cut to the MCP config, ask Claude: “list Moltskin profiles and launch shop-ph-01”.
8. End on the README URL and star prompt.

## Show HN

**Title:** Show HN: Moltskin – self-hosted anti-detect browser for AI agents

**Body:**

I built Moltskin because every anti-detect browser I tried was closed source and
cloud-only, and browser-use only speaks Chromium/CDP.

Moltskin is a local-first profile manager built on Camoufox (open-source Firefox
fork with C++-level fingerprint patches). It gives each profile its own
`user_data_dir`, proxy and stable fingerprint preset, then exposes the whole
thing over a local API and an MCP server.

What works today:
- create / launch / stop profiles, persistent cookies
- proxy + GeoIP checks
- fingerprint preview, consistency checks, CreepJS self-test
- batch operations, JSON import/export
- MCP server (15 tools) and a Python SDK

What is missing: page-level agent actions (click/type over MCP), a desktop app,
and Windows packaging.

`docker compose up -d`, then http://127.0.0.1:8765.

Happy to answer questions about the fingerprint model, the process-per-profile
design, or why Firefox instead of Chromium.

## Reddit r/webscraping

**Title:** I open-sourced a self-hosted anti-detect browser manager (Camoufox + MCP)

**Body:**

Most anti-detect browsers are closed source and charge per profile. I wanted
something I could run locally, audit, and plug into browser agents.

Moltskin manages isolated Camoufox profiles: one process, one data directory and
one stable fingerprint preset per profile. It has a proxy pool with exit-IP and
timezone checks, a pre-launch consistency check, and a CreepJS self-test that
saves a screenshot.

For automation it exposes a local REST API, a Python SDK and an MCP server, so
Claude/Cursor can create and launch profiles directly.

It is early (v0.1.0). I would especially appreciate feedback on:
- fingerprint consistency across launches
- proxy + timezone edge cases
- what MCP tools you would need for real scraping workflows

Docker quickstart in the README.

## X / Twitter thread

1. Anti-detect browsers are either closed source or cloud-only. I open-sourced
   a local-first one built on Camoufox.
2. Each profile gets its own process, data directory, proxy and a fingerprint
   preset that stays stable across launches.
3. Fingerprint preview + consistency checks + CreepJS self-test, right in the UI.
4. It speaks MCP. Claude/Cursor can list, create, launch and stop profiles.
5. `docker compose up -d` → http://127.0.0.1:8765
6. v0.1.0, early. Feedback welcome. GitHub: <link>

## 中文社群（V2EX / 即刻 / 小红书）

> 开源了一个本地优先的多环境指纹浏览器：Moltskin
>
> 基于 Camoufox（开源 Firefox 内核），每个环境独立进程、独立目录、固定指纹，
> 支持代理检测、启动前一致性检查、CreepJS 自检截图、批量操作、API 和 MCP。
>
> 一条命令：`docker compose up -d`，打开 127.0.0.1:8765。
>
> 适合爬虫、AI Agent、跨境多环境管理。早期版本，欢迎提 issue。

## Do not

- Do not market it as “100% undetectable”.
- Do not show bulk account registration, captcha farms or platform evasion.
- Do not claim support for platforms you have not tested.
- Do not publish screenshots with real account names or proxy credentials.

## Metrics to watch

- Docker quickstart completion (issues mentioning setup failures)
- Stars per source (HN / Reddit / X)
- MCP configuration issues
- Top requested missing feature (likely batch import or agent actions)
