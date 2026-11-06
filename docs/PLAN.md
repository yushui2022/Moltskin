# Moltskin

> **自用指纹浏览器 · 立项规划与技术路线**
> 引擎：**Camoufox**（Firefox，MPL-2.0） · 产品层：**Moltskin**（自研）
> 调研快照：**2026-10-25** · 状态：**产品 v0.1.0 已实现（2026-11-06）**，内核基线回归仍待做
>
> 当前代码、安装与快速开始见根目录 [README](../README.md)。

---

## 0. TL;DR

| 项 | 结论 |
|---|---|
| 做什么 | 给自己定制的指纹浏览器：多身份隔离 + 指纹一致性 + 代理绑定 + 可自检 |
| 用什么 | **引擎用 Camoufox（不 rebrand），产品层自己写** |
| 为什么 | 指纹参数走运行时配置（多数改动不用重编浏览器）、补丁按指纹面拆分（40+ 个独立 patch）、自带指纹回归探针与 CI 守卫、有上游升级指南、MPL-2.0 允许应用层闭源、构建成本比 Chromium 低一个量级 |
| 名字 | `Moltskin` = 蜕下的皮：每次会话换一层皮，骨子里是同一只。CLI 名 `moltskin`，PyPI / npm 均未被占用（实测） |
| 第一件事 | **M0：把 Camoufox 的 `build-tester` 与 `ci/run_patch_guards.py` 跑通，固化"指纹基线"**，之后任何改动都必须过 diff |

---

## 1. 命名与边界

### 1.1 双名制（engine / product）

| 层 | 名字 | 规则 |
|---|---|---|
| 引擎 | `camoufox`（保持原名） | **不 rebrand**：不改 `application.ini`、`branding.nsi`、UA 里的 app name、窗口标题、图标资源、菜单文案 |
| 产品 | `Moltskin` | 仓库、CLI、GUI、包名、文档 |

**为什么引擎绝不能 rebrand**：Camoufox 的全部价值在于"看起来就是原版 Firefox"。一旦改品牌，就会引入与真机不一致的暴露面（产品名、窗口标题、UA app name、图标指纹、菜单文案），把自己从"原版 Firefox"降级成"某个不知名的 Firefox 变体"，**反而更可疑**。

> 参照：Chromium / Chrome、nginx / OpenResty —— 引擎名与产品名分离，改上层不改内核身份。

### 1.2 命名规范

- CLI 名 ≤ 8 字符、全小写、无连字符、单读音（`moltskin`、简写 `msk`）
- 统一词根：`moltskin`(CLI) / `moltskin-core` / `moltskin-ui` / `moltskin-sdk`
- 名字中**不出现** `anti` / `fingerprint` / `spoof` / `stealth` —— 进程名、安装路径、窗口标题都是暴露面
- 中文呼称可用"蜕" / "蜕皮"，但仅用于交流，不落到代码与路径

### 1.3 一句话定位

> 不是"比商业指纹浏览器更强"，而是"**我完全掌控、可验证、可复现**的指纹环境管理器"。

---

## 2. 需求与取舍（自用场景）

### 2.1 明确要做

| # | 需求 | 说明 |
|---|---|---|
| R1 | 多身份隔离 | 一个 profile = 一套持久化指纹 + 独立 cookie/storage 目录 + 一个固定代理出口 |
| R2 | 指纹一致性 | 同一 profile 每次启动，指纹逐项**完全一致**（随机化 = 自曝） |
| R3 | 三层自洽 | 出口 IP ↔ 时区 ↔ 语言 ↔ Geolocation ↔ WebRTC ↔ 字体，必须同源 |
| R4 | 可自检 | 随时导出一份"当前环境指纹快照"，并能与历史版本 diff |
| R5 | 可脚本化 | CLI + 本地 HTTP API +（可选）MCP，便于自动化与 AI agent 驱动 |
| R6 | 可复现 | 环境/版本/代理/指纹档案全部落库，换机可重建 |

