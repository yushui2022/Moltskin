# Moltskin Chromium 指纹能力对照

核对版本为 Chromium `158.0.8127.88`，主要实测平台为 macOS arm64。Ubuntu x86-64 已另行验证双环境隔离，以及 Canvas 2D 和 WebGL 读取限制。Chromium 环境可按窗口选择运行平台、架构、屏幕尺寸、像素比和 WebGL 显卡型号，不再固定报告本机操作系统或 GPU。Canvas 2D 与 WebGL 像素噪声、AudioContext 指纹噪声可按环境开启，面板还可以分别限制 Canvas 2D 读取、WebGL 显卡调试信息、本机字体枚举、音视频采集和网站定位。Camoufox 环境支持自己的指纹预设，两个内核走不同的启动路径。[锁定版本](upstream.json) · [Chromium 启动器](../moltskin/services/core/chromium.py) · [Camoufox 启动器](../moltskin/services/core/runner.py)

## 面板预览与正常窗口

面板的「本地兼容性预览」在临时离线页面读取所选系统、平台版本和处理器架构。「打开」会启动该环境自己的 Chromium 进程。正常窗口现在也可以选择「运行平台与架构」，内核通过 `--moltskin-platform=windows:10.0.0:x86_64` 同步 `navigator.platform`、User-Agent、`navigator.userAgentData` 的高熵值，以及 HTTP `Sec-CH-UA-Platform`、`Sec-CH-UA-Platform-Version`、`Sec-CH-UA-Arch`、`Sec-CH-UA-Bitness` 请求头。实测 Windows、macOS 和 Linux 三种预设的 JS 读数与请求头一致。[平台设置回归](../tests/test_chromium_platform_settings.py) · [离线预览](../moltskin/services/core/platform_preview.py) · [正常窗口启动器](../moltskin/services/core/launcher.py)

下表按正常窗口填写。`chromium/launch_preview.py` 是额外的原生命令行启动工具，面板的两个按钮均有各自的服务端入口。[命令行启动工具](launch_preview.py)

## 网页可读的指纹面

“未实现”指在本分支中找不到把相应预设应用到 Chromium 环境的设置、启动逻辑或补丁。网页仍可能通过上游接口读到来自浏览器或操作系统的值。[内核补丁](patches/) · [面板配置校验](../moltskin/services/core/main.py)

