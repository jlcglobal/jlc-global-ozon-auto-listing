# 是否值得上架判断规则

## 用途

定义商品是否进入后续上架资料制作流程的判断框架。

## 待填写规则项

- 必须满足的基础条件：
- 一票否决项：
- 高风险但可人工复核项：
- 平台合规要求：
- 图片质量最低要求：
- SKU 清晰度要求：
- 信息完整度要求：
- 竞争或差异化判断项：

## 输出要求

- 结论写入 `product.json.selection.decision`。
- 理由写入 `product.json.selection.reasons`。
- 阻断项写入 `product.json.selection.blockers`。

## 可选结论

- `pass`
- `review_required`
- `reject`
- `unknown`