### 2.2 明确不做（至少 MVP 不做）

- 代理 IP 池与售卖、云同步、团队协作与席位管理
- 账号批量养号 / RPA 编排
- **不承诺**能过商业级反爬风控 —— 这不是纯软件层能保证的事

---

## 3. 选型：开源指纹浏览器实测调研

### 3.1 结论表

| 项目 | 许可 | 引擎源码可改？ | 预编译产物 | 结论 |
|---|---|---|---|---|
| **daijro/camoufox** | MPL-2.0 | ✅ `browser/patches/` + `additions/` | Win/mac/Linux zip | ✅ **选为引擎** |
| adryfish/fingerprint-chromium | BSD-3 | ⚠️ 源码只在版本 tag，且延迟一个版本 | Win/mac/Linux | 备选（Chromium 路线） |
| heretic-tech/apostate | GPL-3.0 | ✅ `patches/series` + `corpus/` | Win/mac/Linux | 只读参考 |
| Virtual-Browser/VirtualBrowser | BSD-3 | ❌ 无 patches 目录，引擎为二进制 | Win/mac/Linux | 功能层参考 |
| ProxyShard/ShardBrowser | MIT | ❌ Tauri，无 Chromium 补丁 | Win/mac/Linux | 功能层/API 设计参考 |
| chienbm98/foxprofile | MIT | ❌ 引擎靠上游 | 无（需 Python 环境） | ✅ **应用层架构参考** |
| polyackiy/camoufox-profile-manager | MIT | ❌ | Win/mac/Linux | 竞品形态参考 |
| botswin/BotBrowser | 名义 MIT | ❌ | Win/mac/Linux | ❌ 需商业授权，排除 |
| furyteamtop/fury-antidetect-browser | AGPL-3.0 | ✅ | Win/mac | ❌ AGPL，排除 |

### 3.2 逐个评估（含"硬伤"证据）

**① daijro/camoufox —— 选定为引擎**

- 目录结构（实测）：`browser/patches/*.patch`、`browser/additions/camoucfg/MaskConfig.hpp`、`browser/settings/properties.json`、`python/src/camoufox/{fingerprints.py,fpgen.yml,fingerprint-presets.json,webgl.py,locales.py,fonts.json,voice-manifests.json}`、`typescript/src/*`、`browser/tests/build-tester`、`ci/run_patch_guards.py`、`docs/patch-upgrading-guide.md`、`browser/multibuild.py`、`browser/Dockerfile`
- 版本：`camoufox-156.0.1-beta.*`（beta，需注意）
- 强项见 §3.4

**② adryfish/fingerprint-chromium —— Chromium 路线备选**

- 硬伤（实测）：`main` 分支**只有** `LICENSE`、`README.md`、`README-ZH.md`、`qqgroup.png` ——**补丁集不在主分支**，源码只在版本 tag，并且**延迟一个版本**发布（作者说明：防止二次打包牟利）
- 只能通过命令行参数调既有项：`--fingerprint=<32位种子>`、`--fingerprint-platform`、`--fingerprint-brand` 等；**新增一项指纹必须改 C++ 并重编**
- 作者明确声明不提供技术支持
- 可用性：Chromium 150 有 Win 安装包/ZIP、Linux AppImage/TAR，macOS 只在旧版本

**③ heretic-tech/apostate —— 唯一完整公开 Chromium 指纹补丁集**

- 有 `patches/series`、`build/`、`scripts/`、`corpus/`（真机 GPU 家族）、`capture/`（真机抓取工具）
- **GPL-3.0** → 闭源分发不可行；仅作**只读参考**（思路可学，代码不合入）

**④ Virtual-Browser/VirtualBrowser —— 成品 GUI 最好看，但指纹层黑盒**

