# 图片规则

## 当前唯一流程

图片不再由独立风格选择器决定。当前流程固定为：

1. `input/source.json` 和当前商品真实图片进入商品事实合并。
2. `$ozon-ecommerce-designer` 在 `output/ozon-ecommerce-design.json` 中一次性产出俄文资料、卖点、属性语义决策和整套图片销售方案。
3. `scripts/image_planner.py` 只把该方案编译为 `output/image-plan.json`，不得重新分析商品、套用通用模板或从其他商品补图。
4. `scripts/image_generator_contract.py` 为单个图位生成提示包；图位生成后只做技术硬检查。
5. `scripts/image_qc.py` 输出当前图片技术检查报告。

## 输入边界

- 当前商品图片参考只能读取：
  - `input/main-images/`
  - `input/sku-images/`
  - `input/detail-images/`
- `output/generated-images/`、`output/rejected-generation/`、`output/accepted-images/` 永远不能作为新一轮商品参考输入。
- `test-data/` 只用于测试样板，正式生产加载器必须拒绝。
- 不同商品之间禁止互相补图、补规格或复用输出。

## 数量合同

- 已选 SKU 数为 `N`，`N` 必须在 1–10。
- 每个已选 SKU 生成且只生成 1 张独立主图。
- 每个商品生成正好 8 张共享详情图。
- 总图数必须为 `N + 8`。
- 共享详情图只能使用所有已选 SKU 共同成立的卖点；SKU 差异用一张真实原图合成的对比图表达。

## 手动上架质量基准

- `references/manual-ozon-flow-2026-07-12/` 只用于理解电商图片的信息结构、排版层级和整套销售逻辑。
- 不得把参考截图里的商品事实、参数、场景或文案复制到当前商品。
- 一套合格图片必须同时完成商品展示和购买解释：SKU主图突出当前规格；共享详情图覆盖卖点、结构、真实使用场景、规格对比与购买前说明。
- 禁止把白底抠图、孤立产品照、只换背景的重复图或无信息目的技术预览当成最终电商图。
- 图片文字必须是准确、可读、高对比度的俄文。
- SKU主图优先是高级真实电商摄影，不是标题海报：商品质感、材质纹理、光线、阴影、反射、场景和镜头先成立；只允许少量规格/用途/功能证明文字和低调 JLC GLOBAL 水印。
- 详情图可以做信息图，但信息必须由真实商品、尺寸、结构、步骤、SKU差异或使用场景承载，不能变成“背景 + 大字 + 卖点”的贴字模板。

## 生成约束

- 生图优先基于真实商品图片编辑。
- 不得改变商品结构、颜色、SKU 差异和配件数量。
- 不得使用假商品生成最终结果。
- SKU对比、尺寸、结构和包装内容图必须使用真实原图确定性合成，禁止 AI 重绘产品。
- 生活场景图可以基于当前 SKU 真实原图编辑，但不得改变结构、透明度、颜色、比例和数量。
- 规格图只使用商品本体尺寸；包装尺寸不能当商品尺寸。

## 输出

- 图片规划写入 `output/image-plan.json`。
- SKU主图输出到 `output/generated-images/variant-main/`。
- 详情图输出到 `output/generated-images/detail/`。
- 被拒绝或失败图进入 `output/rejected-generation/`。
- 兼容确认图进入 `output/accepted-images/`。
- 图片技术检查写入 `output/image-qc-report.json` 或 `output/qc-report.json`。
