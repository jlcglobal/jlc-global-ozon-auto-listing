# Ozon 当前生产合同

本规则只描述当前 AI Factory 正式流水线。历史离线商品表格、离线类目猜测、
逐属性人工补充和旧人工审核状态不再是运行合同。

## 唯一输入与产物

- 正式商品输入只来自当前工作台本次采集的 `products/<product_id>/input/source.json`
  和同一 `collection_id` 的 `input/source-manifest.json`。
- 用户已选择的 SKU、最终 `category_id + type_id` 和目标店铺是运行前事实。
- 商品事实合并结果写入 `output/product-analysis.json` 及
  `output/merged-product-facts.json`。
- 实时 Ozon 类目属性、字典和值必须进入
  `output/attribute-fill-input.json`。
- `$ozon-ecommerce-designer` 是唯一商品语义决策层，输出
  `output/ozon-ecommerce-design.json`，其中包含俄文SEO资料、30个俄文标签、
  属性语义决策、SKU名称、图片销售方案和逐图提示词。
- 最终 Ozon 属性和请求体只能由确定性字段编译器从规范对象生成：
  `output/ozon-attributes-final.json`、`output/ozon-upload-config.json`、
  `output/ozon-upload-payload.json`。

## 属性填写

- 用户锁定最终类目后，不再让 AI 猜要填哪些字段；程序必须把该类目的
  实时属性清单和完整 `allowed_values` 交给电商设计师。
- 字典字段必须选择当前实时字典中的 `value + dictionary_value_id`。
- 近义匹配只允许解决表达差异，不允许跨物理维度误配：
  容量、重量、承重、数量、尺寸和包装数据不能互相替代。
- 品牌、认证、承重、功率、安全等级、海关编码等缺乏依据的高风险字段
  不得虚构；缺失不得阻塞其他字段。

## 字段编译

- AI 不直接生成 `/v3/product/import` 请求体。
- 整数类型输出真正 JSON 整数；重量克、包装毫米字段小数统一向上取整。
- Decimal 统一为纯数字；Boolean 统一为平台可接受布尔值。
- 远端可见字符串必须去除中文取证说明、控制字符、本地路径和无关证据。
- 标题、描述、标签、offer_id 和属性字符串按各自字段合同自动压缩；
  不新增人工确认。

## 图片与上传

- 图片规则固定为 N 张 SKU 主图 + 正好 8 张共享详情图，N 等于已选 SKU 数。
- 上传前只做技术硬检查：缺图、损坏、尺寸、重复、空白框、水印、SKU绑定、
  俄文不可读、商品结构/数量明显变化。
- 临时图片 HTTPS 通道失败属于传输层问题：自动重建通道并只重试失败店铺，
  不重新分析商品、不重生图、不重复成功店铺写入。

## Ozon API 边界

- 不提交库存字段，不调用库存、仓库或激活接口。
- 已获得有效 `task_id` 的店铺不得重复 CREATE。
- 本地收到 `task_id` 后即视为已交接给 Ozon；不再自动远端回查商品ID。
- 开发、离线验收和测试中 Ozon 写入、只读回查、库存调用必须全部为 0。