- 目录：`server/`、`worker/`、`automation/`、`assets/` —— **无 patches 目录**，引擎以二进制形式发布
- 能改：功能层、UI、自动化封装；**改不了指纹实现本身**
- BSD-3 可闭源，中文界面，兼容 Playwright

**⑤ ProxyShard/ShardBrowser —— API/SDK 设计值得借鉴**

- 目录：`src-tauri/`、`src/`、`crates/`、`sdks/`、`mcp/`、`team-server/`、`openapi.yaml` —— 标准 Tauri 应用，**无 Chromium 补丁**
- MIT 只覆盖 Launcher 侧；引擎为预编译产物
- 值得抄：`openapi.yaml` 的 API 形状、`mcp/` 的 agent 接入方式

**⑥ chienbm98/foxprofile —— 应用层架构参考（MIT）**

- 依赖栈（实测 `requirements.txt`）：`flet`（桌面 UI）+ `camoufox[geoip]`（引擎）+ `fastapi`/`uvicorn`（本地 API）+ `mcp`（agent 接入）+ `aiohttp`
- 结构：`src/`（应用）、`chrome_engine/`（fingerprint-chromium 封装）、`docs/`、`tests/`、`foxprofile_mcp.py`
- 优点：把"profile 管理 + 代理 + GeoIP 一致性校验 + REST + MCP"四件事拆得很干净
- 缺点：约 160 star、单人维护、Releases 无二进制、引擎完全依赖上游
- **用法：抄结构，不 fork 当产品核心**

**⑦ 排除项**

- `botswin/BotBrowser`：README 直指 Pricing / Enterprise，"源码访问"需邮件申请 → 不能作为基底
- `furyteamtop/fury-antidetect-browser`：AGPL-3.0，SaaS 也需开源
- `aliasmode/aliasmode`、`True-Utopian/vesper-antidetect-browser`、`brodev3/antidetect`：声明免费开源，但 **Releases 无二进制**，需自行构建
- `omegaee/my-fingerprint`：纯扩展方案，易被检测，不作为主力

### 3.3 决策矩阵（1–5 分）

| 维度 | camoufox | fingerprint-chromium | apostate | VirtualBrowser | ShardBrowser | foxprofile |
|---|---|---|---|---|---|---|
| 指纹可改深度 | **5** | 3 | 5 | 1 | 1 | 1 |
| 改造成本（越高越省力） | **4** | 2 | 2 | 4 | 4 | 5 |
| 许可可闭源 | **5**(MPL) | 5(BSD) | 1(GPL) | 5(BSD) | 5(MIT) | 5(MIT) |
| 构建/升级成本（越高越省） | **4** | 1 | 1 | 5 | 5 | 5 |
| 自带指纹回归测试 | **5** | 2 | 3 | 1 | 1 | 2 |
| 上游活跃度 | 4 | 3 | 3 | 4 | 4 | 3 |
| **加权结论** | ✅ 引擎 | 备选 | 只读参考 | 功能参考 | 功能参考 | 应用层参考 |

### 3.4 为什么最终选 Camoufox 作为引擎（7 条）

1. **指纹值不写死在编译产物里**
   `python/src/camoufox/fingerprints.py`、`fpgen.yml`、`fingerprint-presets.json`、`webgl.py`、`locales.py`、`fonts.json`、`voice-manifests.json` 负责生成指纹，经 `browser/settings/properties.json` 与 `browser/additions/camoucfg/MaskConfig.hpp` 注入浏览器。
   → **改大部分指纹参数 = 改 JSON / Python，不用重编浏览器**。Chromium 系只能开关既有项，新增必须动 C++ 并重编。

2. **补丁按指纹面拆分（40+ 个独立 patch）**
   `navigator-spoofing`、`webgl-spoofing`、`audio-context-spoofing`、`timezone-spoofing`、`locale-spoofing`、`screen-spoofing`、`font-list-spoofing`、`font-hijacker`、`webrtc-ip-spoofing`、`media-device-spoofing`、`speech-voices-spoofing`、`touchscreen-fingerprint-spoofing`、`geolocation-spoofing`…
   → 加功能 = 加一个 patch 进 `series`，不污染其他改动，便于单独开关与回滚。

