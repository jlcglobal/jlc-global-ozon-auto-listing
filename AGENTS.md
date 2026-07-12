# crossborder-ai-factory 工作规范

本项目用于基于 1688 商品原始资料，辅助生成跨境商品资料包。Codex 在任何商品任务中必须遵守本文件。

## 项目定位

- 不是新的桌面软件。
- 不开发浏览器插件。
- 不开发账号系统。
- 不开发复杂 UI。
- 不重新开发现有的“小白”。
- 第一阶段只建立文件结构、规则模板、统一数据结构和 Codex Skills。

## 强制原则

- 交互式Codex对话可以向用户逐项确认；但用户已点击“运行任务”且`task_authorized=true`后的批次是无人值守执行，子任务不得再提问、请求确认或等待回复，必须直接执行批次指定的唯一`next_action`。

- 不得虚构材质、承重、认证、功能和配件。品牌按项目规则默认“无品牌”。商品及包装重量、尺寸缺失时允许由估算模块生成，并必须保存 `estimated` 与置信度。
- 包装重量必须严格大于商品重量；包装长、宽、高必须分别严格大于商品长、宽、高。运费只使用包装数据计算。
- 原始事实、AI 推测和平台字段必须分开保存。
- 不得覆盖原始图片和原始输入。
- 生图优先基于真实商品图片编辑。
- 不得改变商品结构、颜色、SKU 差异和配件数量。
- 任何未知数据必须标记为 `unknown`。
- 任何失败必须记录原因。
- 支持从失败步骤单独重试。
- 用户点击“运行任务”是当前采集箱快照唯一需要的整批确认；之后不得设置逐商品或逐阶段人工确认门禁。
- 每次处理结束必须生成质检报告。

## 当前限制

- 允许接入 Ozon Seller API；只有用户主动点击“运行任务”形成批次后才允许自动上传，不使用 `APPROVED` 状态。
- 第一阶段只建立数据规范、Schema、状态机、校验和示例目录，不调用 Ozon Seller API。
- 不操作 Ozon 后台。
- 第一阶段不开发 1688 爬虫或采集器。
- 不自动发布。
- 采集箱商品数量不限，点击“运行任务”后可批量处理；每个商品最多选择10个SKU。
- 不开发 UI。
- 不使用假商品生成最终结果。
- 不提前开发第二阶段功能。
- 不接入 OpenAI API、OpenAI 图片 API 或任何需要 `OPENAI_API_KEY` 的模型接口。

## 商品目录约定

每个商品独立放在 `products/<product_id>/` 下。

- 原始输入只允许放入 `input/`。
- 主图原图只允许放入 `input/main-images/`。
- SKU 图片只允许放入 `input/sku-images/`。
- 详情原图只允许放入 `input/detail-images/`。
- 处理后的主图只允许输出到 `output/images/main/`。
- 处理后的详情图只允许输出到 `output/images/detail/`。
- 1688 原始采集数据统一保存为 `input/source.json`。
- Codex 分析保存为 `output/product-analysis.json`。
- 俄文文案保存为 `output/copy-ru.json`。
- 图片规划文件保存为 `output/image-plan.json`。
- 质检报告保存为 `output/qc-report.json`。
- Ozon 草稿保存为 `output/ozon-draft.json`。
- 商品状态保存为 `status.json`。

## 数据分层

商品数据必须区分以下信息：

- `input/source.json`: 1688 原始来源和真实采集事实，不得写入 AI 推测。
- `output/product-analysis.json`: Codex 分析、AI 推测、未知信息、风险和建议。
- `output/copy-ru.json`: 俄文标题、卖点、描述和关键词。
- `output/image-plan.json`: 图片规划、参考原图和禁止改变的商品特征。
- `output/qc-report.json`: 文案、图片、SKU 和真实性检查。
- `output/ozon-draft.json`: Ozon Seller API 待上传草稿。
- `status.json`: 状态、步骤、失败原因、重试次数和 Ozon 返回结果。

## 输出要求

所有输出必须可追溯到输入资料或人工补充信息。无法确认的字段不得留空，也不得猜测，必须写为 `unknown`。

## 任务确认

用户点击“运行任务”即明确授权当前采集箱批次执行到Ozon创建或更新。批次开始后不再进行逐商品确认。系统不得提交库存字段或调用库存接口，库存由用户以后在Ozon后台自行添加。
