# Moltskin Chromium 开发预览

本地控制面板和命令行都可以启动 Chromium 开发包。每个环境都有独立的数据目录，可以设置本机字体、摄像头与麦克风采集、地理位置三类权限。各项指纹面的支持状态、实测范围与平台差异见[指纹能力对照](fingerprint-coverage.md)。

## 打开浏览器内的控制面板

应用下方补丁并构建 Chromium，按项目快速开始安装 Python 依赖。在仓库根目录用同一 Python 环境运行 `python chromium/open_panel.py`。启动器会启动或复用本机控制服务，在 Moltskin Chromium 里打开管理标签页。macOS 也可以运行一次 `python chromium/install_panel_macos.py`，以后双击用户「应用程序」目录中的 `Moltskin.app` 即可打开。

在管理页点击「创建窗口」，填写名称，在「浏览器与系统」选择 Moltskin Chromium，并设置字体、媒体和定位权限，然后在列表中点「打开」。工作环境默认打开空白页。点击地址栏右侧、头像左侧的 Moltskin 变色龙按钮即可打开控制面板首页。已有面板标签时，按钮会切回该标签并回到首页，工作标签会保留。在地址栏输入 `chrome://moltskin/` 也可以回到面板。这个浏览器入口在当前标签页打开本机服务，地址栏会显示实际的回环地址。要修改权限，先关闭对应环境，编辑后再次打开。

「浏览器与系统」显示运行浏览器的主机系统与架构。内核版本默认跟随本机默认构建，也可为每个窗口固定一个已安装版本。自定义默认构建时，在启动面板前设置 `MOLTSKIN_CHROMIUM_BINARY`，指向 Mac 的 `Chromium.app` 或 Linux 的 `chrome`。要提供更多版本，可设置 `MOLTSKIN_CHROMIUM_BUILDS_DIR`，每个子目录分别放置一个 `Chromium.app` 或 `chrome`。列表使用程序实际报告的版本。使用旧主版本测试时应新建环境，已由更新主版本使用的数据目录有降级保护。

在「浏览器与系统」可以为单个 Chromium 窗口填写 User-Agent。留空使用所选内核的默认 UA。要修改已创建的窗口，先关闭窗口，保存后重新打开。要核对结果，在该环境打开本机 `/runtime.html`，页面会分别显示本机服务收到的 HTTP 请求头和网页通过 `navigator.userAgent` 读到的值，以及此窗口实际公开的平台版本、处理器架构和位数。

「本地兼容性预览」支持选择 Windows、macOS 或 Linux，填写可选的平台版本，并选择 ARM64 或 x86-64。每个窗口可以保存自己的预览设置。点击运行后，面板使用选定内核，在一次性离线测试页实测 Chromium 的 User-Agent Client Hints。读数中的 `x86` 加 `64` 表示 x86-64。预览只作用于该测试页，工作窗口正常访问网站时仍使用自身的平台和架构。

每个窗口可以单独静音、设置渲染进程数量的软上限，或限制 V8 老生代堆大小。老生代堆用于存放存活时间较长的 JavaScript 对象，设备内存和操作系统可用内存是独立的指标。「查看当前浏览器实测值」也会显示窗口的语言、时区、硬件并发数、设备内存档位及尺寸。

Chromium 环境可在浏览器中登录并保留本机数据。面板里的 Cookie 文件管理、跨环境状态同步和备份中的登录态目前仅支持 Camoufox。Chromium 环境的名称和权限设置随面板数据库一起备份。

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

关闭 B 的浏览器后，执行 `python3 chromium/launch_preview.py b` 即可恢复上次设置。要调整设置，关闭该环境后重新启动并指定 `--local-fonts`、`--media-capture` 或 `--geolocation`。每个环境的设置和浏览数据保存在 `data/chromium-preview/<环境名>/`，可用 `MOLTSKIN_DATA_DIR` 指定数据根目录。

