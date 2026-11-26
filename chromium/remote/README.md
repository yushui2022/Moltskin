# Linux 远程浏览器窗口

在你的 Mac 或 Linux 电脑上打开一个网页，就能看到 Ubuntu 上的 Moltskin Chromium 标签栏、地址栏和环境工作台。浏览器进程和访问网站的网络连接都在服务器上。Moltskin 启动环境，Xvfb 提供独立显示，x11vnc 传送画面和输入，websockify 将 VNC 接入 WebSocket，noVNC 在本机网页中显示窗口。[noVNC](https://github.com/novnc/noVNC) · [websockify](https://github.com/novnc/websockify)

This preview runs on a dedicated Ubuntu 24.04 amd64 host with one trusted SSH operator. After server setup, run `python3 chromium/remote/view.py HOST` on the client. Repeating the command reconnects to the running browser. Use `--stop` to end the session and remove its temporary profile.

## 首次准备服务器

使用有 SSH 登录权限的普通用户，确认 `systemctl --user status` 能访问该用户的 systemd。服务器和本机使用同一版本的 Moltskin 代码。示例把仓库放在服务器的 `~/Moltskin`，把 Chromium 源码放在 `~/chromium-baseline/src`。已有构建可以直接打包。[Linux 构建步骤](../README.md#linux-x86-64-产品内核)

在服务器上的仓库根目录执行。已有项目虚拟环境时可以复用。

```sh
cd ~/Moltskin
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

python3 chromium/remote/package.py \
    "$HOME/chromium-baseline/src/out/MoltskinLocation" \
    "$HOME/moltskin-linux-preview.deb"

sudo apt-get install -y --reinstall "$HOME/moltskin-linux-preview.deb"
sudo apt-get install -y \
    apparmor xvfb xauth xdotool x11-utils openbox dbus-daemon \
    x11vnc novnc websockify tigervnc-tools fonts-noto-cjk
sudo apparmor_parser -r /etc/apparmor.d/moltskin-preview
```

若 Ubuntu 缺少 `venv` 模块，可用 `sudo apt-get install python3-venv` 安装。软件包将 Chromium 及其资源装到 root 管理的 `/opt/moltskin-preview`。AppArmor 规则允许该路径的 `chrome` 创建浏览器沙箱所需的用户命名空间。升级时用新的构建产物重复打包和安装步骤，并在下一次会话启动前加载规则。

## 打开窗口

在自己电脑上的 Moltskin 仓库中运行以下命令，把 `my-linux-host` 替换为可正常登录的 SSH 主机别名或 `user@host`。客户端需要 Python 3.10 或更新版本，以及已配置 SSH 公钥或 ssh-agent 的 OpenSSH。先用普通 SSH 登录一次，确认服务器主机密钥。Mac 也可以在 Finder 双击 [open.command](open.command)，按提示填写服务器地址。

```sh
python3 chromium/remote/view.py my-linux-host
```

如果服务器上的仓库和 Python 虚拟环境在其他位置，使用远端路径。

```sh
python3 chromium/remote/view.py my-linux-host \
    --repo '~/projects/Moltskin' --python '~/venvs/moltskin/bin/python'
```

启动器会打开系统默认浏览器。网页左侧的 noVNC 把手可收起工具栏。终端为网页保持 SSH 转发。关闭标签页或按 Ctrl+C 断开转发后，再运行同一条命令可接回仍在运行的窗口。如果机器在最后一个 SSH 登录退出时停止用户服务，后台窗口也会结束。需要跨登出重连时，可由管理员运行 `sudo loginctl enable-linger 用户名`。本地管理面板使用的 `8765` 和这里的远程画面是两个独立入口。

## 停止会话

```sh
python3 chromium/remote/view.py my-linux-host --stop
```

自定义服务器路径时，停止命令也要带上相同的 `--repo` 和 `--python`。停止会话将关闭 Chromium 并删除本次预览的临时环境，包括其中的登录状态。再次启动会获得新的环境和连接密码。服务仅监听服务器回环地址。SSH 负责访问控制，此预览面向单个可信操作者。多人团队的成员授权和持久环境管理另见[企业试用验收](../../docs/enterprise-readiness.zh-CN.md)。

## 验收

打开工作台，核对「系统平台」和「内核版本」，在新标签打开一个网页，断开并重连后确认该标签仍在。在服务器运行以下命令，可独立检查两个 Chromium 环境的键鼠输入、同站点存储隔离，以及 Canvas 和 WebGL 图形隐私设置。

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python chromium/check_linux.py /opt/moltskin-preview/chrome
```
