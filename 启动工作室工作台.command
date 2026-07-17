#!/bin/zsh

set -u

PROJECT_DIR="${0:A:h}"
WORKBENCH_URL="http://127.0.0.1:8765/workbench"
HEALTH_URL="http://127.0.0.1:8765/health"
PID_FILE="$PROJECT_DIR/runtime/workbench-server.pid"
LOG_FILE="$PROJECT_DIR/logs/workbench-server.log"
STOP_FILE="$PROJECT_DIR/runtime/workbench-stop-requested"

cd "$PROJECT_DIR" || {
  echo "无法进入项目目录：$PROJECT_DIR"
  read "?按回车键关闭..."
  exit 1
}

is_healthy() {
  /usr/bin/curl --silent --fail --max-time 2 "$HEALTH_URL" >/dev/null 2>&1
}

if is_healthy; then
  echo "工作台已经在运行，正在打开浏览器。"
  /usr/bin/open "$WORKBENCH_URL"
  exit 0
fi

if /usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "端口8765已被其他程序占用，工作台无法启动。"
  /usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN
  read "?按回车键关闭..."
  exit 1
fi

PYTHON_BIN=""
PYTHON_CANDIDATES=(
  "$PROJECT_DIR/.venv/bin/python3"
  "/Library/Developer/CommandLineTools/usr/bin/python3"
  "${commands[python3]:-}"
)

for candidate in "${PYTHON_CANDIDATES[@]}"; do
  if [[ -n "$candidate" && -x "$candidate" ]] && \
    "$candidate" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
    PYTHON_BIN="$candidate"
    break
  fi
done

if [[ -z "$PYTHON_BIN" ]]; then
  echo "没有找到已安装FastAPI和Uvicorn的Python环境。"
  echo "请先在项目目录执行：python3 -m pip install -r requirements.txt"
  read "?按回车键关闭..."
  exit 1
fi

mkdir -p "$PROJECT_DIR/runtime" "$PROJECT_DIR/logs"
rm -f "$PID_FILE"
rm -f "$STOP_FILE"

echo "正在启动跨境AI工厂工作台..."
echo "工作台地址：$WORKBENCH_URL"
echo "局域网服务端口：8765"
echo "启动后可以关闭这个终端窗口。"

/usr/bin/nohup /bin/zsh "$PROJECT_DIR/scripts/run_workbench_service.sh" \
  "$PYTHON_BIN" "$PROJECT_DIR" \
  >> "$LOG_FILE" 2>&1 < /dev/null &

SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"
disown "$SERVER_PID" 2>/dev/null || true

for attempt in {1..40}; do
  /bin/sleep 0.25
  if is_healthy; then
    echo "工作台已在后台运行。"
    /usr/bin/open "$WORKBENCH_URL"
    exit 0
  fi
done

echo "工作台启动失败，最近错误如下："
/usr/bin/tail -30 "$LOG_FILE" 2>/dev/null || true
rm -f "$PID_FILE"
read "?按回车键关闭..."
exit 1
