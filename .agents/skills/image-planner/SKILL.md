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
- 已完成的 `output/ozon-ecommerce-design.json` 和 `output/copy-ru.json`
- 可选的 `output/visual-reference-analysis.json`，只作为 Ozon 参考图的相机实拍感、
  光线、背景、镜头距离和构图节奏提示；不得作为商品事实来源。

## 输出文件

- `products/<product_id>/output/image-plan.json`

## 禁止行为

- 不得生成最终图片。
- 不得改变商品结构、颜色、SKU 差异和配件数量。
- 不得用假商品图替代真实商品图。
- 不得覆盖原始图片。
- 不得从 output、test-data、其他商品或归档商品读取 product_reference。
- 不得在图片规划阶段重新分析商品或使用本地降级方案。
- 不得使用旧风格档案、类目默认值或排版器覆盖
  `ozon-ecommerce-design.json` 中的逐图艺术指导。
- 不得把 Ozon 类目当作画面事实。类目只决定上传字段，不授权图片里出现米、
  坚果、谷物、零食、饲料、宠物食品或其他具体内容物。图片中的内容物、
  配件、使用场景和道具必须来自本商品标题、主图、详情图、SKU 图、结构化属性
  或视觉分析证据；证据不足时优先展示商品本体、闭合/空置状态或中性场景。
- 不得把 `sku_main` 改写成只有空洞口号的普通场景图。SKU 主图必须保持
  电商设计师定义的信息结构：真实商品主体、少量SKU/规格/用途证明和
  商品质感；主图不得照搬商品标题、型号或SKU名做大字海报。主图可见文字
  通常限制为1-2个短证明点加 JLC GLOBAL 水印。
- 不得把文字区做成画面主体。每个图位必须保留电商设计师要求的
  “商品/真实商品照片区域最大、文字模块辅助说明”的关系。
- 不得把 `visual-reference-analysis.json` 里的竞品品牌、店铺名、水印、型号、
  包装、配件、认证、尺寸、重量、材质或功能当作当前商品事实。该文件只能
  影响拍摄质感和镜头语言，不能改变 SKU、属性、标题、简介、标签、图片数量
  或上传 payload。
- 不得输出低对比文字。每条俄文文案都必须带明确的高对比背景处理，
  例如深色/彩色底板配浅色字，或浅色底板配深色字。
- 不得在缺少 `art_direction`、`design_rationale` 或 `overlay_plan` 时套用旧版
  固定模板；允许在当前商品事实、视觉总监销售故事线和真实参考图范围内补齐最小
  缺失字段，继续生成商品专属提示词。

## 验收标准

- `image-plan.json` 符合 `templates/image-plan.schema.json`。
- 每个已选 SKU 规划 1 张独立主图，严格规划 8 张共享详情图；8 个商业目的可以按商品动态落地，不能复用固定美术模板。
- 8 张共享详情图必须形成商品专属购买决策顺序。参数/规格图、步骤教学图、
  SKU/款式对比图、真实使用场景图、近景结构图、购买提醒图都只是可选角色；
  只有当前商品事实和真实参考图支持时才使用。不得强制每个商品都出现成人模特、
  固定第 8 张免责声明、固定生活方式场景或固定购买提醒模板。
- 每张计划图都引用当前 collection manifest 中登记的真实来源图片路径。
- 每张计划图必须原样透传电商设计师确定的 `design_rationale`、
  `art_direction`、`overlay_plan` 和最终 `prompt`。
- `overlay_plan` 必须逐项、按顺序覆盖全部 `russian_text`；图片规划器
  不能更换俄文内容、顺序、事实或图位意图。若坐标、颜色、层级或背景处理
  会造成廉价模板海报感，最终 `prompt` 必须明确把它缩小、减淡或拆成克制的
  Ozon 信息芯片，并继续保证商品/真实商品照片区域最大。版式审美、留白、
  背景选择和信息芯片样式只作为质量优化，不得单独变成硬拦截。
- 不得把旧 `overlay_modules` 名称当作视觉模板透传给生图器。`capacity_badge`、
  `benefit_section`、`icon_chips` 这类旧模块只能被转换为商品专属的紧凑信息说明，
  不能生成固定大标题、徽章栏或三卡卖点区。
- `art_direction` 和最终 `prompt` 必须明确商品/图片区域占主视觉最大面积，
  并明确文字与背景的高对比处理。主图必须优先是高级真实电商摄影：
  可信镜头、景深、材质纹理、软阴影、环境光、反射和干净调色先成立，
  再加克制信息标注。
- 规划结果固定为 N 张 SKU 独立主图 + 正好 8 张共享详情图；N 等于当前已选 SKU 数（1-10）。
- 缺少 `visual-reference-analysis.json` 不得停止流程；存在该文件时只增强
  prompt 中的真实相机拍摄感，不增加人工确认、图片质检或上传步骤。
- 明确记录必须保留和禁止出现的内容。
- 图片风险只写入当前商品 `output/image-plan.json.risks`，不得写入旧商品主档。

## 失败处理

- 如果真实图片不足，优先使用当前商品已登记的 SKU 图、主图和详情图补充参考；
  仍无法识别商品本体或 SKU 身份时，才标记 `failed` 或 `needs_review` 并写明原因。
- 如果 SKU 差异无法判断，写入 `output/image-plan.json.risks`，不得从其他商品或旧输出补图。
- 允许从本 Skill 单独重试。
