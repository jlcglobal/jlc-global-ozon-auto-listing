# Ozon 选品与关键词数据模块

本目录保存 AI Factory 的独立市场分析能力。市场数据与 `products/<product_id>/` 下的 1688 原始商品资料严格分开。

当前已完成：

- 六个首批类目配置；
- 热度、飙升、FBS 适配和关键词机会度的可解释权重；
- SQLite 本地数据结构；
- Ozon 商品目录、商品详情、商品搜索词三个只读接口；
- 数据源权限检测；
- 工作台数据源状态接口；
- 凭证和令牌防落盘检查。

运行真实只读检测：

```bash
python3 scripts/probe_ozon_market_sources.py
```

检测只读取 Ozon 数据，不调用商品写入或库存接口。输出和数据库只保存数据源状态、商品总数和 HTTP 状态，不保存 Client-Id、Api-Key、Ozon ID 登录令牌或验证码。

热销榜和飙升榜必须等待 Ozon 免费选品服务登录后取得真实市场商品数据。没有真实数据时，工作台接口返回 `ranking_available=false`，不得生成示例商品冒充排行榜。
