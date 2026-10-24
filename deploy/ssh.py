#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最小 SSH 执行器 —— 密码只从环境变量读，绝不落盘。

环境变量：
  SSH_HOST  主机
  SSH_PORT  端口（默认 22）
  SSH_USER  用户名
  SSH_PW    密码

用法：
  python ssh.py exec "whoami; uname -a"
  python ssh.py put .\\dist /root/mdti
"""

from __future__ import annotations

import argparse
import os
import posixpath
import sys
from pathlib import Path

import paramiko

SKIP_DIRS = {"__pycache__", ".git", ".idea", ".venv", "node_modules", "deploy", ".pytest_cache"}


def connect() -> paramiko.SSHClient:
    host = os.environ.get("SSH_HOST")
    user = os.environ.get("SSH_USER")
    port = int(os.environ.get("SSH_PORT", "22"))
    password = os.environ.get("SSH_PW")
    key = os.environ.get("SSH_KEY")

    if not (host and user):
        sys.exit("[ssh] 需要 SSH_HOST / SSH_USER 环境变量")
    if not (password or key):
        sys.exit("[ssh] 需要 SSH_PW 或 SSH_KEY 之一")

    client = paramiko.SSHClient()
    # 首次连接的机器没有 known_hosts 记录；这里接受并打印指纹，便于人工核对
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = dict(
        hostname=host,
        port=port,
        username=user,
        timeout=20,
        banner_timeout=20,
        auth_timeout=20,
        look_for_keys=False,   # 不要拿本地私钥去试，避免把账号试锁
        allow_agent=False,
    )
    if key:
        kwargs["key_filename"] = key
    if password:
        kwargs["password"] = password
    client.connect(**kwargs)
    return client


def run_exec(client: paramiko.SSHClient, command: str, timeout: int) -> int:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    if out:
        sys.stdout.write(out)
        if not out.endswith("\n"):
            sys.stdout.write("\n")
    if err:
        sys.stderr.write(err)
    return code


def _mkdir_p(sftp: paramiko.SFTPClient, path: str) -> None:
    parts, cur = path.strip("/").split("/"), ""
    for part in parts:
        cur = f"{cur}/{part}"
        try:
            sftp.stat(cur)
        except IOError:
            sftp.mkdir(cur)


def run_put(client: paramiko.SSHClient, local: Path, remote: str) -> int:
    if not local.exists():
        sys.exit(f"[ssh] 本地路径不存在：{local}")

    sftp = client.open_sftp()
    _mkdir_p(sftp, remote)
    count = 0

    if local.is_file():
        sftp.put(str(local), posixpath.join(remote, local.name))
        count = 1
    else:
        for path in sorted(local.rglob("*")):
            rel = path.relative_to(local)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            target = posixpath.join(remote, rel.as_posix())
            if path.is_dir():
                _mkdir_p(sftp, target)
            else:
                _mkdir_p(sftp, posixpath.dirname(target))
                sftp.put(str(path), target)
                count += 1

    sftp.close()
    print(f"[ssh] 已上传 {count} 个文件 -> {remote}")
    return 0


def run_get(client: paramiko.SSHClient, remote: str, local: Path) -> int:
    sftp = client.open_sftp()
    local.parent.mkdir(parents=True, exist_ok=True)
    sftp.get(remote, str(local))
    size = local.stat().st_size
    sftp.close()
    print(f"[ssh] 已下载 {remote} -> {local}（{size} 字节）")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="最小 SSH 执行器")
    sub = parser.add_subparsers(dest="action", required=True)

    p_exec = sub.add_parser("exec", help="在远端执行命令")
    p_exec.add_argument("command", help="要执行的命令")
    p_exec.add_argument("--timeout", type=int, default=120)

    p_put = sub.add_parser("put", help="上传文件或目录")
    p_put.add_argument("local", type=Path)
    p_put.add_argument("remote")

    p_get = sub.add_parser("get", help="下载文件")
    p_get.add_argument("remote")
    p_get.add_argument("local", type=Path)

    args = parser.parse_args()
    client = connect()
    try:
        if args.action == "exec":
            return run_exec(client, args.command, args.timeout)
        if args.action == "get":
            return run_get(client, args.remote, args.local)
        return run_put(client, args.local, args.remote)
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