macOS 命名环境使用登录钥匙串保护 Cookie。如果系统询问当前开发版是否可访问「Chromium Safe Storage」，确认是自己启动的版本后，在系统弹窗输入登录钥匙串密码，选择「始终允许」可记住此次授权。重新编译的开发版可能需要重新授权。[Apple 钥匙串说明](https://support.apple.com/guide/mac-help/kychn002/mac)

启动器优先查找下文的 `out/MoltskinLocation` 开发包，也能打开早期开发包。选择了阻止采集或定位的环境，需要使用包含对应权限补丁的构建。命令行字体预览还可以用 `--binary` 指定其他开发包。启动前会向内核确认它支持所需功能。

## 检查上游版本

在 Moltskin 仓库根目录执行。此命令只查询官方 Git 标签，成功后打印可用于检出的完整提交号。

```sh
python3 chromium/verify_upstream.py
```

## 首次检出与构建

Linux 首批目标为 Ubuntu x86-64（至少 100 GB 空闲磁盘）；macOS 首批目标为 Apple Silicon，需要 Xcode、macOS SDK 和空间充足的 APFS 卷。按对应平台的指南安装 Git、Python 3 和 `depot_tools`，确保终端能找到 `gclient`、`gn` 和 `autoninja`。[Linux 构建指南](https://chromium.googlesource.com/chromium/src/+/main/docs/linux/build_instructions.md) · [macOS 构建指南](https://chromium.googlesource.com/chromium/src/+/main/docs/mac_build_instructions.md)

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
  mkdir -p out/MoltskinLocation
  cp "$module/args.gn" out/MoltskinLocation/args.gn
  gn gen out/MoltskinLocation
  autoninja -C out/MoltskinLocation -j 4 chrome
)
```

## 验证两个环境

在有桌面会话和已安装字体的机器上，从 Moltskin 仓库根目录执行。测试使用当前 Python 环境中的 Playwright 和指定的浏览器文件。

```sh
browser="$checkout/src/out/MoltskinLocation/chrome"
if [ "$(uname -s)" = Darwin ]; then
  browser="$checkout/src/out/MoltskinLocation/Chromium.app/Contents/MacOS/Chromium"
fi
"$browser" --version --moltskin-capabilities
python3 chromium/check_local_fonts.py "$browser"
MOLTSKIN_CHROMIUM_BINARY="$browser" MOLTSKIN_TEST_NATIVE_BROWSERS=1 python3 -m pytest -q \
  tests/test_chromium_font_rendering.py tests/test_chromium_storage_isolation.py \
  tests/test_chromium_media_capture.py tests/test_chromium_geolocation.py \
  tests/test_chromium_graphics_audio.py tests/test_chromium_platform.py
```

测试在本机启动临时网页和两个独立的浏览器，给相同站点授予字体权限。通过条件为标准环境读到至少一种字体，限制环境读到零种。关闭后交换两边的策略，使用原数据目录重启，再次检查字体数量和各自的站点存储标记。测试只记录数量。

2026-11-24 在 Apple Silicon 上实测 Chromium `158.0.8127.88`：原版在两个环境均返回 550 个字体条目。应用补丁后，A（标准）返回 550，B（受限）返回 0；交换策略并重启后，A 返回 0，B 返回 550，两边的存储标记均与重启前一致。标准模式下的具体数量取决于本机字体。Linux x86-64 尚待实机验证。

2026-11-25 在同一台 Mac 上验证了媒体采集策略。两个独立环境都已授予同一测试站点摄像头和麦克风权限，测试使用 Chromium 模拟设备。标准环境报告两项权限均为 `granted`，音频、视频及组合请求均成功。阻止环境报告两项均为 `denied`，三种请求都返回 `NotAllowedError`。交换策略并重启后，两边各自应用新策略，原站点存储也各自保留。

测试也在发起采集前通过 `enumerateDevices()` 检查了音视频输入设备。标准环境能读取获准设备的名称、设备 ID 和分组 ID。阻止环境中，每类只出现一条匿名记录，三个字段均为空。回归测试只记录这些字段是否存在。

定位回归在两个临时 Chromium 环境中为同一本地站点授予权限，并仅使用模拟坐标。标准环境的单次和持续定位均返回测试坐标。阻止环境的权限状态为 `denied`，两个接口都返回 `PERMISSION_DENIED`。交换设置并重启后再次检查。

**网页字体。** 在这台 Mac 上，两个环境交换字体策略前后，测试段落从缺失字体回退到等宽字体时，每一行的宽度、位置和高度均一致。衬线字体对照组的排版存在可测量的差异。测试页从本机 HTTP 服务加载了一款图标字体，并确认 Chromium 实际用它绘制了测试字符。此用例覆盖拉丁段落和一个图标字形。

**同站点存储。** 两个临时环境通过面板的启动和停止 API 各启停三次。同一测试站点在两个环境里用同名键分别写入 A、B 标记，覆盖 `localStorage`、IndexedDB 和 Cache Storage。两边重启后各自读回原值。在 B 保持打开时修改并清除 A 的测试数据，再重启两个环境，A 的三项记录都已清除，B 三项都读回了 B。Linux 实机结果待补。
