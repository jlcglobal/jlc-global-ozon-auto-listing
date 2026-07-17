# Ozon 市场数据每日更新规范

每天北京时间上午 8 点执行一次，只允许读取 Ozon 官方公开分析数据和写入本地市场数据库。

## 执行顺序

1. 使用 Microsoft Edge 中现有的 Ozon 登录状态打开 `https://data.ozon.ru/app/bestsellers?preset=all`。
2. 下载当天最新的“所有指标”热门商品 Excel 报表。下载属于只读操作。
3. 打开 `https://data.ozon.ru/app/search-queries`，读取近 7 天搜索查询。
4. 对家居、电子产品、卫浴、厨房、户外、汽车配件分别使用俄文类目种子词筛选，保存可见查询、热度、加购数、加购率、买家均价、展示商品数和竞争对手数。
5. 原始搜索词保存到 `runtime/market-intelligence/<YYYY-MM-DD>/ozon-search-queries.json`，结构参考 `market-intelligence/sources/ozon_search_queries_2026-07-13.json`。不得保存 Cookie、令牌、验证码或账号凭证。
6. 运行：

   `/usr/bin/python3 scripts/refresh_ozon_market_daily.py --bestsellers-report <当天Excel绝对路径> --search-queries <当天搜索词JSON绝对路径>`

7. 脚本会继续为最多 500 个待处理商品补齐本地关键词，并尝试为最多 30 个商品同步公开真实主图。主图无法从 Ozon 公开页确认时保留“主图同步中”，不得改用相似图、AI 图或其他商品图片。

8. 检查输出中的商品数、搜索词数、快照日期、关键词补齐数、主图同步数和趋势报告状态。最后用中文汇报成功数量、失败原因和趋势摘要。

## 失败规则

- 登录失效时停止并报告“需要重新登录 Ozon”，不得改用其他网站或生成替代数据。
- 当天报表下载失败或文件日期不是当天时停止，不得把旧报表写成新快照。
- 搜索词读取失败时可以更新商品榜，但必须明确记录搜索词未更新。
- 不调用 Ozon 商品写入接口、库存接口，不创建、修改、上传或发布任何商品。
