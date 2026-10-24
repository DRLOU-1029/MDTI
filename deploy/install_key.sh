#!/bin/sh
# 给 ubuntu 用户安装部署公钥（可重复执行，不会重复追加）
#   sh install_key.sh "ssh-ed25519 AAAA... you@host"
# 不传参数时提示用法，避免把某台机器的公钥硬编码进仓库。
set -e

PUB="${1:-}"
if [ -z "$PUB" ]; then
    echo "用法: sh install_key.sh \"ssh-ed25519 AAAA... comment\"" >&2
    exit 2
fi

mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"

if grep -qF "mdti-deploy" "$HOME/.ssh/authorized_keys"; then
    echo "already installed"
else
    printf '%s\n' "$PUB" >> "$HOME/.ssh/authorized_keys"
    echo "installed"
fi

echo "authorized_keys lines: $(wc -l < "$HOME/.ssh/authorized_keys")"