3. **自带指纹回归测试网（最被低估的一点）**
   `browser/tests/build-tester/`（TS 探测页 + grading 评分）、`ci/run_patch_guards.py`、`browser/tests/playwright/patches/*`（数十个针对性探针）。
   → 魔改指纹最怕"改了 A 泄露了 B"，**没有这套网你根本不知道坏没坏**；上面其他项目全都没有。

4. **有升级路径文档**
   `docs/patch-upgrading-guide.md` 写清 Firefox 升版本时如何 rebase 补丁 —— 这是长期维护的生命线。

5. **双语言客户端已对齐**
   `python/` 与 `typescript/` 完全对应（含 `launchServer.js`）→ GUI 用 Tauri/Electron 时可直接用 TS 那套，不必套 Python 子进程。

6. **构建成本低一个量级**
   Firefox + `browser/multibuild.py` + `browser/Dockerfile`，对比 Chromium fork（源码 100GB+、单核数小时编译）。

7. **许可可接受：MPL-2.0（文件级 copyleft）**
   应用层可闭源，只需公开"你改动过的 Camoufox 自身文件"（详见 §10）。

### 3.5 什么情况下应该改选 Chromium 路线

出现下列任一情况，**停下来重新评估**（转 fingerprint-chromium tag fork，或愿意开源则 fork apostate）：

- [ ] 目标站点/风控体系主要比对 Chrome 侧特征（UA-CH / Client Hints、WebGPU、Chrome 扩展行为）
- [ ] 目标站对 Firefox 有显式降权或直接标记
- [ ] 你需要 Chrome 专属能力（特定扩展、DRM/Widevine 一致性、Chrome 特有 JS 行为）
- [ ] 实测发现 Firefox 基线在该站的通过率显著劣于 Chrome

> **顺序不能错**：先确定"检测基线 + 目标站风控侧"，再定内核。不是先选内核再想办法过检测。

---

