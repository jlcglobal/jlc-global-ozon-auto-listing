# 质检规则

## 用途

定义商品处理完成后的质量检查范围和报告结构。

## 图片评分

图片评分规则统一保存在 `rules/image_qc_rules.json`：

- `product_consistency`: 30分，检查商品身份、颜色、结构和配件。
- `conversion_logic`: 25分，检查销售目的、商品定位和购买阻力。
- `style_match`: 20分，检查选定风格、品类表现和风格冲突。
- `visual_quality`: 15分，检查构图、清晰度、文字和信息密度。
- `compliance`: 10分，检查虚假参数、虚假认证和夸大宣传。

总分由 `scripts/image_qc.py` 根据各检查项扣分自动计算，不允许直接填写总分。

## 评分决策

- 90分及以上：`pass`，推荐进入人工审核，不代表批准上传。
- 75至89分：`revise`，允许人工修改后重新质检。
- 75分以下：`reject`，要求重新规划或重新生成。
- 商品身份改变、结构改变、增加无来源配件、虚假参数、虚假认证、图片不可读或比例错误属于强制否决项，即使总分超过90也必须 `reject`。

## 输出要求

- 全商品资料质检继续写入 `output/qc-report.json`。
- 图片专项评分写入 `output/image-qc-report.json`。
- Codex视觉检查的逐项证据可以写入 `logs/image-qc-assessment.json`，评分引擎只接受有证据的检查项。
- 项目代码不调用任何文字或图片模型API。

## 强制约束

- 每次处理结束必须生成质检报告。
- 失败必须记录原因。
- 未知信息必须保留为 `unknown`。

## 风格一致性检查

每个 `qc-report.json` 必须包含以下检查：

- `selected_style_match`: 图片是否符合 `style-profile.json` 选定风格。
- `electronics_not_overly_home`: 电子产品是否被错误做成温馨家居风。
- `outdoor_has_outdoor_scene`: 户外产品是否有真实户外场景。
- `kitchen_has_kitchen_home_feel`: 厨房用品是否具备厨房或餐厨生活感。
- `style_product_conflict`: 风格是否与商品类型、使用场景或购买动机冲突。

非当前品类的专项检查写为 `not_applicable`，不得省略。风格不匹配时至少为 `review_required`；明显冲突时必须为 `fail`。