| 项目 | 按环境修改指纹值 | 已验证的行为与剩余缺口 |
|---|---|---|
| Navigator 的设备、平台、浏览器版本、语言等属性 | 网站语言、UA、平台与已安装内核可选 | 页面与 Worker 使用窗口的网站首选语言，`Intl` 默认区域同步。窗口可以设置 UA，也可以固定到某个已安装的 Moltskin Chromium 完整版本。平台预设同步 `navigator.platform`、UA 中的系统标识、Client Hints 高熵值和 HTTP 请求头。[平台设置回归](../tests/test_chromium_platform_settings.py) |
| CPU 并发数和设备内存 | 可选择标准值或限制报告上限 | 限制时，逻辑处理器数最多报告 4，设备内存档位最多报告 4 GiB。Mac 上同一环境标准模式实测为 18 / 32 GiB，受限模式为 4 / 4 GiB。恢复默认后读数复原。导航和子资源请求收到的两种内存提示头均同步从 32 变为 4。Linux 已加入同一验收脚本，仍待实机复测。[测试范围](../tests/test_chromium_hardware_reporting.py) |
| 屏幕尺寸、分辨率、像素比 | 可选择屏幕预设 | 每个窗口可选 `WIDTHxHEIGHTxDEPTH@DPR` 预设；`screen.width/height/availWidth/availHeight/colorDepth`、`devicePixelRatio`、`device-width`/`device-height`/`resolution` 媒体查询同步。产品预设按平台设置 `availHeight`：Windows/Linux 模拟任务栏，macOS 模拟菜单栏与 Dock，窗口外框尺寸仍由「启动窗口大小」单独控制。[实机回归](../tests/test_chromium_screen.py) |
| 窗口内外尺寸 | 启动外框尺寸和屏幕预设可选 | 可选择启动时的实际窗口宽高，启动后仍能手动调整。网页内部可用的高度受工具栏影响。虚拟屏幕尺寸、色深和像素比由「屏幕分辨率与像素比」单独设置。[屏幕回归](../tests/test_chromium_screen.py) |
| Canvas 2D 像素读取与导出 | 可选择像素噪声或限制读取 | 开启噪声后，`getImageData`、`toDataURL` 和 `toBlob` 得到稳定的 ±1 颜色扰动，Alpha 不变；裁剪读取与整图读取的对应像素一致。单像素读取不施加噪声，避免被常见的读数噪声探针直接标记。限制读取会让 2D 像素读取和画布导出返回权限错误。[像素噪声回归](../tests/test_chromium_canvas_noise.py) · [读取限制回归](../tests/test_chromium_graphics_privacy.py) |
| WebGL 厂商/型号、参数、扩展、上下文属性、着色精度 | 可选择显卡预设、开启像素噪声，也可限制调试扩展 | 可选择 Apple、Intel、NVIDIA 或 AMD 显卡预设，覆盖 `WEBGL_debug_renderer_info` 返回的未遮蔽厂商和型号；跨系统 Windows 平台预设还会稳定 `MAX_TEXTURE_SIZE`、`MAX_VIEWPORT_DIMS` 等参数并过滤 ASTC/ETC/PVRTC 扩展。遮蔽值仍为 `WebKit` / `WebKit WebGL`。在 WebGL 1 和 2 中也可关闭该扩展，网页与专用 Worker 均受策略约束。普通 3D 绘图和像素读取仍可用。开启像素噪声后，`readPixels` 返回稳定的 ±1 颜色扰动，Alpha 不变。上下文属性与着色精度仍无环境级预设。[显卡回归](../tests/test_chromium_webgl_info.py) · [像素噪声回归](../tests/test_chromium_canvas_noise.py) · [扩展限制回归](../tests/test_chromium_graphics_privacy.py) |
| AudioContext 样本与指纹 | 可开启音频噪声 | 开启后，`OfflineAudioContext` 渲染结果和 `decodeAudioData` 得到的样本带稳定的 ±0.0001 扰动；`AnalyserNode` 的浮点频域、字节频域、浮点时域和字节时域读数也带稳定扰动。听感不可闻，同一环境图案稳定，不同环境不同。采样率、输出延迟和最大声道数仍沿用真实设备，未提供环境级预设。[音频噪声回归](../tests/test_chromium_audio_noise.py) |
| 语音合成声音列表与播放速率 | Windows 平台预设按语言生成 | 仅选择跨系统 Windows 平台预设时，网站首选语言为 `en-US`、`en-GB`、`ja-JP`、`de-DE`、`fr-FR` 或 `zh-CN` 会返回一组稳定的 Windows 风格本地语音，并设置默认语音；macOS/Linux 或未选平台时保留宿主语音。语音播放速率仍由页面控制，实际合成质量取决于宿主 TTS 后端。[语音回归](../tests/test_chromium_speech.py) |
| 麦克风、摄像头、扬声器数量 | 未实现数量配置 | 已能阻止音视频采集。模拟设备测试验证了受限环境读不到输入设备的名称、设备 ID 和分组 ID。扬声器数量和输出设备列表尚未验收。 |
| HTTP User-Agent、Accept-Language 与 Navigator 联动 | UA、语言和平台可按窗口设置 | Chromium 将窗口设置的 UA 用于 HTTP `User-Agent` 和 `navigator.userAgent`。留空时，平台预设会生成对应系统的默认 UA。首选语言应用到 HTTP `Accept-Language` 和 Navigator。Client Hints 的平台、版本、架构和位数随平台预设同步。[平台设置回归](../tests/test_chromium_platform_settings.py) |
| WebRTC 协议层地址 | 固定代理时禁用非代理 UDP | 普通网页请求可以按环境指定固定代理；有代理时启动参数加入 `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`，避免 WebRTC 绕过代理。完整 STUN/UDP 地址替换和媒体链路仍需独立验收。[窗口代理](README.md#窗口代理) |
| 定位坐标、时区和区域语言 | 时区、网站首选语言可选 | 时区应用于该窗口页面与 Worker 的日期计算和 Intl 时区。网站首选语言同步到 `navigator.language`、HTTP `Accept-Language` 和渲染进程 ICU 默认区域，`Intl.DateTimeFormat`、`Intl.NumberFormat` 等使用同一区域。定位权限可以单独阻止，面板不配置定位坐标。 |
| Battery API | 未实现 | 没有电量、充电状态等环境级配置，也没有针对该接口的专项回归。 |
| 字体列表与字体度量 | Windows 平台预设可过滤字体族 | 选择与宿主不同的 Windows 平台预设时，CSS `font-family` 只保留 Arial、Georgia、Tahoma、Times New Roman、Verdana 等跨平台字体，macOS 专有的 PingFang、Helvetica、Futura、Apple SD Gothic Neo 等会回退到通用字体。字体度量和 ClientRects 仍由实际字体文件决定，系统字形回退仍使用宿主字体；`document.fonts.check()` 和 `FontFace(local(...))` 已同步过滤。[字体回归](../tests/test_chromium_font_filter.py) |
| 其他常见表面 | 部分权限可用 | 可按窗口限制 Canvas 2D 像素读取和图像导出，或开启稳定像素噪声。已验证本机字体枚举限制、拉丁字体回退及网页图标字体。ClientRects 无环境级预设。[像素噪声回归](../tests/test_chromium_canvas_noise.py) · [图形隐私说明](README.md#图形隐私) |

实机回归分别覆盖了[字体与排版](../tests/test_chromium_font_rendering.py)、[图形与离线音频](../tests/test_chromium_graphics_audio.py)、[媒体采集](../tests/test_chromium_media_capture.py)和[定位](../tests/test_chromium_geolocation.py)。

## 对照 Camoufox 的 Stealth patches

| 清单项 | 自编 Chromium 的情况 |
|---|---|
| Page Agent 主世界隔离、frame 执行上下文泄漏 | 常规面板直接启动 Chromium 进程。仓库没有这两项专门补丁或验收。跨站 iframe 的渲染进程隔离另见下文。 |
| `navigator.webdriver` | 已加入 Moltskin 内核补丁和启动参数 `--moltskin-hide-webdriver`。即使通过 Playwright/CDP 启动，页面读取仍为 `false`。[实机回归](../tests/test_chromium_webdriver.py) |
| Firefox 无头模式的指针类型修复 | 原条目针对 Camoufox 的 Firefox 问题。Moltskin Chromium 尚无有头与无头指针行为的专项对照。[原始 Issue](https://github.com/daijro/camoufox/issues/26) |
| 撤销 anti-zoom / meta viewport 补丁 | 原条目涉及 Camoufox 历史补丁。当前 Chromium 补丁集没有对应变更，缩放与视口兼容性未单独验收。 |
| 非默认屏幕与窗口尺寸 | 两个环境可分别选择启动时的窗口外框尺寸，见[双窗口实机回归](../tests/test_chromium_region.py)；Chromium 窗口还可单独设置虚拟屏幕尺寸、色深和像素比，见[屏幕回归](../tests/test_chromium_screen.py)。 |
| 重新启用 Firefox Fission | Chromium 使用 Site Isolation。Mac 测试中，同源 iframe 与父页面共用渲染目标，跨站 iframe 出现独立的渲染目标。该结果覆盖本次两个本地站点的测试场景。[Chromium Site Isolation](https://www.chromium.org/Home/chromium-security/site-isolation/) · [渲染目标说明](https://developer.chrome.com/docs/extensions/reference/api/debugger#frames) |
| 重新启用 PDF.js | Chromium 内置查看器使用 PDFium。Mac 实测可打开本地双页 PDF，查看器正确显示总页数 2。[Chromium PDF 组件](https://raw.githubusercontent.com/chromium/chromium/main/pdf/README.md) |
| 其他可能暴露信息的配置属性 | 原条目没有列出属性名及预期行为，无法逐项判定。 |
| 拟人鼠标移动 | Chromium 启动路径没有这项功能。Camoufox 启动器会向自己的内核传入 `humanize` 设置。 |

PDF 与跨站 iframe 验收使用正常启用沙箱的本机无头 Chromium。Linux 产物、有头模式以及不同 GPU 或音频硬件仍需分别验收。[PDF 与站点隔离回归](../tests/test_chromium_platform.py) · [平台设置回归](../tests/test_chromium_platform_settings.py)
