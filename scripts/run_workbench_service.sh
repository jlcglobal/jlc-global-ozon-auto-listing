#!/bin/zsh

set -u

PYTHON_BIN="$1"
PROJECT_DIR="$2"
STOP_FILE="$PROJECT_DIR/runtime/workbench-stop-requested"
PID_FILE="$PROJECT_DIR/runtime/workbench-server.pid"

cd "$PROJECT_DIR" || exit 1
trap 'rm -f "$PID_FILE"' EXIT

while [[ ! -f "$STOP_FILE" ]]; do
  /usr/bin/caffeinate -ims "$PYTHON_BIN" -m uvicorn app:app \
    --app-dir "$PROJECT_DIR/collector/local-ingest" \
    --host 0.0.0.0 \
    --port 8765
  EXIT_CODE=$?
  if [[ -f "$STOP_FILE" ]]; then
    echo "工作台已按用户要求安全退出。"
    break
  fi
  echo "工作台进程退出（代码 $EXIT_CODE），2秒后自动重启。"
  /bin/sleep 2
done
