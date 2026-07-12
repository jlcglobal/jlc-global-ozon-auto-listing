# 商品风格判断系统

## 处理顺序

```text
source.json + product-analysis.json + 真实商品图
  -> Style Selector
  -> style-profile.json
  -> Image Planner
  -> image-plan.json
  -> Codex 内置图片生成
  -> image-qc-report.json
```

项目代码只做规则选择、规划和生成前约束，不调用文字或图片模型 API。图片理解和图片生成由当前 Codex 会话完成。

## 两层判断

第一层是确定性规则。`style_selector_rules.json` 对商品类型、类目、中文标题、使用场景、目标用户、购买动机和属性分别加权，得到候选风格分数。

第二层是商品事实理解。Codex 在 `product-analysis.json` 中记录从真实图片和来源文件得到的商品类型、目标用户、使用场景和可追溯卖点。Style Selector 只读取这些已分层信息，不把风格模板反写到 `source.json`。

当最高分低于阈值、两个风格分差过小或商品信息不足时，输出 `needs_review` 或 `needs_input`，不允许生成器自由选择一个风格。

## 风格与结构绑定

每个 `style_family` 在 `image_structure_rules.json` 中只有一套允许结构。Image Planner 必须把结构原样写入 `image-plan.json.image_set_structure`，生成器只能处理其中的 `image_type`。

例如：

- `electronics_clean_tech`: `main -> feature -> usage -> detail -> size -> disclaimer`
- `outdoor_rugged_lifestyle`: `main -> scene -> durability -> usage -> detail -> size -> disclaimer`
- `kitchen_warm_home`: `main -> benefit -> scene -> problem_solution -> detail -> size -> disclaimer`

## 真实性阻断

- 尺寸、重量或承重未知时，`size` 图片进入 `needs_review`，生成器拒绝执行。
- 防水、耐磨、阻燃、安全或耐候没有可靠来源时，`durability` 图片进入 `needs_review`。
- 没有真实商品参考图时，所有最终商品图片都禁止生成。
- 风格模板不能补全材质、参数、认证、品牌、功能、适配范围或配件。

## 生成器约束

`image_generator_contract.py` 在生成前校验：

- `style-profile.json` 与 `image-plan.json` 的风格一致；
- 图片类型属于所选结构；
- 图片槽位没有被真实性规则阻断；
- 存在真实商品参考图；
- 输出比例固定为 3:4。

校验通过后只输出供当前 Codex 会话使用的提示包，不调用外部 API。

## QC

QC 必须检查选定风格、电子产品家居化、户外场景、厨房家居感和风格冲突。图片风格正确不等于商品事实正确；俄文、产品结构、颜色、SKU 差异和未确认声明仍需分别检查。

`image-qc-report.json` 使用100分制：商品一致性30、转化逻辑25、风格匹配20、视觉质量15、合规10。分数达到90仅推荐进入人工审核；任何商品结构改变、虚假参数或虚假认证都会触发强制 `reject`。
