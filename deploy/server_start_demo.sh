#!/bin/sh
# 演示实例：不连 MCP、不碰账号，只用脱敏快照
#   sh server_start_demo.sh          启动（默认 1146）
#   sh server_start_demo.sh 8080     换端口
set -e

PORT="${1:-1146}"
APP_DIR="/opt/mdti"

cd "$APP_DIR"

# 只停同端口的实例，不要误伤正式实例
pkill -f "port $PORT" 2>/dev/null || true
sleep 0.4

MDTI_DEMO=1 setsid nohup python3 web/app.py --host 0.0.0.0 --port "$PORT" \
    > "$APP_DIR/demo.log" 2>&1 < /dev/null &
sleep 1.5

echo "--- listening ---"
ss -tlnp 2>/dev/null | grep ":$PORT" || echo "NOT LISTENING on $PORT"

echo "--- mode ---"
python3 - "$PORT" <<'PY'
import sys, urllib.request
port = sys.argv[1]
base = f"http://127.0.0.1:{port}"
print("mode   :", urllib.request.urlopen(base + "/api/mode", timeout=5).read().decode())
print("healthz:", urllib.request.urlopen(base + "/healthz", timeout=5).read().decode())
PY
