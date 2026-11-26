# Mac 正常窗口的平台实测

2026-11-26 在 macOS arm64 上使用自编 Chromium `158.0.8127.88`。第 1、3 项要求网站读到 Windows 和 x86-64，本次均未通过。

| 读取位置 | platform | architecture | bitness |
|---|---|---|---|
| 本地兼容性预览 | Windows | x86 | 64 |
| 正常窗口中的 BrowserLeaks | macOS | arm | 64 |

## 测试条件

在独立的临时数据目录新建一个 Chromium 环境，保存系统 `Windows`、平台版本 `15.0.0` 和架构 `x86_64`，UA 留空。先用该环境保存的选择调用面板的预览接口，然后调用面板「打开」所用的 API 启动同一环境的有头浏览器。测试直接调用 API，跳过了面板点击和启动工作台。运行结束后关闭此测试环境。

将检测网址打开在该 Chromium 进程中，通过 macOS 辅助功能接口核对进程和网页 URL，读取网站已经渲染的 Client Hints JavaScript API 表格。正常窗口沿用产品启动参数，采集时没有使用远程调试或向网站注入脚本。本记录只覆盖表格里的三个字段。[BrowserLeaks Client Hints](https://browserleaks.com/client-hints)

## 验收状态

| 编号 | 状态 | 依据 |
|---|---|---|
| 1 | 未通过 | 真实网站报告 macOS 和 arm，位数为 64。 |
| 3 | 未通过 | UA Client Hints 的平台与架构已与预期不符。本次没有另读旧接口 `navigator.platform`。 |
| 2、4–12 | 未测 | 按任务设定的顺序，在第一阶段的系统和架构核验后停止。 |

面板的平台预览由 `platform_preview.measure` 创建临时离线页面。正常窗口由 `launcher.launch` 启动，传给 `chromium.prepare_launch` 的参数中没有预览系统和架构。独立命令行工具 `chromium/launch_preview.py` 可以启动原生 Chromium，面板的平台预览由服务端单独实现。[离线预览](../moltskin/services/core/platform_preview.py) · [正常窗口](../moltskin/services/core/launcher.py) · [命令行入口](../chromium/launch_preview.py)

## 本轮采集日志

```json
{
  "observed_at": "2026-11-26T13:29:41+08:00",
  "source_revision": "e038811a87a3d179e7b6cdc37f73230f6dbbb0ea",
  "chromium": "158.0.8127.88",
  "profile_id": "pf_1e1e6cea7cdf",
  "profile_selection": {
    "preview_platform": "Windows",
    "preview_architecture": "x86_64",
    "preview_platform_version": "15.0.0"
  },
  "preview": {
    "platform": "Windows",
    "platform_version": "15.0.0",
    "architecture": "x86",
    "bitness": "64"
  },
  "normal_launch": {
    "method": "POST /api/v1/profiles/{id}/launch",
    "headed": true,
    "custom_user_agent": false,
    "url": "https://browserleaks.com/client-hints",
    "observation": "macOS Accessibility, BrowserLeaks rendered Client Hints JavaScript API table",
    "web_area_matched": true,
    "accessible_rows": 45,
    "site_values": {
      "platform": "macOS",
      "bitness": "64",
      "architecture": "arm"
    }
  }
}
```

## 后续结果：0011-platform

上文保留 2026-11-26 修复前的正常窗口基线。随后加入 `0011-platform.patch`，按窗口传入 `--moltskin-platform=platform:version:architecture`。用同一自编 Chromium 分别启动三个预设，通过本地 HTTP 页面读取 JavaScript 值和请求头，结果如下。

| 预设 | `navigator.platform` | UA 中的系统标识 | `Sec-CH-UA-Platform` | `architecture` / `bitness` | `platformVersion` |
|---|---|---|---|---|---|
| `windows:10.0.0:x86_64` | `Win32` | `Windows NT 10.0.0; Win64; x64` | `Windows` | `x86` / `64` | `10.0.0` |
| `macos:14.6.0:arm64` | `MacIntel` | `Macintosh; Intel Mac OS X 10_15_7` | `macOS` | `arm` / `64` | `14.6.0` |
| `linux:6.8.0:x86_64` | `Linux x86_64` | `X11; Linux x86_64` | `Linux` | `x86` / `64` | `6.8.0` |

高熵 Client Hints 需要网站先通过 `Accept-CH` 请求；首轮请求只发送低熵的 `Sec-CH-UA-Platform`，后续请求包含上表四项。回归脚本在 `tests/test_chromium_platform_settings.py`。本轮验证使用本地测试页，未重复 BrowserLeaks 人工核对；历史日志中的网站读数仍是修复前的值。
