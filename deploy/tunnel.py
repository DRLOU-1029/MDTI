#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地端口转发：浏览器 -> 127.0.0.1:<local> -> SSH -> 服务器 <remote>。

服务器所在网络对非 22 端口做了拦截时，用这个把服务「搬」到本机回环再访问。

  python tunnel.py --local 8000 --remote-port 8000

依赖环境变量：SSH_HOST / SSH_USER，以及 SSH_PW 或 SSH_KEY 之一。
"""

from __future__ import annotations

import argparse
import os
import select
import socket
import sys
import threading

import paramiko


def connect(host: str, user: str, password: str | None, key: str | None) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = dict(
        hostname=host, port=22, username=user,
        timeout=20, banner_timeout=20, auth_timeout=20,
        look_for_keys=False, allow_agent=False,
    )
    if key:
        kwargs["key_filename"] = key
    if password:
        kwargs["password"] = password
    client.connect(**kwargs)
    return client


def pump(sock: socket.socket, chan) -> None:
    """在本地 socket 和 SSH channel 之间双向搬运字节。"""
    try:
        while True:
            readable, _, _ = select.select([sock, chan], [], [])
            if sock in readable:
                data = sock.recv(65536)
                if not data:
                    break
                chan.sendall(data)
            if chan in readable:
                data = chan.recv(65536)
                if not data:
                    break
                sock.sendall(data)
    except (OSError, EOFError):
        pass
    finally:
        for thing in (chan, sock):
            try:
                thing.close()
            except Exception:
                pass


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="SSH 本地端口转发")
    parser.add_argument("--local", type=int, default=8000)
    parser.add_argument("--remote-host", default="127.0.0.1")
    parser.add_argument("--remote-port", type=int, default=8000)
    args = parser.parse_args()

    host = os.environ.get("SSH_HOST")
    user = os.environ.get("SSH_USER")
    if not (host and user):
        sys.exit("[tunnel] 需要 SSH_HOST / SSH_USER")

    client = connect(host, user, os.environ.get("SSH_PW"), os.environ.get("SSH_KEY"))
    transport = client.get_transport()
    if transport is None:
        sys.exit("[tunnel] SSH 连接没有 transport")

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", args.local))
    server.listen(32)
    print(
        f"forward 127.0.0.1:{args.local} -> {host} {args.remote_host}:{args.remote_port}",
        flush=True,
    )

    try:
        while True:
            conn, addr = server.accept()
            try:
                chan = transport.open_channel(
                    "direct-tcpip", (args.remote_host, args.remote_port), addr
                )
            except Exception as exc:  # 服务器拒绝转发时不要整个进程崩掉
                print("[tunnel] open_channel failed:", exc, flush=True)
                conn.close()
                continue
            threading.Thread(target=pump, args=(conn, chan), daemon=True).start()
    except KeyboardInterrupt:
        print("bye")
    finally:
        server.close()
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
