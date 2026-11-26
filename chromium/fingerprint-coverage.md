# Moltskin Chromium 指纹能力对照

核对版本为 Chromium `158.0.8127.88`，主要实测平台为 macOS arm64。Ubuntu x86-64 已另行验证双环境隔离，以及 Canvas 2D 和 WebGL 读取限制。当前 Chromium 环境使用本机操作系统。面板可以分别限制 Canvas 2D 读取、WebGL 显卡调试信息、本机字体枚举、音视频采集和网站定位。Camoufox 环境支持自己的指纹预设，两个内核走不同的启动路径。[锁定版本](upstream.json) · [Chromium 启动器](../moltskin/services/core/chromium.py) · [Camoufox 启动器](../moltskin/services/core/runner.py)

## 网页可读的指纹面

“未实现”指在本分支中找不到把相应预设应用到 Chromium 环境的设置、启动逻辑或补丁。网页仍可能通过上游接口读到来自浏览器或操作系统的值。[内核补丁](patches/) · [面板配置校验](../moltskin/services/core/main.py)

| 项目 | 按环境修改指纹值 | 已验证的行为与剩余缺口 |
|---|---|---|
| Navigator 的设备、平台、浏览器版本、语言等属性 | 网站语言、UA 和已安装内核可选 | 页面与 Worker 使用窗口的网站首选语言。窗口可以设置 UA，也可以固定到某个已安装的 Moltskin Chromium 完整版本。平台、硬件并发数和设备内存档位由实际运行的浏览器报告，可在该环境打开本机 `/runtime.html` 核对。 |
| 屏幕尺寸、分辨率、像素比 | 未实现 | 没有 Chromium 环境级屏幕预设。 |
| 窗口内外尺寸 | 启动外框尺寸可选 | 可选择启动时的实际窗口宽高，启动后仍能手动调整。网页内部可用的高度受工具栏影响。显示器尺寸和像素比沿用本机。 |
| WebGL 参数、扩展、上下文属性、着色精度 | 可限制显卡调试扩展 | 在 WebGL 1 和 2 中可关闭读取未遮蔽厂商与型号的扩展，网页与专用 Worker 均受该策略约束。普通 3D 绘图和像素读取仍可用。上下文属性、显卡型号替换和着色精度均无环境级预设。[实机回归](../tests/test_chromium_graphics_privacy.py) |
| AudioContext 采样率、输出延迟、最大声道数 | 未实现 | 两种环境下，离线音频图输出的 256 个采样值均符合预期。这里的采样率由测试网页指定；实际声卡、扬声器和输出延迟仍待验证。 |
| 语音合成声音列表与播放速率 | 未实现 | 没有按环境提供声音或速率预设，也没有语音播放专项回归。 |
| 麦克风、摄像头、扬声器数量 | 未实现数量配置 | 已能阻止音视频采集。模拟设备测试验证了受限环境读不到输入设备的名称、设备 ID 和分组 ID。扬声器数量和输出设备列表尚未验收。 |
| HTTP User-Agent、Accept-Language 与 Navigator 联动 | UA 和网站首选语言可按窗口设置 | Chromium 将窗口设置的 UA 用于 HTTP `User-Agent` 和 `navigator.userAgent`。留空使用内核默认 UA。首选语言应用到 HTTP `Accept-Language` 和 Navigator。Client Hints 遵循当前 Chromium 的内置行为。 |
| WebRTC 协议层地址 | 未实现 | 普通网页请求可以按环境指定固定代理。WebRTC 的媒体传输和 UDP 地址仍需独立验收。[窗口代理](README.md#窗口代理) |
| 定位坐标、时区和区域语言 | 时区、网站首选语言可选 | 时区应用于该窗口页面与 Worker 的日期计算和 Intl 时区。浏览器菜单语言及 Intl 默认格式语言由系统和浏览器决定。定位权限可以单独阻止，面板不配置定位坐标。 |
| Battery API | 未实现 | 没有电量、充电状态等环境级配置，也没有针对该接口的专项回归。 |
| 其他常见表面 | 部分权限可用 | 可按窗口限制 Canvas 2D 像素读取和图像导出。已验证本机字体枚举限制、拉丁字体回退及网页图标字体。ClientRects 无环境级预设。[图形隐私说明](README.md#图形隐私) |

实机回归分别覆盖了[字体与排版](../tests/test_chromium_font_rendering.py)、[图形与离线音频](../tests/test_chromium_graphics_audio.py)、[媒体采集](../tests/test_chromium_media_capture.py)和[定位](../tests/test_chromium_geolocation.py)。

## 对照 Camoufox 的 Stealth patches

| 清单项 | 自编 Chromium 的情况 |
|---|---|
| Page Agent 主世界隔离、frame 执行上下文泄漏 | 常规面板直接启动 Chromium 进程。仓库没有这两项专门补丁或验收。跨站 iframe 的渲染进程隔离另见下文。 |
| `navigator.webdriver` | 没有 Moltskin 专门补丁或验收。 |
| Firefox 无头模式的指针类型修复 | 原条目针对 Camoufox 的 Firefox 问题。Moltskin Chromium 尚无有头与无头指针行为的专项对照。[原始 Issue](https://github.com/daijro/camoufox/issues/26) |
| 撤销 anti-zoom / meta viewport 补丁 | 原条目涉及 Camoufox 历史补丁。当前 Chromium 补丁集没有对应变更，缩放与视口兼容性未单独验收。 |
| 非默认屏幕与窗口尺寸 | 两个环境可分别选择启动时的窗口外框尺寸，见[双窗口实机回归](../tests/test_chromium_region.py)。实际屏幕分辨率沿用本机。 |
| 重新启用 Firefox Fission | Chromium 使用 Site Isolation。Mac 测试中，同源 iframe 与父页面共用渲染目标，跨站 iframe 出现独立的渲染目标。该结果覆盖本次两个本地站点的测试场景。[Chromium Site Isolation](https://www.chromium.org/Home/chromium-security/site-isolation/) · [渲染目标说明](https://developer.chrome.com/docs/extensions/reference/api/debugger#frames) |
| 重新启用 PDF.js | Chromium 内置查看器使用 PDFium。Mac 实测可打开本地双页 PDF，查看器正确显示总页数 2。[Chromium PDF 组件](https://raw.githubusercontent.com/chromium/chromium/main/pdf/README.md) |
| 其他可能暴露信息的配置属性 | 原条目没有列出属性名及预期行为，无法逐项判定。 |
| 拟人鼠标移动 | Chromium 启动路径没有这项功能。Camoufox 启动器会向自己的内核传入 `humanize` 设置。 |

PDF 与跨站 iframe 验收使用正常启用沙箱的本机无头 Chromium。Linux 产物、有头模式以及不同 GPU 或音频硬件仍需分别验收。[平台回归测试](../tests/test_chromium_platform.py)
