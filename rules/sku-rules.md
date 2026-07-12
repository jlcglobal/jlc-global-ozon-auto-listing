# SKU 规则

## 用途

定义 SKU 结构、颜色、规格、配件和图片对应关系的记录标准。

## 待填写规则项

- SKU 命名规则：
- 颜色字段规则：
- 尺寸字段规则：
- 套装和配件规则：
- SKU 图片要求：
- SKU 差异描述规则：
- 缺失 SKU 信息处理方式：
- SKU 与 Ozon 字段映射规则：

## 强制约束

- 不得改变 SKU 差异。
- 不得增删配件数量。
- 无法确认的 SKU 信息必须标记为 `unknown`。

## 同源商品分组

- 同一 `source_product_id`、规范化 1688 链接、采集 `product_id` 或 SKU 选择任务中的 SKU 必须属于同一个商品组。
- 选择两个及以上 SKU 时，`must_merge=true`，生成一个商品组和多个 `offer_id`。
- 只选择一个 SKU 时，生成一个单 SKU 商品组。
- SKU 名称、价格、图片、颜色、尺寸、容量或套装数量差异不得作为拆分同源商品的理由。
- 本地 Ozon 规则库只负责选择表达 SKU 差异的变体属性，不负责拆分同源商品。
- 无法映射变体属性时必须输出 `variant_mapping_status=RULE_REQUIRED` 并禁止上传，不得静默拆分。

## 输出要求

- SKU 事实写入 `product.json.facts.skus`。
- Ozon SKU 字段草稿写入 `product.json.platform.ozon.skus`。
