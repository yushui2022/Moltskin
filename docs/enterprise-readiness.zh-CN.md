# Moltskin 离企业团队试用还有多远

评估日期为 2026-11-25，代码基线为开发分支 `feat/chromium-engine` 的 [71f3827](https://github.com/yushui2022/Moltskin/tree/71f382760b519f8189c5f75be3beefd62e5c6653)。本文评估该分支可交付的功能。竞品对照来自各家公开文档。

**当前适合在 Mac 上做本地多环境试用。** 用户已有完整的桌面操作路径，可以从独立管理窗口创建环境、看到启动反馈，然后在该环境的 Chromium 窗口内查看工作台。已有分组、批量操作、回收站、代理库，以及两个 Chromium 环境读写同一测试站点并分别重启的隔离回归。团队上云还需要回答三个日常问题：员工能打开哪些环境，断线后回到哪个窗口，服务器坏了能找回哪些数据。[桌面入口](../chromium/open_panel.py) · [工作台测试](../tests/test_window_workbench_page.py) · [双环境存储隔离测试](../tests/test_chromium_storage_isolation.py)

## 桌面产品先改什么

**评审后的进展**　开发分支已接入 Chromium 按窗口绑定固定代理。macOS arm64 的本地 HTTP 实测覆盖两个窗口各走各的代理、重启保留绑定，以及代理断开后该窗口请求失败而另一个窗口继续可用。HTTPS 和 SOCKS5 已验证配置校验与启动参数，认证代理和 WebRTC 仍需单独处理。下表保留评审基线当时的行为。[使用说明](../chromium/README.md#窗口代理) · [原生网络回归](../tests/test_chromium_proxy_routing.py)

Chromium 也已接入独立的 Canvas 2D 读取控制和 WebGL 显卡调试扩展控制。本地实测页能核对这两个设置。屏幕分辨率、处理器型号与音频参数仍没有可替换的硬件预设。[图形能力对照](../chromium/fingerprint-coverage.md)

代理库中已有的密码仍作为文本写入 SQLite。进入多人试用前应补上凭据存储保护，并用一份带测试凭据的备份验证访问和恢复范围。Chromium 本次仅接受未填写凭据的代理。[代理写入](../moltskin/services/core/main.py) · [数据库备份](../moltskin/services/core/backup.py)

| 用户操作 | 当前行为 | 下一步与验收方法 |
|---|---|---|
| 给某个窗口选代理 | 代理库支持 TXT、CSV 批量导入、校验和检测。Camoufox 可以绑定代理。Chromium 使用系统网络，API 和启动器均会拒绝绑定。 | 优先让使用者在列表和批量操作预览中看见每个窗口的网络模式。为 Chromium 接入按环境绑定后，用两个环境访问自有测试端点，各自核对出口。指定代理不可用时应提示失败，禁止静默改走系统网络。[代理导入测试](../tests/test_proxy_bulk_import.py) · [启动约束](../moltskin/services/core/launcher.py) |
| 改系统、处理器或其他环境参数 | Chromium 可按窗口设置 UA、网站首选语言、时区、启动窗口尺寸和部分网站权限。系统与处理器选项用于本地兼容性预览，普通页面读取宿主平台。屏幕分辨率、WebGL 和音频参数仍无环境级预设。 | 拉齐创建页、环境详情、工作台的表述，让用户能分别看到配置值、该窗口的实测值和仅预览项。选择不同平台做预览后，重启并访问自有测试页，三处都应能解释页面读到的值。未采样的项目显示「未验证」。[内核能力对照](../chromium/fingerprint-coverage.md) · [平台预览测试](../tests/test_chromium_platform_preview.py) |

## 团队上云的验收顺序

| 顺序 | 仓库里已有的基础与缺口 | 什么结果算完成 |
|---|---|---|
| 1 · 成员、权限和审计 | 本机 API 使用一个管理令牌。工作台按活动会话验证签名。操作日志已记录环境和动作，普通桌面操作的操作者默认是 `local`。现有分组可用于整理环境，云端还需要为每组绑定成员及操作权限。[API 与工作台](../moltskin/services/core/main.py) · [审计记录](../moltskin/services/core/db.py) | 管理员分别给甲乙分配环境。只有运行权限的成员可以打开自己的窗口，无法编辑、删除或访问对方环境。撤权后旧会话不能继续操作或重连。管理员按成员与环境能查到授权、启动、失败和撤权记录。 |
| 2 · 网页远程控制 | Linux 已有双窗口、键鼠输入和数据隔离验收脚本。它验证节点上的浏览器，网页到节点的远程会话还需端到端验证。[Linux 验收入口](../chromium/check_linux.py) · [开发路线](../README.zh-CN.md#chromium-开发路线) | 两位成员从各自电脑用网页打开各自的 Linux 窗口，同时在同一测试站点输入不同标记。甲断网重连后回到原窗口，乙全程可操作，键鼠和站点数据各归其主。 |
| 3 · 故障恢复 | 备份包含 Chromium 配置元数据。选择备份登录态时，仅收录 Camoufox 支持的站点数据，清单会列出排除的环境。还原会先替换数据库，再逐项复制文件，中断后的完整恢复仍需验证。[备份与恢复](../moltskin/services/core/backup.py) · [跨目录还原测试](../tests/test_backup_paths.py) | 导出前逐环境展示实际归档内容及需要重新登录的项目。在干净节点恢复两个测试环境，分别核对配置、Cookie 和 IndexedDB 的预期结果。中途打断一次还原，再试时能回到完整可用的状态。 |
| 4 · 可核验的版本与升级 | 已锁定 Chromium 源码提交，有平台验收脚本。常规 GitHub CI 运行静态检查与默认测试。默认运行会跳过需要真实浏览器的用例。[上游版本](../chromium/upstream.json) · [CI](../.github/workflows/ci.yml) | 设置页能识别应用提交、内核版本和平台。每个发布包附校验值与该产物的双环境实机报告。先用副本演练升级和失败恢复，再让试用团队更新。 |

建议先把前两项连成一次双人演示，再以第三项作为承载真实团队数据的条件。本轮本机执行默认测试得到 **125 通过、35 跳过**。这个结果覆盖默认回归，Linux 原生产物、网页远控与灾备另有各自的验收条件。

## 商业产品提供了哪些参照

- 比特浏览器可以为员工分配角色和环境分组。GoLogin 还区分只运行、可编辑和完整权限，可授权整个工作区或指定文件夹。这两项直接对应企业员工入职和撤权。[比特浏览器成员授权](https://doc.bitbrowser.net/help1/users/add-users-and-set-permissions) · [GoLogin 权限表](https://support.gologin.com/en/articles/3452652-team-members)
- GoLogin 提供从网页访问环境的入口，并明确云会话的并发限制。Moltskin 可用「两名成员同时操作并分别断线重连」作为首轮云端体验验收。[GoLogin 网页版说明](https://gologin.com/docs/general/gologin-web-and-chrome-os)
- AdsPower 可按时间和条件查看登录、打开环境、修改环境与代理的记录。Moltskin 可用一个实际支持问题验收审计功能，例如让管理员查出「昨天谁改了这个窗口的代理，之后谁打开过它」。[AdsPower 操作日志](https://help.adspower.com/docs/action_logs)

## 给独立评审者

请按「创建环境 → 选择内核与网络 → 打开窗口 → 分配给同事 → 断线重连 → 换机恢复」走一遍。从实际代码入口核对上述判断，标明哪些是实测、哪些仅有脚本、哪些是规划。请给出最值得先做的三项优化，每项附受影响用户、代码位置、最小可交付改动和验收用例，并指出有证据可以降级或删除的一项建议。
