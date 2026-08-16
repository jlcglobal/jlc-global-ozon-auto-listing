# JLC GLOBAL｜Ozon 自动化上架

JLC GLOBAL Community 是面向 Codex 的本地 `1688 → Ozon` 商品生产工作台。用户克隆仓库后，用 Codex 打开项目，配置自己的 Ozon 店铺，即可运行商品采集、俄文资料生成、属性编译、图片规划与生成，以及受控的 Ozon 商品创建/更新流程。

> 这是源码公开社区版，不是 OSI 定义的开源软件。仅允许学习、研究、个人实验和其他非商业用途，详见 [LICENSE](LICENSE)。商业使用必须事先取得著作权人的书面授权。

## 核心能力

- Edge/Chromium 扩展采集 1688 商品、SKU、属性与图片。
- 本地工作台选择 SKU、Ozon 类目和目标店铺。
- Codex Skills 完成商品理解、俄文电商设计、图片规划和逐图生成。
- 确定性编译 Ozon 属性、重量、尺寸、标签和上传 payload。
- 按 RETS（俄通收）物流线路、计费重和尺寸限制自动计算运费与建议售价。
- 支持用户配置自己的 Ozon Seller API，并显式开启商品创建/更新。
- 不接 OpenAI API，不要求 `OPENAI_API_KEY`；AI 步骤由用户自己的 Codex 环境执行。

## 安全默认值

- 默认 `app_mode=development`，Ozon 写入为 `dry-run`。
- 只有用户把 `config/pipeline-settings.json` 的 `app_mode` 改为 `production`，并在工作台明确运行已选商品时，才允许创建或更新 Ozon 商品。
- 不包含真实店铺密钥、商品、订单、数据库、运行日志、远端任务号或 JLC GLOBAL 内部业务数据。
- 不提交库存字段，也不调用库存、仓库或激活接口。

## 使用要求

- macOS（当前主要验证平台）
- Codex 桌面版或 Codex CLI
- Python 3.11+
- Node.js 20+
- Edge 或 Chromium 浏览器
- 如需上传：你自己的 Ozon Seller API `Client-Id` 和 `Api-Key`

## 快速开始

### 1. 克隆并用 Codex 打开

```bash
git clone https://github.com/jlcglobal/jlc-global-ozon-auto-listing.git
cd jlc-global-ozon-auto-listing
```

在 Codex 中打开这个目录，然后说：

```text
请按 README 初始化 JLC GLOBAL Community，检查依赖、创建本地配置并启动工作台。不要调用任何 Ozon 写接口。
```

Codex 会读取 `.agents/skills/`，按项目合同执行设计、生图和流水线任务。

### 2. 安装依赖

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cd collector/workbench-command-center
npm install
npm run build
cd ../..
```

### 3. 创建本地配置

```bash
cp ozon-adapter/shops.example.json ozon-adapter/shops.json
cp .env.example ozon-adapter/.env.default
```

把你自己的 Ozon 凭证写入 `ozon-adapter/.env.default`。该本地密钥文件已被 `.gitignore` 排除：

```dotenv
OZON_DEFAULT_CLIENT_ID=your_client_id
OZON_DEFAULT_API_KEY=your_api_key
```

### 4. 安装 1688 采集插件

1. 打开 Edge 的 `edge://extensions/` 或 Chrome 的 `chrome://extensions/`。
2. 开启“开发人员模式”。
3. 选择“加载已解压的扩展”。
4. 选择仓库中的 `collector/edge-extension/`。
5. 启动本地工作台后，在 1688 商品页使用扩展采集。

插件不会携带 JLC GLOBAL 的 Cookie 或登录状态；用户需要在自己的浏览器中登录 1688。

#### 1688 详情页无法采集

1. 必须先启动本地工作台，并确认 <http://127.0.0.1:8765/workbench> 可以打开。
2. 在 Edge/Chrome 扩展管理页确认插件已启用；更新代码后点击插件的“重新加载”。
3. 使用自己的账号登录 1688，再打开形如 `https://detail.1688.com/offer/商品编号.html` 的单品详情页；列表页、搜索页和登录验证页不能作为商品详情采集。
4. 等待商品标题、SKU 和图片在页面中加载完成后再点击插件。若页面要求滑块、短信验证或重新登录，先在浏览器中人工完成验证。
5. 仍失败时，同时记录插件显示的错误、浏览器开发者工具 Console 错误及本地工作台终端输出。插件不绕过 1688 登录、验证码、风控或访问权限。

### 5. 启动工作台

```bash
./.venv/bin/python -m uvicorn app:app \
  --app-dir collector/local-ingest \
  --host 127.0.0.1 \
  --port 8765
```

