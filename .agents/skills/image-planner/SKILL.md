# image-planner

## 触发条件

- 用户要求生成图片规划。
- `products/<product_id>/product.json` 已存在。
- 商品目录中已有真实商品图或 SKU 图。

## 输入文件

- `products/<product_id>/product.json`
- `products/<product_id>/input/raw-images/`
- `products/<product_id>/input/sku-images/`
- `rules/image-rules.md`
- `templates/image-plan.schema.json`

## 输出文件

- `products/<product_id>/image-plan.json`
- `products/<product_id>/product.json`

## 禁止行为

- 不得生成最终图片。
- 不得改变商品结构、颜色、SKU 差异和配件数量。
- 不得用假商品图替代真实商品图。
- 不得覆盖原始图片。

## 验收标准

- `image-plan.json` 符合 `templates/image-plan.schema.json`。
- 规划 1 张主图和 4 张详情图，除非规则或素材不足导致必须标记风险。
- 每张计划图都引用真实来源图片 ID。
- 明确记录必须保留和禁止出现的内容。
- 图片风险写入 `image-plan.json.risks` 和 `product.json.risks`。

## 失败处理

- 如果真实图片不足，标记 `failed` 或 `needs_review`，并写明原因。
- 如果 SKU 差异无法判断，写入 `unknowns` 和图片风险。
- 允许从本 Skill 单独重试。

## 允许修改 product.json 的字段

- `unknowns`
- `risks`
- `processing`

## 不允许修改 product.json 的字段

- `facts.images`
- `facts.skus` 中非图片引用字段
- `platform.ozon`
- `profit`
- `qc`
