# crossborder-ai-factory 工作规范

## 共享知识库入口

本项目属于洪辰全局工作体系。继续 JLC GLOBAL、AI Factory、Ozon 自动上架、商品资料、财务/采购成本或内容素材相关任务前，必须先读取：

- `/Users/apple/Documents/洪辰知识库/SOURCE_OF_TRUTH.md`

该知识库只作为跨项目导航与长期记忆入口；涉及本项目实际功能、运行状态、商品数据、API 提交、SKU、Ozon 类目属性和真实业务结果时，仍以本项目文件、实时后台、运行日志和用户本轮确认优先。

本项目用于基于 1688 商品原始资料，辅助生成跨境商品资料包。Codex 在任何商品任务中必须遵守本文件。

## 项目定位

- AI Factory 是 1688 到 Ozon 的本地自动上架生产线。
- 当前正式链路是：1688采集 → 用户选择SKU、最终Ozon类目和目标店铺 → 商品事实合并 → `$ozon-ecommerce-designer` 生成俄文资料、属性语义决策和图片方案 → 确定性字段编译 → 图片生成和技术检查 → 用户点击上传 → 多店Ozon提交。
- 不扩展库存、订单、广告、财务运营分析或传统ERP功能。
- 不保留历史单文件、表格或旧图片目录合同作为运行入口。

## 强制原则

- 交互式Codex对话可以向用户逐项确认；但用户已点击“运行任务”且`task_authorized=true`后的批次是无人值守执行，子任务不得再提问、请求确认或等待回复，必须直接执行批次指定的唯一`next_action`。

- 不得虚构材质、承重、认证、功能和配件。品牌按项目规则默认“无品牌”。商品及包装重量、尺寸优先使用本次工作台采集的SKU测量表和1688结构化属性；缺失时才允许由估算模块生成，并必须保存 `estimated` 与置信度。
- 包装重量必须严格大于商品重量；包装长、宽、高必须分别严格大于商品长、宽、高。运费只使用包装数据计算。
- 原始事实、AI 推测和平台字段必须分开保存。
- 不得覆盖原始图片和原始输入。
- 生图优先基于真实商品图片编辑。
- 不得改变商品结构、颜色、SKU 差异和配件数量。
- 任何未知数据必须标记为 `unknown`。
- 任何失败必须记录原因。
- 支持从失败步骤单独重试。
- 用户点击“运行任务”是当前采集箱快照唯一需要的整批确认；之后不得设置逐商品、逐属性、逐图片或逐阶段人工确认门禁。
- 每次处理结束必须生成质检报告。

## 当前限制

- 允许接入 Ozon Seller API；只有用户主动点击最终上传或明确打开自动上传后才允许提交创建/更新。
- 不提交库存字段，不调用库存、仓库、激活接口。
- 开发、测试和离线验收不得调用 Ozon CREATE/UPDATE、只读回查或库存接口。
- 本地收到 Ozon `task_id` 后即进入本地交接完成，不自动追踪远端商品ID。
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
- AI生成候选图只允许输出到 `output/generated-images/variant-main/` 和 `output/generated-images/detail/`。
- 被用户拒绝、生成中止或技术失败的AI图片只允许进入 `output/rejected-generation/`。
- 已确认有效图片只允许进入 `output/accepted-images/`；当前自动候选流程上传前优先使用已通过技术检查的生成候选图。
- 1688 原始采集数据统一保存为 `input/source.json`。
- Codex 分析保存为 `output/product-analysis.json`。
- 电商设计师完整方案保存为 `output/ozon-ecommerce-design.json`。
- 类目属性填值输入保存为 `output/attribute-fill-input.json`。
- 确定性属性编译结果保存为 `output/ozon-attributes-final.json`。
- 俄文兼容文案保存为 `output/copy-ru.json`，必须由电商设计师方案投影，不得独立生成另一套标题/描述/标签。
- 图片规划文件保存为 `output/image-plan.json`。
- 质检报告保存为 `output/qc-report.json`。
- Ozon 草稿保存为 `output/ozon-draft.json`，最终请求体只能由统一字段编译器生成。
- 商品状态保存为 `status.json`。

## 数据分层

商品数据必须区分以下信息：

- `input/source.json`: 1688 原始来源和真实采集事实，不得写入 AI 推测。
- `output/product-analysis.json`: Codex 分析、AI 推测、未知信息、风险和建议。
- `output/ozon-ecommerce-design.json`: 商品理解、俄文SEO资料、属性语义决策、图片销售方案和逐图提示词。
- `output/attribute-fill-input.json`: 当前类目的实时属性、字典值和商品合并事实。
- `output/copy-ru.json`: 从电商设计师方案投影出的俄文标题、卖点、描述和关键词。
- `output/image-plan.json`: 图片规划、参考原图和禁止改变的商品特征。
- `output/qc-report.json`: 文案、图片、SKU 和真实性检查。
- `output/ozon-draft.json`: Ozon Seller API 待上传草稿，禁止直接复用未编译的AI中间值。
- `status.json`: 状态、步骤、失败原因、重试次数和 Ozon 返回结果。

## 输出要求

所有输出必须可追溯到本次工作台采集资料、用户本批次明确选择/填写内容、实时Ozon属性元数据或AI低风险估算。无法确认的高风险字段不得虚构；普通低风险字段由程序自动转换、压缩或估算后继续流水线。

## 任务确认

用户点击“运行任务”即明确授权当前采集箱批次执行到Ozon创建或更新。批次开始后不再进行逐商品确认。系统不得提交库存字段或调用库存接口，库存由用户以后在Ozon后台自行添加。