## 4. 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│ L4  UI 层        Moltskin GUI（Flet 或 Tauri+React）          │
├─────────────────────────────────────────────────────────────┤
│ L3  接口层       CLI (moltskin) │ 本地 HTTP API │ MCP Server  │
├─────────────────────────────────────────────────────────────┤
│ L2  核心层       profile store │ launcher │ proxy binder      │
│                  geo-consistency │ fingerprint preset engine  │
├─────────────────────────────────────────────────────────────┤
│ L1  引擎层       camoufox（保持原名，不 rebrand）             │
├─────────────────────────────────────────────────────────────┤
│ L0  布局层       每个 profile 独立 user-data-dir + 独立代理   │
└─────────────────────────────────────────────────────────────┘
```

### 4.1 目录规划

```
Moltskin/
├─ README.md              # 本文件（立项书 / 规划）
├─ docs/
│  ├─ ARCHITECTURE.md     # 架构细化
│  ├─ FINGERPRINT.md      # 指纹项清单与一致性规则
│  └─ BASELINE.md         # 指纹基线报告（M0 产出）
├─ patches/               # 我们对 camoufox 的补丁（对应 MPL 义务）
│  ├─ series
│  └─ 0001-xxx.patch
├─ core/                  # L2 核心层（profile / 代理 / 一致性 / 预设）
│  ├─ store.py            # 落库（SQLite）
│  ├─ launcher.py         # 启动与生命周期
│  ├─ proxy.py            # 代理绑定与出口 IP 校验
│  ├─ coherence.py        # IP/时区/语言/geo/WebRTC 一致性
│  └─ presets/            # 指纹预设（设备家族档案）
├─ cli/                   # L3 CLI
├─ api/                   # L3 本地 HTTP API
├─ mcp/                   # L3 MCP server（可选）
├─ ui/                    # L4 GUI（可选，后置）
└─ probes/                # 自研指纹探针页 + diff 工具
```

### 4.2 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 引擎品牌 | 保持 `camoufox` 原名 | 保 stock parity（§1.1） |
| 指纹存储 | 每个 profile 一个固定 seed + 显式字段 | 可复现、可 diff（R2/R6） |
| 指纹生成 | 复用 camoufox 的 `fpgen`，上层加我们自己的 preset 层 | 不重造轮子，只做一致性收敛 |
| 代理 | 每 profile 强绑定，启动前实测出口 IP | 避免"指纹对了 IP 错了"（R3） |
| 一致性判定 | 启动时计算 `coherence score`，低于阈值拒绝启动 | 把"看起来像"变成可测量 |
| 自动化接口 | CLI 优先，其次本地 HTTP API，MCP 后置 | 自用场景 80% 操作在 CLI |
| GUI | 后置到 P2，先 CLI + API | 避免早期把时间烧在界面上 |
| 自检 | 内置探针页，输出 JSON 快照，跨会话 diff | R4；也是 M0 的交付物 |

---

## 5. 指纹设计原则（铁律）

1. **一致性 > 随机性**：同一个 profile 每次启动必须逐项一致。随机化 = 主动自曝。
2. **三层同源**：出口 IP ↔ 时区 ↔ 语言 ↔ Geolocation ↔ WebRTC ↔ 字体族，全部由同一份"身份档案"派生。
3. **引擎级 > 注入级**：优先 C++ / 引擎内 patch；JS 注入（`addInitScript` / content script）是最后手段，因为它本身就留下可检测痕迹。
4. **不改引擎品牌面**：见 §1.1。
5. **可测量**：任何指纹改动都必须过探针基线 + diff，凭"我觉得像"不算数。
6. **代理质量 > 指纹细节**：机房 IP 在任何浏览器上都容易吃验证码；住宅/移动代理的优先级高于调一个 WebGL 参数。

---

## 6. 功能规划

### MVP（自用最小闭环）

| # | 功能 | 说明 |
|---|---|---|
| F1 | profile CRUD + 落库 | seed、指纹字段、代理、备注持久化 |
| F2 | CLI | `moltskin new / up / ls / rm / check` |
| F3 | 代理绑定 + 出口 IP 校验 | 启动前实测，不符则告警/阻断 |
| F4 | 自动 geo 对齐 | 按出口 IP 推导时区/语言/geo，允许逐项 pin 死 |
| F5 | 目录隔离 | 每 profile 独立 user-data-dir，互不串味 |
| F6 | 指纹快照 + diff | 探针页输出 JSON，跨会话/跨 profile 比对 |
| F7 | 本地 HTTP API | 供脚本调用（对齐 `openapi.yaml` 风格） |

### P1

- MCP server（让 AI agent 能驱动 profile）
- 批量启动 / 批量检查
- 指纹预设库（设备家族档案，落 `core/presets/`）
- cookie / 书签导入导出
- profile 加密备份与换机重建

### P2

- 桌面 GUI（Flet 起步，必要时 Tauri+React）
- 代理池健康度看板
- 快照回滚 / 时间线
- 多机同步（自用可选）

---

## 7. 魔改工作流

### 7.1 新增一个指纹补丁

1. 确认改动是否真需要 C++：**能通过 `properties.json` / python 侧配置解决的就不要动 patch**（§3.4 第 1 条）
2. 在 `patches/` 下新增 `000N-<面>-<意图>.patch`，登记到 `patches/series`
3. 同步补一条探针：`browser/tests/playwright/patches/<name>.py` 或我们自己的 `probes/`
4. 跑回归：`build-tester` + `run_patch_guards` + 探针全绿
5. 记录到 `docs/FINGERPRINT.md`：改了哪个面、为什么、对哪些检测项有影响

### 7.2 回归基线（M0 交付）

- 输入：一组固定 seed + 固定代理出口
- 输出：`docs/BASELINE.md` —— 各检测站（BrowserScan / BrowserLeaks / FingerprintJS / sannysoft）的逐项结果
- 用途：此后**每次改动都与之 diff**，任何一项"变红"即阻断合并

### 7.3 上游升级（Firefox 大版本）

1. 读 `docs/patch-upgrading-guide.md`
2. rebase `patches/series` 到新版本
3. 跑完整回归；`run_patch_guards` 会拦住典型的"补丁静默失效"
4. 更新 `docs/BASELINE.md` 并归档旧基线

---

## 8. 里程碑

| 里程碑 | 内容 | 出口标准 |
|---|---|---|
| **M0** | 环境 + 指纹基线 | camoufox 本地可构建；`build-tester`、`run_patch_guards` 跑通；产出 `docs/BASELINE.md` |
| **M1** | profile store + CLI + 目录隔离 | 能创建/启动/管理多 profile，指纹跨会话一致 |
| **M2** | 代理绑定 + geo 一致性 | 出口 IP ↔ 时区/语言/geo/WebRTC 全对齐，不符则阻断 |
| **M3** | 指纹快照 + diff | 一条命令出 JSON 快照并与历史/其他 profile 比对 |
| **M4** | 第一个自定义补丁 | 针对实际目标站的一个具体问题，patch + 探针 + 回归全绿 |
| **M5** | 本地 API + MCP；GUI 视需要 | 脚本/agent 可驱动；GUI 后置 |

---

## 9. 风险与对策

| 风险 | 级别 | 对策 |
|---|---|---|
| Camoufox 当前版本为 beta（156.0.1-beta） | 中 | 锁定已验证版本，升级走 M0 基线回归，不追新 |
| 上游基本单人主导，存在停更风险 | 中 | 保留 `patches/` 完整可独立构建；文档化构建步骤；必要时接管 fork |
| Firefox 大版本 rebase 是持续成本 | 中 | `patch-upgrading-guide` + `run_patch_guards` + 基线 diff，把成本前置到流程里 |
| **目标站风控偏 Chrome 侧** | **高** | **§3.5 触发即重新评估内核选型** —— 这是最大的方向性风险 |
| 代理质量不足导致验证码 | 高 | 住宅/移动代理优先；datacenter IP 不承诺通过率 |
| 自研补丁引入新的可检测痕迹 | 中 | 每个 patch 必配探针；基线 diff 变红即回滚 |
| 检测方持续进化 | 高 | 接受"没有永久方案"；把基线回归做成常态而非一次性 |

---

## 10. 许可证与合规

### 10.1 MPL-2.0（Camoufox）要点

- **文件级** copyleft：你修改过的 Camoufox **自身文件**，分发时需以 MPL-2.0 公开其源码
- 你的**独立应用层**（`core/`、`cli/`、`ui/` 等）可以保持闭源
- 我们的做法：把对引擎的改动全部收敛到 `patches/` 下的 patch 文件并随仓库公开，天然满足义务
- 分发时（若未来分发）需附 MPL 声明与源码获取方式
- **本节非法律意见**；正式分发前请复核 [MPL-2.0 原文](https://www.mozilla.org/en-US/MPL/2.0/)

### 10.2 第三方许可清单（需随仓库维护）

| 组件 | 许可 | 用法 | 义务 |
|---|---|---|---|
| camoufox | MPL-2.0 | 引擎 | 改动文件需公开 |
| Firefox 源码 | MPL-2.0 | camoufox 上游 | 同上 |
| foxprofile（若抄结构） | MIT | 仅参考/少量代码 | 保留版权与许可声明 |
| apostate（若参考） | GPL-3.0 | **只读参考** | 不复用代码，避免传染 |

### 10.3 用途合规

本工具用于**隔离与隐私保护**是正当用途。用于规避平台风控批量运营账号，可能违反目标平台的服务条款；请自行评估使用边界。

---

## 11. 待决策（Open Questions）

- [ ] **目标站点清单 + 其风控偏 Chrome 还是 Firefox？**（决定 §3.5 是否触发，最高优先级）
- [ ] GUI 技术栈：Flet（快）vs Tauri+React（可控、可复用 TS 客户端）？
- [ ] profile 数据是否需要静态加密？加密强度与"忘记密码即全丢"的取舍？
- [ ] 是否需要 MCP / AI agent 驱动？
- [ ] 是否只有自己用？若未来给他人用，需重新审视 §10 与命名暴露面
- [ ] 基准设备档案（Windows 10/11 + 哪几档 GPU / 分辨率）确定几套？

---

## 附录 A：调研原始数据（2026-10-25 快照）

| 仓库 | ★ | 许可 | 最近推送 | 最新产物 |
|---|---|---|---|---|
| daijro/camoufox | ~12.8k | MPL-2.0 | 2026-10-25 | `camoufox-156.0.1-beta.36-{win,mac,lin}` zip |
| CloakHQ/CloakBrowser | ~32k | — | — | Playwright drop-in，无桌面 GUI |
| adryfish/fingerprint-chromium | ~3.2k | BSD-3 | 2026-10-02 | Chromium 150：Win installer/ZIP、Linux AppImage/TAR |
| Virtual-Browser/VirtualBrowser | ~3.2k | BSD-3 | 2026-10-16 | 2.4.0：Win setup、mac dmg、Linux deb |
| ProxyShard/ShardBrowser | ~1.4k | MIT | 2026-10-25 | Launcher 2.1.2：Win portable/MSI、mac dmg、Linux rpm |
| botswin/BotBrowser | ~2.6k | 名义 MIT | 2026-10-23 | 156.0.8078.47 全平台 —— **需商业授权** |
| heretic-tech/apostate | ~65 | GPL-3.0 | 2026-10-02 | 155.0.8059.31：Win/mac/Linux |
| chienbm98/foxprofile | ~160 | MIT | 2026-10-22 | v2.3.1 无二进制（Flet + FastAPI + MCP） |
| polyackiy/camoufox-profile-manager | ~79 | MIT | 2026-10-25 | 0.6.0：Win setup、mac dmg、Linux deb |
| furyteamtop/fury-antidetect-browser | ~87 | AGPL-3.0 | 2026-10-24 | 0.4.15 全平台 |
| aliasmode/aliasmode | ~77 | Apache-2.0 | 2026-10-25 | 无 |
| True-Utopian/vesper-antidetect-browser | ~87 | MIT | 2026-10-13 | 无 |
| brodev3/antidetect | ~111 | MIT | 2026-10-16 | 无 |
| omegaee/my-fingerprint | ~1.5k | Apache-2.0 | 2026-09-27 | 浏览器扩展（Chrome/Firefox） |

**名字可用性实测**：`moltskin` / `ninefox` / `foxmolt` / `reynardfox` / `corsacfox` / `denfox` 在 PyPI 与 npm 均为空；`molt`、`vixen`、`silo`、`burrow`、`fennec`、`kitfox`、`vulpine` 已被占用。

---

## 附录 B：名词表

| 词 | 含义 |
|---|---|
| profile | 一套绑定的身份：指纹档案 + 独立 user-data-dir + 代理出口 |
| seed | 指纹生成的确定性种子；同 seed 必得同指纹 |
| stock parity | 与官方原版 Firefox 的行为/特征一致度 |
| coherence score | IP / 时区 / 语言 / geo / WebRTC 的一致性评分 |
| 探针（probe） | 用已知检测手段测自家环境的页面/脚本 |
| 基线（baseline） | 某次已知良好状态下全部指纹项的冻结快照 |
| rebrand | 修改浏览器品牌面（本项目中**禁止**） |
