# Moltskin Chromium 开发预览

本地控制面板和命令行都可以启动 Chromium 开发包。每个环境都有独立的数据目录和本机字体访问设置。

## 打开浏览器内的控制面板

构建包含下方两个补丁的 Chromium，按项目快速开始安装 Python 依赖。在仓库根目录用同一 Python 环境运行 `python chromium/open_panel.py`。启动器会启动或复用本机控制服务，在 Moltskin Chromium 里打开管理标签页。macOS 也可以运行一次 `python chromium/install_panel_macos.py`，以后双击用户「应用程序」目录中的 `Moltskin.app` 即可打开。

在管理页点击「创建窗口」，选择 Chromium、填写名称并选择字体访问策略，然后在列表中点「打开」。工作环境默认打开空白页；在地址栏输入 `chrome://moltskin/` 可以回到面板。这个浏览器入口在当前标签页打开本机服务，地址栏会显示实际的回环地址。要修改字体策略，先关闭对应环境，编辑后再次打开。

已有环境继续使用创建时的内核。面板根据环境 ID 分配数据目录，命令行预览的数据位于自己的目录中。如果开发包放在自定义路径，启动面板前设置 `MOLTSKIN_CHROMIUM_BINARY`，指向 Mac 的 `Chromium.app` 或 Linux 的 `chrome` 可执行文件。面板会显示实际检测到的内核版本。

Chromium 环境可在浏览器中登录并保留本机数据。面板里的 Cookie 文件管理、跨环境状态同步和备份中的登录态目前仅支持 Camoufox。Chromium 环境的名称和字体策略随面板数据库一起备份。

## 临时打开看效果

在仓库根目录执行 `python3 chromium/launch_preview.py`。每次都会打开全新的临时环境，正常退出浏览器后清理数据。macOS 临时预览使用 Chromium 的测试钥匙串处理 Cookie 加密，不读取登录钥匙串里的「Chromium Safe Storage」。需要保留登录状态时，使用下面的命名环境。[Chromium 开发构建说明](https://chromium.googlesource.com/chromium/src/+/main/docs/mac_build_instructions.md#avoiding-system-permissions-dialogs-after-each-build)

## 打开两个环境

在 Moltskin 仓库根目录打开两个终端，分别执行一条命令。

```sh
python3 chromium/launch_preview.py a --local-fonts standard
python3 chromium/launch_preview.py b --local-fonts restricted
```

网页调用 `window.queryLocalFonts()` 时，标准模式沿用 Chromium 的站点授权流程。限制模式在浏览器进程中直接返回空列表，即使该站点已有授权。这个设置只作用于 Local Font Access API，网页仍可正常使用 CSS 字体。

关闭 B 的浏览器后，执行 `python3 chromium/launch_preview.py b` 即可恢复上次设置。要调整设置，关闭该环境后重新启动并指定 `--local-fonts`。每个环境的设置和浏览数据保存在 `data/chromium-preview/<环境名>/`，可用 `MOLTSKIN_DATA_DIR` 指定数据根目录。

macOS 命名环境使用登录钥匙串保护 Cookie。如果系统询问当前开发版是否可访问「Chromium Safe Storage」，确认是自己启动的版本后，在系统弹窗输入登录钥匙串密码，选择「始终允许」可记住此次授权。重新编译的开发版可能需要重新授权。[Apple 钥匙串说明](https://support.apple.com/guide/mac-help/kychn002/mac)

启动器优先查找下文的 `out/MoltskinPanel` 开发包，也能用以前的 `out/MoltskinFont` 做字体预览。命令行字体预览还可以用 `--binary` 指定其他开发包。启动前会向内核确认它支持所需功能。

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

## 构建带内置面板入口和字体设置的预览版

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
  mkdir -p out/MoltskinPanel
  cp "$module/args.gn" out/MoltskinPanel/args.gn
  gn gen out/MoltskinPanel
  autoninja -C out/MoltskinPanel -j 4 chrome
)
```

## 验证两个环境

在有桌面会话和已安装字体的机器上，从 Moltskin 仓库根目录执行。测试使用当前 Python 环境中的 Playwright 和指定的浏览器文件。

```sh
browser="$checkout/src/out/MoltskinPanel/chrome"
if [ "$(uname -s)" = Darwin ]; then
  browser="$checkout/src/out/MoltskinPanel/Chromium.app/Contents/MacOS/Chromium"
fi
"$browser" --version --moltskin-capabilities
python3 chromium/check_local_fonts.py "$browser"
```

测试在本机启动临时网页和两个独立的浏览器，给相同站点授予字体权限。通过条件为标准环境读到至少一种字体，限制环境读到零种。关闭后交换两边的策略，使用原数据目录重启，再次检查字体数量和各自的站点存储标记。测试只记录数量。

2026-11-24 在 Apple Silicon 上实测 Chromium `158.0.8127.88`：原版在两个环境均返回 550 个字体条目。应用补丁后，A（标准）返回 550，B（受限）返回 0；交换策略并重启后，A 返回 0，B 返回 550，两边的存储标记均与重启前一致。标准模式下的具体数量取决于本机字体。Linux x86-64 尚待实机验证。
