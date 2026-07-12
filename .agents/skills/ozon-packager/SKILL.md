# ozon-packager

## 触发条件

- 用户要求生成 Ozon 商品资料包。
- `product.json`、俄文文案、图片和质检报告已准备完成。

## 输入文件

- `products/<product_id>/product.json`
- `products/<product_id>/qc-report.md`
- `products/<product_id>/output/main-images/`
- `products/<product_id>/output/detail-images/`
- `rules/ozon-rules.md`
- `rules/sku-rules.md`
- `templates/product.schema.json`

## 输出文件

- `products/<product_id>/output/ozon.xlsx`
- `products/<product_id>/output/report.md`
- `products/<product_id>/product.json`

## 禁止行为

- 不得连接 Ozon API。
- 不得操作 Ozon 后台。
- 不得自动发布。
- 不得补写未经确认的 Ozon 必填参数。
- 不得覆盖原始输入和原始图片。

## 验收标准

- Ozon 资料包只使用 `product.json`、输出图片和人工确认信息。
- 未知字段必须保留为 `unknown` 或在报告中列为待人工补充。
- `output/report.md` 包含打包内容、缺失字段、风险和人工确认清单。
- 打包步骤写入 `product.json.processing.steps`。

## 失败处理

- 如果 Ozon 必填字段缺失，生成可复核报告，不得伪造字段。
- 如果无法生成 `ozon.xlsx`，记录失败原因和可重试状态。
- 允许从本 Skill 单独重试。

## 允许修改 product.json 的字段

- `platform.ozon`
- `unknowns`
- `risks`
- `processing`

## 不允许修改 product.json 的字段

- `source`
- `facts`
- `inferences`
- `profit`
- `qc`
