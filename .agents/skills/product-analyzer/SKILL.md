# product-analyzer

## 触发条件

- 用户要求整理 1688 商品原始资料。
- 用户要求分析产品事实、未知信息、风险或是否值得上架。
- `products/<product_id>/input/source.json` 或原始图片已放入商品目录。

## 输入文件

- `products/<product_id>/input/source.json`
- `products/<product_id>/input/raw-images/`
- `products/<product_id>/input/sku-images/`
- `rules/product-analysis.md`
- `rules/selection-rules.md`
- `rules/sku-rules.md`
- `rules/profit-rules.md`
- `templates/product.schema.json`

## 输出文件

- `products/<product_id>/product.json`

## 禁止行为

- 不得虚构材质、尺寸、重量、承重、认证、品牌、功能和配件。
- 不得覆盖 `input/` 内任何文件。
- 不得把 AI 推测写成原始事实。
- 不得输出最终上架结论为已发布或可自动发布。

## 验收标准

- `product.json` 符合 `templates/product.schema.json`。
- 原始事实写入 `facts`。
- AI 推测写入 `inferences`，并带置信度和依据。
- 未知数据写入 `unknowns`。
- 风险写入 `risks`。
- 是否值得上架写入 `selection`。
- 失败步骤写入 `processing.steps`，并说明是否可重试。

## 失败处理

- 如果原始资料不足，写入 `unknowns` 和 `risks`。
- 如果无法完成结构化分析，将当前步骤标记为 `failed`。
- 所有失败必须记录 `failure_reason`。
- 允许从本 Skill 单独重试。

## 允许修改 product.json 的字段

- `source`
- `facts`
- `inferences`
- `unknowns`
- `risks`
- `selection`
- `profit`
- `processing`

## 不允许修改 product.json 的字段

- `platform.ozon` 中已由 `russian-copywriter` 或 `ozon-packager` 生成的字段，除非只是补充事实风险引用。
- `qc`，除非记录本步骤失败导致的待质检风险。
