# JLC GLOBAL 工作台 UI 设计系统 v4（浅色 Bento 版）

> 设计依据：nextlevelbuilder/ui-ux-pro-max-skill 输出的
> 「Data-Dense Dashboard（数据密集型仪表盘）」+「Bento Box Grid（模块化卡片栅格）」规范，
> 配色采用电商 Emerald 绿 + 橙点缀的浅色方案。

## 一、设计 Token（唯一维护入口）

所有 UI 视觉都收敛在 collector/workbench-command-center/src/design-system.css：

| Token | 值 | 用途 |
| --- | --- | --- |
| --jlc-primary | #059669 | 主色（按钮、选中态、焦点环） |
| --jlc-accent | #ea580c | 点缀色（警告、批量操作高亮） |
| --jlc-bg | #f5f7fa | 页面底色 |
| --jlc-surface | #ffffff | 卡片/面板底色 |
| --jlc-text | #0f172a | 正文 |
| --jlc-text-2 | #475569 | 次要文字（约 7:1） |
| --jlc-text-3 | #5b6b80 | 弱化文字（约 4.6:1） |
| --jlc-border | #e2e8f0 | 边框 |
| --jlc-radius-lg | 20px | 大卡片圆角 |
| --jlc-shadow / --jlc-shadow-hover | 柔和双层阴影 | 卡片层级 |
| --jlc-font-ui | Inter -> 微软雅黑 等 | UI 字体栈 |
| --jlc-font-mono | JetBrains Mono -> Consolas | 数字/编码 |

antd 组件 token 在 src/main.tsx 的 ConfigProvider 中同步维护
（colorPrimary、colorBgLayout、Menu、Layout、Table、Tabs 等）。

## 二、设计原则（照此维护）

1. 浅色实底，拒绝玻璃拟态：body * { backdrop-filter: none }，
   所有面板必须是纯色白底，禁止恢复 blur() 或半透明深色背景。
2. 文字对比度：小字号 4.5:1 以上、大字号 3:1 以上，弱化文字统一用 --jlc-text-3。
3. 卡片即 Bento 模块：白色、--jlc-radius-lg 圆角、细边框、柔和阴影，
   hover 时边框加深 + 阴影抬升（200-250ms）。
4. 数字/指标用等宽字体（--jlc-font-mono），提升可扫读性。
5. 可点击元素：cursor: pointer + hover 反馈 + 键盘 :focus-visible 焦点环。
6. 动效克制：只做透明度/位移/阴影过渡，遵守 prefers-reduced-motion。

## 三、如何修改 UI

1. 改 design-system.css（新样式/修 bug 都加在这里，不要再往 styles.css 叠补丁）。
2. 涉及 antd 组件则同步改 main.tsx 的 ConfigProvider token。
3. 组件硬编码的颜色类（如 bg-[...]）应改到 src/components/ui/*.tsx。
4. 构建并重启：

   cd collector/workbench-command-center
   pnpm run build
   cd ../..
   scripts/stop-workbench.ps1
   然后启动 uvicorn（collector/local-ingest 的 app:app，端口 8765）
   浏览器打开 http://127.0.0.1:8765/command-center

## 四、质量检查（每次改完必跑）

用 Playwright（系统 Edge 通道）打开 /command-center 后执行：

- 断言 getComputedStyle 无任何 backdrop-filter；
- 遍历可见文本节点，计算前景/背景对比度，小字号 4.5、大字号 3 以上；
- 检查 document.documentElement.scrollWidth <= clientWidth（无横向滚动）。

审计脚本示例位于 collector/workbench-command-center/scripts/
（可参考 verify-command-center.mjs 扩展）。

## 五、版本与缓存

- 前端产物构建到 dist/，由 collector/local-ingest/app.py 以
  COMMAND_CENTER_VERSION（如 2026-08-16-ui-v4-bento-light）作为缓存版本号；
- 每次改完 UI 必须：pnpm run build -> 更新 app.py 的版本号 -> 重启工作台，
  否则浏览器会继续用旧资源。
