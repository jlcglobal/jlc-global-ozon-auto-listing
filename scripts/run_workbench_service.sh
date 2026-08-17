#!/bin/zsh

set -u

PYTHON_BIN="$1"
PROJECT_DIR="$2"
STOP_FILE="$PROJECT_DIR/runtime/workbench-stop-requested"
SERVICE_STATE_DIR="/Users/apple/Library/Application Support/JLCGlobal"
PID_FILE="$SERVICE_STATE_DIR/workbench-server.pid"

cd "$PROJECT_DIR" || exit 1
/bin/mkdir -p "$SERVICE_STATE_DIR"
SERVER_PID=""

cleanup() {
  if [[ -n "$SERVER_PID" ]] && /bin/kill -0 "$SERVER_PID" 2>/dev/null; then
    /bin/kill -TERM "$SERVER_PID" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
}

trap cleanup EXIT
trap 'cleanup; exit 0' INT TERM

while [[ ! -f "$STOP_FILE" ]]; do
  /usr/bin/caffeinate -ims "$PYTHON_BIN" -m uvicorn app:app \
    --app-dir "$PROJECT_DIR/collector/local-ingest" \
    --host 0.0.0.0 \
    --port 8765 &
  SERVER_PID=$!
  echo "$SERVER_PID" > "$PID_FILE"

  # KeepAlive can restart a crashed process, but it cannot detect a process
  # that is still listening while its request loop is wedged. Active product
  # batches can temporarily saturate local I/O, so never use the short idle
  # threshold while a batch runner is alive.
  /bin/sleep 5
  FAILED_PROBES=0
  while /bin/kill -0 "$SERVER_PID" 2>/dev/null; do
    if /usr/bin/curl --silent --show-error --fail --max-time 5 \
      http://127.0.0.1:8765/health >/dev/null 2>&1; then
      FAILED_PROBES=0
    else
      FAILED_PROBES=$((FAILED_PROBES + 1))
      FAILURE_LIMIT=5
      if /usr/bin/pgrep -f "$PROJECT_DIR/scripts/run_batch.py" >/dev/null 2>&1; then
        FAILURE_LIMIT=20
      fi
      if [[ "$FAILED_PROBES" -ge "$FAILURE_LIMIT" ]]; then
        echo "工作台连续 $FAILED_PROBES 次健康检查失败，自动重启。"
        /bin/kill -TERM "$SERVER_PID" 2>/dev/null || true
        /bin/sleep 3
        /bin/kill -KILL "$SERVER_PID" 2>/dev/null || true
        break
      fi
    fi
    /bin/sleep 15
  done

  wait "$SERVER_PID" 2>/dev/null
  EXIT_CODE=$?
  SERVER_PID=""
  rm -f "$PID_FILE"
  if [[ -f "$STOP_FILE" ]]; then
    echo "工作台已按用户要求安全退出。"
    break
  fi
  echo "工作台进程退出（代码 $EXIT_CODE），2秒后自动重启。"
  /bin/sleep 2
done
