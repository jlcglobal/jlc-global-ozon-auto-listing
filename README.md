# JLC GLOBAL Community

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
git clone https://github.com/jlcglobal/jlc-global-community.git
cd jlc-global-community
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
cp .env.example .env
```

把你自己的 Ozon 凭证写入 `.env`。这些本地文件已被 `.gitignore` 排除：

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

### 5. 启动工作台

```bash
./.venv/bin/python -m uvicorn app:app \
  --app-dir collector/local-ingest \
  --host 127.0.0.1 \
  --port 8765
```

访问 <http://127.0.0.1:8765/workbench>。

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
