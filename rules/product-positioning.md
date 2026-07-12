# Product Positioning Agent 规则

## 目的

Product Positioning 位于产品事实分析和风格选择之间，用于回答“这个商品应该怎么卖”，但不能改变或补造商品事实。

```text
source.json
  -> product-analysis.json
  -> product-positioning.json
  -> style-profile.json
  -> image-plan.json
  -> Codex 内置图片生成
```

## 输入

- `input/source.json`
- `output/product-analysis.json`
- 真实主图、SKU图和详情图

## 输出

- `output/product-positioning.json`

## 强制规则

- 商品事实只能来自采集资料、真实图片和已确认参数。
- 客户痛点、购买动机、情绪触发和市场定位可以是有依据的商业推断，但必须标记为 `supported_inference` 并保存来源。
- 材质、尺寸、重量、承重、认证、品牌、功能、包装数量和配件不得由定位层补全。
- `competitive_advantage` 必须有商品结构、SKU差异或真实图片证据；没有证据时写 `unknown`。
- 没有目标市场售价、平台费用、物流成本和竞品数据时，`recommended_price_position` 必须为 `unknown`。
- Product Positioning 不能回写 `source.json` 或覆盖 `product-analysis.json`。

## 下游约束

- Style Selector 必须把 `product-positioning.json` 加入来源并读取目标客户、购买动机、痛点和核心销售角度。
- Image Planner 的 `buyer_analysis` 必须优先使用 Product Positioning，而不是重新自由推断。
- Image Generator 提示包必须包含商品定位，但仍受真实性规则和风格模板约束。
