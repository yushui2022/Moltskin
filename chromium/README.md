# Moltskin Chromium 开发预览

本地控制面板和命令行都可以启动 Chromium 开发包。每个环境都有独立的数据目录，可单独设置 Canvas 2D 读取、WebGL 显卡调试信息、Canvas/WebGL 像素噪声、AudioContext 指纹噪声、本机字体、摄像头与麦克风采集及定位权限。各项指纹面的支持状态、实测范围与平台差异见[指纹能力对照](fingerprint-coverage.md)。

## 打开桌面管理窗口

应用下方补丁并构建 Chromium，按项目快速开始安装 Python 依赖。在仓库根目录用同一 Python 环境运行 `python chromium/open_panel.py`。启动器会启动或复用本机控制服务，并在独立的桌面窗口中显示「浏览器窗口」列表。macOS 也可以运行一次 `python chromium/install_panel_macos.py`，以后双击用户「应用程序」目录中的 `Moltskin.app` 即可打开。

在管理页点击「创建窗口」，填写名称，在「浏览器与系统」选择 Moltskin Chromium，然后在列表中点「打开」。管理窗口会显示启动状态，遇到错误时显示原因。有头环境会另开带地址栏和标签栏的浏览器窗口。默认设置下，首个标签页是该环境自己的工作台，显示名称、分组、备注、本次启动时间、实际内核和网页读取的 UA。

