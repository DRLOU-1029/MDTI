#!/bin/sh
# MDTI 网页调试台启停脚本（服务器端用）
#   sh server_start.sh              启动（默认 0.0.0.0:1145，对外可访问）
#   sh server_start.sh 8080         换端口
#   sh server_start.sh 8080 127.0.0.1   换端口 + 只监听回环
#   sh server_stop.sh               停止
set -e

PORT="${1:-1145}"
HOST="${2:-0.0.0.0}"
APP_DIR="/opt/mdti"

cd "$APP_DIR"

pkill -f "port $PORT" 2>/dev/null || true
sleep 0.4

setsid nohup python3 web/app.py --host "$HOST" --port "$PORT" > "$APP_DIR/web.log" 2>&1 < /dev/null &
sleep 1.5

echo "--- listening ---"
ss -tlnp 2>/dev/null | grep ":$PORT" || echo "NOT LISTENING on $PORT"

echo "--- self check ---"
python3 - "$PORT" <<'PY'
import sys, urllib.request
port = sys.argv[1]
base = f"http://127.0.0.1:{port}"
print("healthz :", urllib.request.urlopen(base + "/healthz", timeout=5).read().decode())
print("index   :", len(urllib.request.urlopen(base + "/", timeout=5).read()), "bytes")
print("types   :", len(urllib.request.urlopen(base + "/api/types", timeout=5).read()), "bytes")
PY

echo "--- log tail ---"
tail -n 5 "$APP_DIR/web.log" 2>/dev/null || true
