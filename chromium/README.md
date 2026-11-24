# Chromium 源码构建

第一阶段构建未经 Moltskin 修改的 Chromium，保留它作为后续内核补丁的测试对照。上游版本和提交记录在 [upstream.json](upstream.json)。当前面板使用 Camoufox；Chromium 的面板启动入口属于下一阶段。

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
