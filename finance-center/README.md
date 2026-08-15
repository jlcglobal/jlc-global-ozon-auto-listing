# AI Factory 财务中心

财务中心是 AI Factory 工作台内的本地模块。它读取客户自己配置的 Ozon Seller API，只使用订单、Finance 和商品信息查询接口，不调用商品、价格、库存或其他写接口。

## 使用方式

1. 双击项目根目录的 `启动工作室工作台.command`。
2. 打开左侧“财务中心”。默认显示全部店铺、本月至今、人民币。
3. 可切换单店、时间范围和人民币/卢布；商品与订单页同时保留订单编号、Posting、商业编号和 SKU。
4. 负责人可立即同步、导入 Excel/CSV、回滚导入和维护“其他收支”；普通成员可查看和导出。

## 自动更新

- 每天北京时间 15:00 执行只读同步。
- 计划任务比较最后成功同步的准确时间与当天 15:00 截止点；上午人工同步不会跳过下午计划同步。
- 15:00 时电脑关机不会丢任务；下次启动工作台会补跑。
- 每次计划同步重扫最近 90 天，以吸收 Ozon 的延迟结算和状态变化。
- Finance的90天范围按最多28天拆分，低于接口单次最长1个月的限制。
- HTTP 400等不可重试4xx会立即停止当前批次并封锁当天自动重试；剩余店铺不会继续发送相同请求。
- 429、5xx和网络故障至少退避1小时，整批最多自动尝试2次；退避状态保存在SQLite中，工作台重启后仍然有效。
- 保护期内“立即同步”也会在本地被拦截，不会绕过熔断器；后台每5分钟只检查一次本地到期状态。
- 同步前按交易日期获取汇率；无法取得时使用本地保守回退并保留来源。
- Ozon Ads 未接入时明确显示“未配置”，不会当作 0。

## 数据口径

- `profit_margin` 在 Python、SQLite、API 和前端之间统一为 0–1 小数；前端显示时才转成百分比。
- “已完整核算利润”只统计采购、Finance、物流和广告均完整的订单商品行。
- “全店预计利润”只在采购、Finance、物流和期间广告都有可用样本时，按已覆盖成本率外推缺口；任一关键来源没有样本时显示“暂不可计算”。
- 未匹配 Finance/广告只进入待核对清单，不会自动变成成本或收入。
- 广告只有在订单号或 Posting 能唯一精确对应时才允许进入单笔订单；只有活动名、日期、SKU 或商品编号的汇总广告保留为期间级费用，只扣一次。
- 缺采购价保持缺失，不按 0 元成本处理。

## 导入与回滚

- 支持 `.xlsx`、`.csv` 和 `.tsv`。
- Excel 日期序号在导入边界统一转成 ISO 日期。
- 系统先预览字段映射；金额字段和低置信度字段必须人工确认后才能导入。
- 每个导入批次先创建 SQLite 一致性备份，并逐行记录修改；回滚只恢复该批次，不覆盖无关数据。

## 本地 API

这些接口属于 AI Factory 本地工作台，沿用工作台成员认证，不是暴露到公网的 ERP 接口。

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| GET | `/api/workbench/finance/overview` | 成员 | 总览、覆盖率与双利润口径 |
| GET | `/api/workbench/finance/orders` | 成员 | 订单商品行，含订单号、Posting、商业编号 |
| GET | `/api/workbench/finance/products` | 成员 | 商品汇总与主图 |
| GET | `/api/workbench/finance/reconciliation` | 成员 | Finance/广告待核对清单 |
| GET | `/api/workbench/finance/export/{orders|products|reconciliation}` | 成员 | CSV 导出 |
| GET | `/api/workbench/finance/sync-status` | 成员 | 同步账本、熔断/退避状态和只读/写调用计数 |
| POST | `/api/workbench/finance/sync` | 负责人 | 立即执行只读 Ozon 同步 |
| POST | `/api/workbench/finance/imports/preview` | 负责人 | 预览文档与字段自动映射 |
| POST | `/api/workbench/finance/imports/commit` | 负责人 | 确认后导入并创建回滚点 |
| GET | `/api/workbench/finance/imports` | 负责人 | 导入批次记录 |
| POST | `/api/workbench/finance/imports/{batch_id}/rollback` | 负责人 | 定向回滚一个批次 |
| GET/POST/PATCH/DELETE | `/api/workbench/finance/other-entries` | 查看为成员，修改为负责人 | 其他收入/支出 |

查询接口支持 `store_id`、`date_from`、`date_to`；总览另支持 `currency=CNY|RUB`，订单/商品另支持 `q` 和 `limit`。导入预览接收 `file_name`、Base64 文件内容 `content_base64` 和可选 `file_kind`；确认接口还需提交人工核对后的 `mapping`。

## 本地安全边界

- 运行数据库：`runtime/finance/finance.sqlite3`，权限为 `0600`。
- 自动备份：`runtime/finance/backups/`。
- 无效广告归单修复会先生成数据库恢复点，并在“导入与回滚记录”中保留系统审计批次；系统一致性修复只能通过对应恢复点整体回退。
- 店铺凭据沿用 AI Factory 本地店铺配置，权限为 `0600`；API 和导出不会返回密钥。
- 对外售卖时由客户配置自己的 Ozon 凭据，不共用开发者或其他客户的 API。
- 只读端点白名单：FBO Posting、FBS Posting、Finance 交易、商品信息。
