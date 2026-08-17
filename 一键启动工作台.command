#!/bin/zsh

# 一键启动 AI Factory 工作台（launchd 托管版，2026-08-14）
# 用法：双击本文件；或终端里执行 `zsh 一键启动工作台.command`
# 行为：服务已在运行 → 直接打开工作台页面；没在运行 → 用 launchd 拉起后打开。

set -u

PROJECT_DIR="${0:A:h}"
WORKBENCH_URL="http://127.0.0.1:8765/command-center?v=2026-08-01-ui-state-v1"
HEALTH_URL="http://127.0.0.1:8765/health"
JOB_LABEL="com.crossborder.ai-factory.workbench"

is_healthy() {
  /usr/bin/curl --silent --fail --max-time 2 "$HEALTH_URL" >/dev/null 2>&1
}

if is_healthy; then
  echo "✅ 工作台已在运行，正在打开浏览器。"
  /usr/bin/open "$WORKBENCH_URL"
  exit 0
fi

echo "工作台未运行，正在通过 launchd 启动（会自动注册开机自启 + 崩溃自动拉起）..."
/bin/launchctl kickstart -k "gui/$(/usr/bin/id -u)/$JOB_LABEL" 2>/dev/null || {
  echo "⚠️  launchd 任务未注册，尝试用自带脚本启动..."
  nohup /bin/zsh "$PROJECT_DIR/scripts/run_workbench_service.sh" \
    "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR" \
    >"$PROJECT_DIR/logs/workbench-server.log" 2>&1 &
}

for _ in {1..20}; do
  if is_healthy; then
    echo "✅ 工作台已启动：$WORKBENCH_URL"
    /usr/bin/open "$WORKBENCH_URL"
    exit 0
  fi
  /bin/sleep 0.5
done

echo "❌ 工作台 10 秒内未就绪。"
echo "   排查：tail -50 '$PROJECT_DIR/logs/workbench-server.log'"
echo "   或看 launchd 日志：~/Library/Logs/JLCGlobal/workbench-launchd-error.log"
read "?按回车键关闭..."
exit 1