窗口直接向 IPinfo 查询自己的出口 IP 和所在地区。如果读取失败，窗口会通过 ipify 获取出口 IP，并尝试用本机已安装的地理库查询该地址。页面分别显示 IP 所在地时间和浏览器时区。地理信息来自出口 IP 的估算，服务缺少的数据会在页面上标明。[IPinfo 接口说明](https://ipinfo.io/developers/ipinfo-api) · [ipify 接口说明](https://www.ipify.org/)

「启动与浏览」可以指定空白页或管理首页。全局关闭自动打开工作台时，使用默认设置的环境启动到空白页。点击地址栏右侧、头像左侧的 Moltskin 变色龙按钮即可打开控制面板首页。已有面板标签时，按钮会切回该标签并回到首页，工作标签会保留。在地址栏输入 `chrome://moltskin/` 也可以回到面板。要修改权限，先关闭对应环境，编辑后再次打开。

「浏览器与系统」显示运行浏览器的主机系统与架构。内核版本默认跟随本机默认构建，也可为每个窗口固定一个已安装版本。自定义默认构建时，在启动面板前设置 `MOLTSKIN_CHROMIUM_BINARY`，指向 Mac 的 `Chromium.app` 或 Linux 的 `chrome`。要提供更多版本，可设置 `MOLTSKIN_CHROMIUM_BUILDS_DIR`，每个子目录分别放置一个 `Chromium.app` 或 `chrome`。列表使用程序实际报告的版本。使用旧主版本测试时应新建环境，已由更新主版本使用的数据目录有降级保护。

「网站首选语言」会同时设置 `navigator.language`、HTTP `Accept-Language`、渲染进程的 ICU 默认区域和语音合成列表。`Intl.DateTimeFormat`、`Intl.NumberFormat`、日期与数字格式化都使用该区域，不再跟随宿主系统。选择与宿主不同的 Windows 平台预设时，语音列表会替换为对应语言的 Windows 风格本地声音；例如 `en-US` 返回 Microsoft David、Zira 和 Mark；macOS、Linux 或未选择平台时保留宿主语音，避免与 Emoji、字体和系统音色冲突。实际播放仍由宿主 TTS 后端合成，未知 voiceURI 可能回退到默认声音；该功能用于统一网页可见的环境画像。

在「浏览器与系统」可以为单个 Chromium 窗口填写 User-Agent。留空使用所选内核的默认 UA。要修改已创建的窗口，先关闭窗口，保存后重新打开。要核对结果，在该环境打开本机 `/runtime.html`，页面会分别显示本机服务收到的 HTTP 请求头和网页通过 `navigator.userAgent` 读到的值，以及此窗口实际公开的平台版本、处理器架构和位数。

「本地兼容性预览」支持选择 Windows、macOS 或 Linux，填写可选的平台版本，并选择 ARM64 或 x86-64。每个窗口可以保存自己的预览设置。点击运行后，面板使用选定内核，在一次性离线测试页实测 Chromium 的 User-Agent Client Hints。读数中的 `x86` 加 `64` 表示 x86-64。预览用于快速核对所选内核与预设，不改变工作窗口；要让正常网站读到平台预设，使用下面「平台与 Client Hints」中的窗口设置。

每个窗口可以单独静音、设置渲染进程数量的软上限，或限制 V8 老生代堆大小。老生代堆用于存放存活时间较长的 JavaScript 对象，设备内存和操作系统可用内存是独立的指标。「查看当前浏览器实测值」也会显示窗口的语言、时区、硬件并发数、设备内存档位及尺寸。

Chromium 环境可在浏览器中登录并保留本机数据。面板里的 Cookie 文件管理、跨环境状态同步和备份中的登录态目前仅支持 Camoufox。Chromium 环境的名称和权限设置随面板数据库一起备份。

### 硬件信息隐私

Chromium 窗口可以沿用浏览器的标准报告，或将网站能读取的逻辑处理器数限制到最多 4 个、设备内存档位限制到最多 4 GiB。两项都取 Chromium 默认报告值与上限中的较小者。内存档位本身是估算值。这项设置只影响对网站公开的数值，操作系统照常分配 CPU 和内存。

限制覆盖页面、iframe 和 Worker 的 `navigator.hardwareConcurrency` 与 `navigator.deviceMemory`，以及网站请求的 `Sec-CH-Device-Memory` 和 `Device-Memory`。可在当前窗口的实测页查看两个 JavaScript 读数。已有窗口默认使用标准模式，选择限制需要内核声明 `moltskin-limit-hardware-reporting`。[实机回归](../tests/test_chromium_hardware_reporting.py)

### 图形隐私

新建或编辑 Chromium 窗口时，在「指纹设置」中可以分别设置以下两项。已有环境和新窗口默认使用「标准」。

- **Canvas 像素读取**。选择「限制读取」会让普通网页的 2D 像素读取与画布图像导出返回权限错误。绘图仍可使用，依赖像素读取的网页编辑器或图片导出可能受影响。
- **WebGL 显卡信息**。限制后，WebGL 1 和 2 不再提供读取未遮蔽厂商和型号的调试扩展。普通 3D 绘制和像素读取仍可用。[扩展规范](https://registry.khronos.org/webgl/extensions/WEBGL_debug_renderer_info/)

关闭窗口后可更改设置，重新打开该环境，在本机 `/runtime.html` 查看「图形隐私实测」。页面会尝试读回一个绿色像素并查询 WebGL 调试扩展。两项限制均需当前内核声明对应能力。

### Windows 字体过滤

选择与宿主不同的 Windows 平台预设时，启动器会传入 `--moltskin-font-filter=windows`；同系统 Windows 画像保留宿主 Windows 字体，不启用跨系统白名单。内核在解析 CSS `font-family` 时只保留跨平台桌面字体族（Arial、Georgia、Tahoma、Times New Roman、Trebuchet MS、Verdana 等），macOS 专有的 PingFang、Helvetica、Futura、Apple SD Gothic Neo、Geneva 等会从字体列表中移除并回退到通用字体。

这能消除 CreepJS 等站点的字体族列表泄漏，但不改变 Arial 等共用字体文件的实际度量；系统字形回退、Web Font 和 ClientRects 仍按宿主渲染。`document.fonts.check()` 和 `FontFace(local(...)).load()` 也会按白名单返回不可用，CreepJS 的字体列表不再列出 macOS 专有字体。[实机回归](../tests/test_chromium_font_filter.py)

### 屏幕与像素比

Chromium 窗口可在「屏幕分辨率与像素比」选择预设，例如 `1920 × 1080 · 1.25x`。启动器按平台传入可用工作区：Windows/Linux 使用任务栏占用（例如 `1920x1040`），macOS 使用菜单栏和 Dock 占用（例如 `1920x1000`），未选择平台时按宿主系统选择。启动器传给内核 `--moltskin-screen-metrics=1920x1080x24@1.25|1920x1040`，内核同步覆盖网页读取的 `screen.width/height/availWidth/availHeight/colorDepth`、`devicePixelRatio`，以及 `device-width`、`device-height`、`resolution` 媒体查询。启动窗口外框尺寸仍由「启动窗口大小」独立控制，因此窗口可以小于虚拟屏幕。[实机回归](../tests/test_chromium_screen.py)

### 平台与 Client Hints

Chromium 窗口可在「运行平台与架构」选择 Windows、macOS 或 Linux，以及 x86-64 或 ARM64。启动器传入 `--moltskin-platform=windows:10.0.0:x86_64`，内核同步覆盖：

- `navigator.platform`
- 默认 `navigator.userAgent` 中的系统标识
- `navigator.userAgentData.platform`
- `getHighEntropyValues()` 返回的 `architecture`、`bitness` 和 `platformVersion`
- HTTP `Sec-CH-UA-Platform`、`Sec-CH-UA-Platform-Version`、`Sec-CH-UA-Arch` 和 `Sec-CH-UA-Bitness`

手动填写 User-Agent 时，该 UA 优先于平台预设生成的默认值；Client Hints 仍按平台预设返回。平台预设不会改变渲染、字体或 JavaScript 引擎，也不模拟对应系统的 GPU 和音频设备。[实机回归](../tests/test_chromium_platform_settings.py)

### WebGL 显卡型号

Chromium 窗口可在「WebGL 显卡型号」选择 Apple、Intel、NVIDIA 或 AMD 显卡预设。启动器把预设拆成 `--moltskin-webgl-vendor` 和 `--moltskin-webgl-renderer` 两个参数，内核只覆盖 `WEBGL_debug_renderer_info` 扩展返回的 `UNMASKED_VENDOR_WEBGL` 和 `UNMASKED_RENDERER_WEBGL`。遮蔽的 `VENDOR` 与 `RENDERER` 仍返回 `WebKit` 和 `WebKit WebGL`，与标准 Chromium 一致。

跨系统 Windows 画像还会启用稳定的 WebGL 参数画像，例如 `MAX_TEXTURE_SIZE`、`MAX_VIEWPORT_DIMS`、纹理单元和 uniform 向量上限，并过滤 ASTC/ETC/PVRTC 等 macOS 常见压缩纹理扩展。显卡预设本身只改变网页读到的字符串，不改变实际 GPU、驱动、渲染结果或性能。选择「限制 WebGL 显卡调试信息」时，该扩展不可用，显卡预设也不会暴露。[实机回归](../tests/test_chromium_webgl_info.py)

### Canvas 与 WebGL 像素噪声

在「指纹设置」中开启「Canvas 与 WebGL 像素噪声」后，启动器从环境 ID 派生一个稳定种子，并传入 `--moltskin-canvas-noise=<seed>`。内核覆盖以下读取路径：

- Canvas 2D `getImageData()`、`toDataURL()` 和 `toBlob()`
- WebGL `readPixels()` 和 WebGL 画布导出

颜色通道加入确定性的 `-1`、`0` 或 `+1` 扰动，Alpha 通道保持不变。同一环境每次启动得到相同图案；复制或重新导入的环境会获得新的环境 ID，因此噪声图案不同。裁剪读取使用画布绝对坐标，和整图读取的对应像素一致。单像素读取不施加噪声，以避开常见的读数噪声探针；正常尺寸的 Canvas 指纹仍会得到稳定扰动。

噪声只在读取时应用，不改写画布内部像素，所以页面上的绘制结果和后续 `drawImage()` 不会累积误差。开启后 `toBlob()` 会跳过渐进式编码，以统一应用噪声。选择「限制 Canvas 像素读取」时，读取本身会被拒绝，噪声设置不会绕过限制。[实机回归](../tests/test_chromium_canvas_noise.py)

### AudioContext 指纹噪声

在「指纹设置」中开启「AudioContext 指纹噪声」后，启动器从环境 ID 派生独立的种子，并传入 `--moltskin-audio-noise=<seed>`。内核在以下位置加入确定性的微小样本扰动：

- `OfflineAudioContext.startRendering()` 返回的渲染结果
- `decodeAudioData()` 返回的 `AudioBuffer`
- `AnalyserNode.getFloatFrequencyData()` / `getByteFrequencyData()`
- `AnalyserNode.getFloatTimeDomainData()` / `getByteTimeDomainData()`

渲染和解码样本的扰动幅度为 `±0.0001`，约 -80 dBFS；`AnalyserNode` 浮点频域扰动为 `±0.01 dB`，字节读数为 `±1`。这些变化听感不可闻，但会改变浮点样本和量化后的指纹哈希。同一环境每次启动得到相同图案；不同环境以及同一环境的 Canvas 噪声使用不同种子。采样率、输出延迟和声道数仍来自真实音频设备。[实机回归](../tests/test_chromium_audio_noise.py)

### 自动化标记与 WebRTC

Moltskin Chromium 启动时固定传入 `--moltskin-hide-webdriver`，因此即使窗口被 Playwright、CDP 或其他自动化客户端启动，网页读取的 `navigator.webdriver` 仍为 `false`。这是内核补丁 `0009-hide-webdriver.patch` 的行为，实机回归见 `../tests/test_chromium_webdriver.py`。

窗口绑定固定代理时，启动参数还会加入 `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`，避免 WebRTC 通过非代理 UDP 暴露本机出口。未绑定代理的窗口保留 Chromium 默认策略；STUN、媒体传输和 UDP 地址替换仍需按站点单独验收。

### 窗口代理

在「代理 IP」保存代理，然后在新建或编辑 Chromium 窗口时选择该代理。留空会沿用系统网络。支持未设置用户名和密码的 HTTP、HTTPS 和 SOCKS5 代理。窗口运行期间需先关闭窗口再变更绑定或代理地址。复制环境、导出和重新导入配置都会保留代理设置。

指定代理通过 Chromium 的原生网络栈路由普通网页请求。代理失联时，相应的网页请求会报错。本机回环地址按 Chromium 的内置规则访问，所以本地工作台仍可用。WebRTC 的媒体传输和 UDP 需要独立验证。[Chromium 代理行为](https://chromium.googlesource.com/chromium/src/+/HEAD/net/docs/proxy.md)

## 临时打开看效果

在仓库根目录执行 `python3 chromium/launch_preview.py`。每次都会打开全新的临时环境，正常退出浏览器后清理数据。macOS 临时预览使用 Chromium 的测试钥匙串处理 Cookie 加密，不读取登录钥匙串里的「Chromium Safe Storage」。需要保留登录状态时，使用下面的命名环境。[Chromium 开发构建说明](https://chromium.googlesource.com/chromium/src/+/main/docs/mac_build_instructions.md#avoiding-system-permissions-dialogs-after-each-build)

## 打开两个环境

在 Moltskin 仓库根目录打开两个终端，分别执行一条命令。

```sh
python3 chromium/launch_preview.py a --local-fonts standard
python3 chromium/launch_preview.py b --local-fonts restricted --media-capture blocked --geolocation blocked
```

网页调用 `window.queryLocalFonts()` 时，标准模式沿用 Chromium 的站点授权流程。限制模式在浏览器进程中直接返回空列表，即使该站点已有授权。这个设置只作用于 Local Font Access API，网页仍可正常使用 CSS 字体。

在 B 环境中，网站申请摄像头、麦克风或两者时都会被阻止，即使这个站点以前已有授权。设置为 `--media-capture standard` 可恢复 Chromium 的站点授权行为。位置设置独立生效，`--geolocation blocked` 会拒绝网页的单次和持续定位请求。媒体权限适用于网站访问摄像头和麦克风，不涉及屏幕共享。

关闭 B 的浏览器后，执行 `python3 chromium/launch_preview.py b` 即可恢复上次设置。图形策略也可单独修改，例如 `python3 chromium/launch_preview.py b --canvas-readback blocked --webgl-debug-info standard`。每个环境的设置和浏览数据保存在 `data/chromium-preview/<环境名>/`，可用 `MOLTSKIN_DATA_DIR` 指定数据根目录。

macOS 命名环境使用登录钥匙串保护 Cookie。如果系统询问当前开发版是否可访问「Chromium Safe Storage」，确认是自己启动的版本后，在系统弹窗输入登录钥匙串密码，选择「始终允许」可记住此次授权。重新编译的开发版可能需要重新授权。[Apple 钥匙串说明](https://support.apple.com/guide/mac-help/kychn002/mac)

启动器优先查找 `out/MoltskinHardwarePrivacy` 开发包，也能打开早期开发包。选择了图形、采集或定位限制的环境，需要使用包含对应补丁的构建。命令行预览可用 `--binary` 指定其他开发包。启动前会向内核确认它支持所需功能。

## 检查上游版本

在 Moltskin 仓库根目录执行。此命令只查询官方 Git 标签，成功后打印可用于检出的完整提交号。

```sh
python3 chromium/verify_upstream.py
```

## 首次检出与构建

Linux 首批目标为 Ubuntu x86-64（至少 100 GB 空闲磁盘）；macOS 首批目标为 Apple Silicon，需要 Xcode、macOS SDK 和空间充足的 APFS 卷。按对应平台的指南安装 Git、Python 3 和 `depot_tools`，确保终端能找到 `gclient`、`gn` 和 `autoninja`。[Linux 构建指南](https://chromium.googlesource.com/chromium/src/+/main/docs/linux/build_instructions.md) · [macOS 构建指南](https://chromium.googlesource.com/chromium/src/+/main/docs/mac_build_instructions.md)

### Linux x86-64 产品内核

在 Moltskin 仓库根目录执行。检出和编译用普通用户，首次安装系统依赖时才需要 `sudo`。构建脚本使用 `upstream.json` 的提交，同步依赖，按顺序应用仓库内的补丁，生成 release 配置并编译。编译结束会运行产物，检查完整版本号和本分支要求的全部 Moltskin 能力。

```sh
checkout="$HOME/chromium-baseline"
python3 chromium/build_linux.py "$checkout" --install-deps --jobs 4
```

输出为 `$checkout/src/out/MoltskinLocation/chrome`。网络传输中断时可重跑原命令。若依赖同步和 hooks 已完成，可用 `python3 chromium/build_linux.py "$checkout" --prepared --jobs 4` 接续编译。遇到本地修改冲突时会报错，已经完整应用的补丁可直接复用。

### 从另一台电脑打开 Linux 窗口

在 Ubuntu 上将上一步的产物安装到固定路径，并用普通 SSH 用户启动远程会话。客户端可以通过 noVNC 查看和操作窗口。部署、重连和停止命令见[Linux 远程窗口](remote/README.md)。

### Linux 有头双窗口验收

在同一台 Ubuntu 上安装 `xvfb`、`xauth`、`xdotool` 和 `python3-venv`，在项目 Python 虚拟环境中安装 `requirements-dev.txt`。按上节安装带 AppArmor 规则的内核后，用普通用户运行。

```sh
python chromium/check_linux.py /opt/moltskin-preview/chrome
```

验收会先检查 x86-64 ELF、版本和补丁能力，再通过面板 API 在私有 X11 显示中同时打开两个环境。测试向两个窗口分别发送鼠标和键盘输入，在同一个本地站点写入不同的 Cookie、localStorage、IndexedDB 和缓存，并在重启及清除一个环境后核对各自的数据。整个过程使用临时环境目录。要让服务器上的管理服务使用通过验收的内核，设置 `MOLTSKIN_CHROMIUM_BINARY` 指向这个 `chrome`，保留同目录的资源和运行库。

### 手动构建上游对照版本

从 Moltskin 根目录开始，在同一终端执行以下步骤。首次使用全新的源码目录，路径中不要包含空格；如果构建盘挂载在其他位置，替换 `checkout`。

```sh
module="$(pwd)/chromium"
checkout="$HOME/chromium-baseline"
pin="$(python3 "$module/verify_upstream.py")" &&
  mkdir "$checkout" &&
  cd "$checkout" &&
  gclient config --name=src https://chromium.googlesource.com/chromium/src.git &&
  gclient sync --no-history --nohooks --revision "src@$pin"
```

检出成功后执行构建。Ubuntu 安装系统依赖时可能提示输入密码。首次全量编译从四个并行任务开始。

```sh
(
  set -eu
  cd "$checkout/src"
  test "$(git rev-parse HEAD)" = "$pin"
  if [ "$(uname -s)" = Linux ]; then
    ./build/install-build-deps.sh
  fi
  gclient runhooks
  mkdir -p out/Moltskin
  cp "$module/args.gn" out/Moltskin/args.gn
  gn gen out/Moltskin
  autoninja -C out/Moltskin -j 4 chrome
)
```

下载中断时，在同一终端进入已经包含 `.gclient` 的 `$checkout`，重跑上面的 `gclient sync` 命令；编译中断可直接重跑构建步骤。

## 验收基线

在 `$checkout/src` 下，Linux 的可执行文件为 `out/Moltskin/chrome`，Mac 为 `out/Moltskin/Chromium.app/Contents/MacOS/Chromium`。用该文件执行 `--version` 并与锁定版本核对。用全新的测试数据目录打开自有 HTTPS 测试页，正常关闭后重启，记录系统、架构、构建日志和产物摘要。两端分别保留结果，作为接入 Moltskin 启动器前的基线。

## 构建带内置面板和权限设置的预览版

检出干净的锁定版本、同步依赖后，在同一终端执行。单独的输出目录可以保留上面的对照版本。

```sh
(
  set -eu
  cd "$checkout/src"
  test "$(git rev-parse HEAD)" = "$pin"
  for patch in "$module"/patches/*.patch; do
    git apply --check "$patch"
    git apply "$patch"
  done
  out=out/MoltskinLocation
  if [ "$(uname -s)" = Darwin ]; then out=out/MoltskinHardwarePrivacy; fi
  mkdir -p "$out"
  cp "$module/args.gn" "$out/args.gn"
  gn gen "$out"
  autoninja -C "$out" -j 4 chrome
)
```

## 验证两个环境

在有桌面会话和已安装字体的机器上，从 Moltskin 仓库根目录执行。测试使用当前 Python 环境中的 Playwright 和指定的浏览器文件。

```sh
browser="$checkout/src/out/MoltskinLocation/chrome"
if [ "$(uname -s)" = Darwin ]; then
  browser="$checkout/src/out/MoltskinHardwarePrivacy/Chromium.app/Contents/MacOS/Chromium"
fi
"$browser" --version --moltskin-capabilities
python3 chromium/check_local_fonts.py "$browser"
MOLTSKIN_CHROMIUM_BINARY="$browser" MOLTSKIN_TEST_NATIVE_BROWSERS=1 python3 -m pytest -q \
  tests/test_chromium_font_rendering.py tests/test_chromium_storage_isolation.py \
  tests/test_chromium_media_capture.py tests/test_chromium_geolocation.py \
  tests/test_chromium_graphics_audio.py tests/test_chromium_graphics_privacy.py \
  tests/test_chromium_worker_canvas_policy.py tests/test_chromium_hardware_reporting.py tests/test_chromium_platform.py
```

测试在本机启动临时网页和两个独立的浏览器，给相同站点授予字体权限。通过条件为标准环境读到至少一种字体，限制环境读到零种。关闭后交换两边的策略，使用原数据目录重启，再次检查字体数量和各自的站点存储标记。测试只记录数量。

2026-11-24 在 Apple Silicon 上实测 Chromium `158.0.8127.88`：原版在两个环境均返回 550 个字体条目。应用补丁后，A（标准）返回 550，B（受限）返回 0；交换策略并重启后，A 返回 0，B 返回 550，两边的存储标记均与重启前一致。标准模式下的具体数量取决于本机字体。Linux x86-64 尚待实机验证。

2026-11-25 在同一台 Mac 上验证了媒体采集策略。两个独立环境都已授予同一测试站点摄像头和麦克风权限，测试使用 Chromium 模拟设备。标准环境报告两项权限均为 `granted`，音频、视频及组合请求均成功。阻止环境报告两项均为 `denied`，三种请求都返回 `NotAllowedError`。交换策略并重启后，两边各自应用新策略，原站点存储也各自保留。

测试也在发起采集前通过 `enumerateDevices()` 检查了音视频输入设备。标准环境能读取获准设备的名称、设备 ID 和分组 ID。阻止环境中，每类只出现一条匿名记录，三个字段均为空。回归测试只记录这些字段是否存在。

定位回归在两个临时 Chromium 环境中为同一本地站点授予权限，并仅使用模拟坐标。标准环境的单次和持续定位均返回测试坐标。阻止环境的权限状态为 `denied`，两个接口都返回 `PERMISSION_DENIED`。交换设置并重启后再次检查。

**网页字体。** 在这台 Mac 上，两个环境交换字体策略前后，测试段落从缺失字体回退到等宽字体时，每一行的宽度、位置和高度均一致。衬线字体对照组的排版存在可测量的差异。测试页从本机 HTTP 服务加载了一款图标字体，并确认 Chromium 实际用它绘制了测试字符。此用例覆盖拉丁段落和一个图标字形。

**同站点存储。** 两个临时环境通过面板的启动和停止 API 各启停三次。同一测试站点在两个环境里用同名键分别写入 A、B 标记，覆盖 `localStorage`、IndexedDB 和 Cache Storage。两边重启后各自读回原值。在 B 保持打开时修改并清除 A 的测试数据，再重启两个环境，A 的三项记录都已清除，B 三项都读回了 B。Ubuntu x86-64 的已安装内核也通过了这项双环境验收。
