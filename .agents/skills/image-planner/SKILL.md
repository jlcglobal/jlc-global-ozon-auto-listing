---
name: image-planner
description: Plan source-grounded Ozon image roles and prompts.
---

# image-planner

## 触发条件

- 用户要求生成图片规划。
- 当前正式商品已通过 `source-manifest.json` 的 product_id + collection_id 门禁。
- 商品目录中已有本次工作台采集并登记的真实商品图或 SKU 图。

## 输入文件

- `products/<product_id>/input/source.json`
- `products/<product_id>/input/source-manifest.json`
- `products/<product_id>/input/main-images/`
- `products/<product_id>/input/sku-images/`
- `products/<product_id>/input/detail-images/`
- `rules/image-rules.md`
- `templates/image-plan.schema.json`
- 已完成的 `output/ozon-ecommerce-design.json`、`output/copy-ru.json` 和 `output/ecommerce-creative-brief.json`

## 输出文件

- `products/<product_id>/output/image-plan.json`

## 禁止行为

- 不得生成最终图片。
- 不得改变商品结构、颜色、SKU 差异和配件数量。
- 不得用假商品图替代真实商品图。
- 不得覆盖原始图片。
- 不得从 output、test-data、其他商品或归档商品读取 product_reference。
- 不得在图片规划阶段重新分析商品或使用本地降级方案。

## 验收标准

- `image-plan.json` 符合 `templates/image-plan.schema.json`。
- 每个已选 SKU 规划 1 张独立主图，严格规划 8 张共享详情图；8 个商业目的可以按商品动态落地，不能复用固定美术模板。
- 每张计划图都引用当前 collection manifest 中登记的真实来源图片路径。
- 规划结果固定为 N 张 SKU 独立主图 + 正好 8 张共享详情图；N 等于当前已选 SKU 数（1-10）。
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
