# russian-copywriter

## 触发条件

- 用户要求生成俄文标题、卖点、关键词或描述。
- `products/<product_id>/product.json` 已存在，并完成基础事实分析。

## 输入文件

- `products/<product_id>/product.json`
- `rules/russian-copy-rules.md`
- `rules/ozon-rules.md`
- `templates/product.schema.json`

## 输出文件

- `products/<product_id>/product.json`
- `products/<product_id>/output/title-ru.txt`
- `products/<product_id>/output/description-ru.txt`
- `products/<product_id>/output/keywords-ru.txt`

## 禁止行为

- 不得虚构材质、尺寸、重量、承重、认证、品牌、功能和配件。
- 不得把未知字段翻译成确定参数。
- 不得使用未在 `facts` 或人工确认信息中出现的品牌词。
- 不得修改原始输入和原始图片。

## 验收标准

- 俄文标题、卖点、关键词和描述基于 `product.json.facts`。
- 无法确认的信息不进入确定性文案，或明确标记为 `unknown`。
- Ozon 文案草稿写入 `product.json.platform.ozon`。
- 文案文件已输出到 `output/`。
- 处理记录写入 `product.json.processing.steps`。

## 失败处理

- 如果缺少必要事实，写入 `unknowns` 和 `risks`。
- 如果俄文文案无法生成，将步骤标记为 `failed`。
- 失败原因必须写入 `processing.steps[].failure_reason`。
- 允许从本 Skill 单独重试。

## 允许修改 product.json 的字段

- `platform.ozon.title_ru`
- `platform.ozon.bullets_ru`
- `platform.ozon.description_ru`
- `platform.ozon.keywords_ru`
- `platform.ozon.attributes` 中与文案直接相关的草稿字段
- `unknowns`
- `risks`
- `processing`

## 不允许修改 product.json 的字段

- `facts`
- `source`
- `profit`
- `qc`
