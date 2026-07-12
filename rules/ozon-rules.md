# Ozon 资料包规则

## 用途

定义 Ozon 商品资料包字段、文件和人工确认流程。

## 阶段3.5字段

- 类目只输出俄文建议名称和路径提示；未查询真实Ozon数据时，`description_category_id` 和 `type_id` 必须为 `unknown`。
- 属性使用语义字段保存；未查询真实类目属性时，`ozon_attribute_id` 和 `complex_id` 必须为 `unknown`。
- 未确认的材质、尺寸、重量、承重、认证、品牌和包装数量必须为 `unknown`。
- 1688采购价只保存为 `purchase_price_cny`，不得作为Ozon的RUB售价。
- SKU必须完整保留真实ID、中文名称、规格、采购价来源、图片关联、库存状态和原始数据。
- SKU没有专属图片时必须保留 `sku_image_missing=true`，不得分配随机图片。

## 阶段3.6离线类目和属性匹配

- 阶段3.6读取 `source.json`、`product-analysis.json`、`product-positioning.json` 和本规则文件。
- 类目判断必须综合商品类型、使用场景、购买动机和已完成的图片事实分析，不得只依赖中文标题。
- 未调用Ozon Seller API时，类目仅是离线语义建议；`category_id` 必须为 `unknown`，置信度不得超过离线配置上限。
- `required_attributes` 是待Ozon真实类目元数据确认的候选字段，不得宣称为当前Ozon线上类目的完整必填属性。
- `ozon_attribute_id`、`complex_id` 和无法从可靠来源确认的属性值必须为 `unknown`。
- 材质、尺寸、重量、承重、认证、品牌、功能和包装数量不得根据品类常识或图片猜测。
- 类目和属性结果分别写入 `output/ozon-category.json` 与 `output/ozon-attributes.json`。
- 阶段3.6只能更新草稿metadata，必须保持 `upload_allowed=false` 和 `preflight.status=failed`。

## 上传门禁

- 阶段3.5和阶段3.6所有 `ozon-draft.json` 必须为 `upload_allowed=false`。
- 类目、属性、CNY售价、图片质检和采集授权任一未完成时，`preflight.status=failed`。
- 内容生成完成不等于允许上传。

## 当前限制

- 允许使用 Ozon Seller API。
- 当前阶段只生成 Ozon 草稿结构和上传前校验，不调用 Ozon Seller API。
- 用户点击“运行任务”视为对当前采集箱快照的一次整批处理授权，写入 `status.json.task_authorized` 和 `batch_id`；不再使用逐商品人工审核状态。
- 不操作 Ozon 后台。
- 不自动发布。
- 不接入 OpenAI API、OpenAI 图片 API 或任何需要 `OPENAI_API_KEY` 的模型接口。

## 阶段4.1只读Ozon metadata

- 认证信息只能从 `OZON_CLIENT_ID` 和 `OZON_API_KEY` 环境变量读取，不得写入商品文件、日志或源码。
- 只允许调用类目树、类目属性和属性字典值三个只读端点。
- 禁止调用创建商品、更新商品、上传图片、更新价格、更新库存和发布相关端点。
- 类目树原始标准化快照写入 `output/ozon-category-tree.json`。
- 目标类目的真实属性规则写入 `output/ozon-category-attributes.json`。
- 只有与现有俄文属性名称完全对应的字段才允许自动映射；其他字段必须为 `unknown`。
- Ozon字典属性的值必须与真实 `allowed_values` 匹配，否则进入 `invalid_values`。
- `output/ozon-preflight.json` 必须始终保持 `upload_allowed=false`；阶段4.1不能开启上传。

## 自动批量创建与更新

- 采集箱商品数量不限，但同一个商品只能选择1至10个SKU；服务端在采集和入队两个位置都必须校验。
- 用户点击“运行任务”后，当前采集箱内商品自动处理到底，不使用 `WAITING_REVIEW`、`APPROVED` 或逐阶段确认。
- 最终上传Preflight和 `ozon-draft.json.upload_allowed` 必须同时为 `true`。
- 图片必须来自已通过QC的生成图，并通过临时HTTPS地址交给Ozon抓取。
- 批量任务仍按商品逐个调用创建或更新接口，单个商品失败不得阻断其他商品。
- 不提交库存或仓库字段，不调用库存、仓库、激活接口。
- 商品创建后可以进入Ozon审核，但库存保持未设置，商品不得自动销售。
- 上传到哪个店铺，只在该店铺真实接收创建任务后记录对应店铺名。
- 缺少真实尺寸重量时允许使用合理估算值，但必须标记为 `estimated` 和置信度，不得写入 `source.json` 冒充1688事实。

## 输出要求

- Ozon 字段草稿写入 `products/<product_id>/output/ozon-draft.json`。
- 类目和属性语义草稿写入 `products/<product_id>/output/attributes.json`。
- 上传状态、Ozon `product_id`、`offer_id` 和失败原因写入 `products/<product_id>/status.json`。
- 第一阶段不生成 `ozon.xlsx`。
