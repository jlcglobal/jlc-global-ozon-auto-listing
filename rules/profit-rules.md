# 利润规则

## 用途

定义商品利润、成本和价格判断规则。实际机器配置位于 `pricing-engine/pricing_rules.json`。

## 当前规则

- 采购价：优先SKU采购价；缺失时使用1688已采集价格区间中的最高值并标记 `price_range_conservative`。
- 物流：只读取 `pricing-engine/shipping_rules.xlsx` 的 `RETS` 工作表。
- 线路：在重量、货值和尺寸均符合时选择人民币运费最低的RETS线路。
- 汇率：读取 `RETS!P2`。
- 类目佣金：已配置类目使用真实规则；未知类目默认18%，限制在12%-20%。
- 其他比例费用：物流佣金2%、收单费2%、提现费1.2%。
- 固定费用：打包费2元。
- 默认利润加成：50%。
- 低于最低利润或最低利润率时输出 `REJECT`；物流占比过高输出 `WARNING`。

## 输出要求

- 成本事实和估算写入 `output/cost-analysis.json`。
- SKU售价写入 `output/pricing-result.json`。
- 利润和建议写入 `output/profit-analysis.json`。
- Ozon草稿引用定价结果，但定价通过不代表其他上传条件已通过。

## 当前限制

- 估算重量或尺寸必须保留 `source=estimated` 和置信度，不得写回 `source.json`。
- 没有采购价或没有可用RETS线路时必须 `REJECT`。
- Pricing Engine不调用OpenAI API、第三方AI API或Ozon Seller API。