访问 <http://127.0.0.1:8765/workbench>。

### Ozon 类目树与匹配

- 仓库自带一份 Ozon 官方简体中文类目树和类目规则缓存，未配置 API、网络失败或 Ozon 暂时不可用时自动离线兜底。
- 配置并启用自己的 Ozon 店铺后，工作台每次启动都会在后台调用两次 Ozon **只读**类目接口，分别取得俄文和简体中文树，生成本机最新缓存；不会创建/更新商品，也不会调用库存接口。
- 刷新成功后插件优先读取本机实时缓存；刷新失败则继续使用仓库内置缓存，不阻断工作台启动。刷新日志位于 `logs/ozon-category-refresh.log`。
- 新出现且未包含在内置规则包中的类目，在用户选择时才通过只读接口加载该类目的属性和字典值，并缓存在本机。
- 类目名称搜索不到时，可以输入更短的商品核心词、俄文名称、`category_id` 或 `type_id`，也可以从类目树逐级展开。系统只允许选择 Ozon 返回的有效叶子节点，不会靠翻译文本猜测类目 ID。

排查顺序：

1. 确认 `ozon-adapter/shops.json` 中的店铺 ID 与 `config/pipeline-settings.json` 的 `shop_name` 一致。
2. 确认对应的 `ozon-adapter/.env.<店铺ID>` 已填写该店铺的 `Client-Id` 和 `Api-Key`。
3. 重启工作台并查看 `logs/ozon-category-refresh.log`；`prewarmed` 表示实时树已更新，`cache_fresh` 表示本地缓存仍在有效期，`bundled_fallback` 表示正在使用内置缓存。
4. 更新仓库代码后，在浏览器扩展管理页点击“重新加载”，避免旧插件继续持有旧类目缓存。

### 6. 允许真实 Ozon 上传（可选）

先在 `development` 模式完成采集和 dry-run 检查。确认配置、类目、SKU、图片和 payload 都正确后，将 `config/pipeline-settings.json` 中的 `app_mode` 改为 `production`。

真实写入仍需工作台本批次明确授权。请先用测试商品验证；Ozon API 行为与责任由使用者自行承担。

## 主流程

```text
1688 采集
→ 选择 SKU / Ozon 类目 / 店铺
→ 商品事实合并
→ Codex 电商设计
→ 确定性 Ozon 属性编译
→ 图片规划与生成
→ 图片技术检查
→ Ozon 商品创建或更新（仅 production + 本批次授权）
```

## 目录

- `.agents/skills/`：Codex 原生任务技能与事实边界。
- `collector/edge-extension/`：1688 浏览器采集插件。
- `collector/local-ingest/`：本地工作台服务。
- `collector/workbench-command-center/`：工作台前端。
- `scripts/`：主流水线、图片、属性和批次编排。
- `ozon-adapter/`：Ozon 类目与属性适配。
- `ozon-field-completion/`：字段补全和最终资料编译。
- `ozon-uploader/`：受控 Ozon 商品创建/更新。
- `pricing-engine/`：重量、尺寸、成本与价格规则。
- `pricing-engine/shipping_rules.xlsx`：RETS（俄通收）物流基础价表；不是通用物流报价，使用者应按自己的 RETS 合同和最新报价维护。
- `templates/`：产物 JSON Schema。

## 真实性边界

- 原始采集事实、AI 推断和平台字段分开保存。
- 不虚构材质、承重、认证、功能或配件。
- 缺失的高风险事实标记为 `unknown`。
- 包装重量必须大于商品重量；包装各边尺寸必须大于商品对应尺寸。
- 生图优先基于真实商品图片编辑，不改变结构、颜色、SKU 差异和配件数量。

## 验证

```bash
python3 -m py_compile scripts/run_batch.py scripts/image_planner.py scripts/ozon_attribute_compiler.py
python3 -m unittest discover -s tests -p 'test*.py'
```

社区版排除了 JLC GLOBAL 的真实商品素材和内部运行 fixture，因此与私有工作区的完整测试数量不同。

## 许可

PolyForm Noncommercial License 1.0.0。允许非商业使用、修改和分发；商业使用不在许可范围内。

- 著作权与权利人：[COPYRIGHT.md](COPYRIGHT.md)
- 商业授权：[COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md)
- 侵权线索与举报：[INFRINGEMENT-REPORTING.md](INFRINGEMENT-REPORTING.md)
- 安全问题：[SECURITY.md](SECURITY.md)

商业授权及侵权举报邮箱：`18920385676@163.com`

Required Notice: Copyright 2026 洪辰 (Hongchen), JLC GLOBAL.
