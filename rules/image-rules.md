# 图片规则

## 用途

定义主图、详情图、SKU 图的规划、编辑、生成和质检要求。

## 阶段3固定流程

1. Product Analyzer 读取 `source.json` 和真实商品图，生成 `product-analysis.json`。
2. Style Selector 读取 `source.json`、`product-analysis.json` 和真实商品图，生成 `style-profile.json`。
3. Image Planner 必须先读取 `style-profile.json`，再从 `image_structure_rules.json` 选择固定图片结构。
4. Image Generator 只能使用 `image_generator_contract.py` 输出的提示包，并通过当前 Codex 会话内置生图能力编辑真实商品图。
5. Image QC 必须检查风格匹配、商品真实性、俄文、比例、销售逻辑和未确认声明，并输出加权评分。

## 风格约束

- 风格模板定义在 `rules/style_profiles.json`。
- 风格选择规则定义在 `rules/style_selector_rules.json`。
- 图片结构定义在 `rules/image_structure_rules.json`。
- `image-plan.json.style_family` 必须与 `style-profile.json.style_family` 完全一致。
- `image-plan.json.image_set_structure` 必须与选定风格的结构完全一致。
- 生成器不得自行切换风格；任何偏离必须进入 `review_required`。
- 所有图片固定为 3:4。
- 主图、详情图和免责声明图都必须基于真实商品图。
- 规格图遇到尺寸、重量或承重未知时必须阻止生成。
- 耐用、防水、阻燃、认证、适配车型等证明图必须有可靠来源，否则阻止生成。

## 手动上架质量基准

- `references/manual-ozon-flow-2026-07-12/`中的流程截图是电商表达质量基准，只用于判断图片是否像完整的Ozon销售套图，不得把截图里的其他商品、参数或文案复制到当前商品。
- 一套合格图片必须同时完成商品展示和购买解释：SKU主图突出当前规格；共享详情图覆盖卖点、结构、真实使用场景、规格对比与购买前说明。
- 禁止把白底抠图、孤立产品照、只换背景的重复海报或带一行标题的技术预览当成最终电商图。
- 最终图片必须有准确、可读的俄文信息层级；主图用一条短销售信息，详情图可用标题加利益点。俄文由确定性文字叠加完成，不依赖生图模型拼写。
- 商品已有人工确认的套图逻辑时，可在`input/operator-guidance.json -> image_detail_roles`中为当前商品指定正好8个详情图商业角色。该配置只能决定图位、场景、俄文和来源图，不能改写商品事实、SKU尺寸或配件。
- SKU对比、尺寸、结构和包装内容图必须使用真实原图确定性合成，禁止AI重绘产品；生活场景图可以基于当前SKU真实原图编辑，但不得改变结构、透明度、颜色、比例和数量。

## 图片计划字段

每张图片至少包含：

- `image_type`
- `buyer_question`
- `selling_goal`
- `scene`
- `russian_text`
- `visual_direction`
- `reference_product_images`

## 强制约束

- 优先基于真实商品图片编辑。
- 不得改变商品结构、颜色、SKU 差异和配件数量。
- 不得使用假商品生成最终结果。
- 不得忽略 `style-profile.json` 自由发挥。
- 不得用风格模板补全缺失商品事实。
- 不得把生活方式场景写成商品参数证据。

## 输出要求

- 图片规划写入 `image-plan.json`。
- 风格档案写入 `products/<product_id>/output/style-profile.json`。
- 主图输出到 `products/<product_id>/output/generated-images/main/`。
- 详情图输出到 `products/<product_id>/output/generated-images/detail/`。
- 免责声明图输出到 `products/<product_id>/output/generated-images/disclaimer/`。
- 全商品风险和风格一致性检查写入 `output/qc-report.json`。
- 图片专项评分写入 `output/image-qc-report.json`。
