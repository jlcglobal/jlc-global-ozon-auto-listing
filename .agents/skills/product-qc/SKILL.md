---
name: product-qc
description: Validate product facts, copy, images, and upload readiness.
---

# product-qc

## 触发条件

- 用户要求检查商品资料。
- 任一商品处理步骤结束后需要生成质检报告。
- 准备交付 Ozon 资料包前。

## 输入文件

- `products/<product_id>/product.json`
- `products/<product_id>/image-plan.json`
- `products/<product_id>/output/main-images/`
- `products/<product_id>/output/detail-images/`
- `products/<product_id>/output/title-ru.txt`
- `products/<product_id>/output/description-ru.txt`
- `products/<product_id>/output/keywords-ru.txt`
- `rules/qc-rules.md`
- `rules/image-rules.md`
- `rules/russian-copy-rules.md`
- `templates/qc-report.schema.json`

## 输出文件

- `products/<product_id>/qc-report.md`
- `products/<product_id>/product.json`

## 禁止行为

- 不得修改原始输入和原始图片。
- 不得为了通过质检而改写事实。
- 不得自动发布或上传商品。
- 不得忽略未知信息。

## 验收标准

- 检查商品结构、颜色、配件、俄文、二维码、Logo 和参数真实性。
- 检查 SKU 与图片、文案、平台字段是否一致。
- 所有失败和风险写入 `qc-report.md`。
- `product.json.qc` 已更新。
- 报告明确说明是否需要人工确认。

## 失败处理

- 如果无法检查某一项，状态写为 `unknown`，并说明原因。
- 如果发现阻断风险，状态写为 `fail` 或 `review_required`。
- 允许从本 Skill 单独重试。

## 允许修改 product.json 的字段

- `qc`
- `risks`
- `processing`

## 不允许修改 product.json 的字段

- `source`
- `facts`
- `inferences`
- `platform.ozon`
- `profit`
