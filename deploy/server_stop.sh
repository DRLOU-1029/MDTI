#!/bin/sh
# 停止 MDTI 网页调试台
pkill -f 'web/app.py' 2>/dev/null && echo "stopped" || echo "not running"
