# Moltskin Chromium 开发预览

Chromium 预览版为每个环境提供独立的数据目录和本机字体访问设置。已有开发包时可直接用命令行启动。当前桌面面板使用 Camoufox。

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

启动器默认查找下文的 `out/MoltskinFont` 开发包。其他位置可用 `--binary` 指定 macOS 的 `Chromium.app` 或 Linux 的 `chrome` 可执行文件。启动前会向内核确认它支持这项设置。

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

## 构建带字体设置的预览版

检出干净的锁定版本、同步依赖后，在同一终端执行。单独的输出目录可以保留上面的对照版本。

```sh
(
  set -eu
  cd "$checkout/src"
  test "$(git rev-parse HEAD)" = "$pin"
  patch="$module/patches/0001-restrict-local-font-access.patch"
  git apply --check "$patch"
  git apply "$patch"
  mkdir -p out/MoltskinFont
  cp "$module/args.gn" out/MoltskinFont/args.gn
  gn gen out/MoltskinFont
  autoninja -C out/MoltskinFont -j 4 chrome
)
```

## 验证两个环境

在有桌面会话和已安装字体的机器上，从 Moltskin 仓库根目录执行。测试使用当前 Python 环境中的 Playwright 和指定的浏览器文件。

```sh
browser="$checkout/src/out/MoltskinFont/chrome"
if [ "$(uname -s)" = Darwin ]; then
  browser="$checkout/src/out/MoltskinFont/Chromium.app/Contents/MacOS/Chromium"
fi
"$browser" --version --moltskin-capabilities
python3 chromium/check_local_fonts.py "$browser"
```

测试在本机启动临时网页和两个独立的浏览器，给相同站点授予字体权限。通过条件为标准环境读到至少一种字体，限制环境读到零种。关闭后交换两边的策略，使用原数据目录重启，再次检查字体数量和各自的站点存储标记。测试只记录数量。

2026-11-24 在 Apple Silicon 上实测 Chromium `158.0.8127.88`：原版在两个环境均返回 550 个字体条目。应用补丁后，A（标准）返回 550，B（受限）返回 0；交换策略并重启后，A 返回 0，B 返回 550，两边的存储标记均与重启前一致。标准模式下的具体数量取决于本机字体。Linux x86-64 尚待实机验证。
