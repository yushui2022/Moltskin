#!/bin/sh
# Finder opens this file in Terminal. The SSH client stays attached while viewing.
set -eu
cd "$(dirname "$0")"
if [ "$#" -gt 0 ]; then
    exec python3 view.py "$@"
fi
printf 'Linux 服务器的 SSH 地址或别名：'
IFS= read -r host
[ -n "$host" ] || exit 0
printf '服务器上 Moltskin 的目录 [~/Moltskin]：'
IFS= read -r repo
if [ -n "$repo" ]; then
    exec python3 view.py "$host" --repo "$repo"
fi
exec python3 view.py "$host"
