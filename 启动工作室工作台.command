#!/bin/zsh

set -u

PROJECT_DIR="${0:A:h}"
WORKBENCH_URL="http://127.0.0.1:8765/workbench"
HEALTH_URL="http://127.0.0.1:8765/health"
PID_FILE="$PROJECT_DIR/runtime/workbench-server.pid"

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
echo $$ > "$PID_FILE"
trap 'rm -f "$PID_FILE"' EXIT INT TERM

(
  for attempt in {1..40}; do
    /bin/sleep 0.25
    if is_healthy; then
      /usr/bin/open "$WORKBENCH_URL"
      exit 0
    fi
  done
  echo "工作台启动超时，请查看当前终端中的错误。"
) &

echo "正在启动跨境AI工厂工作台..."
echo "工作台地址：$WORKBENCH_URL"
echo "局域网服务端口：8765"
echo "请保持这个终端窗口开启。"
echo

exec /usr/bin/caffeinate -dims "$PYTHON_BIN" -m uvicorn app:app \
  --app-dir "$PROJECT_DIR/collector/local-ingest" \
  --host 0.0.0.0 \
  --port 8765
