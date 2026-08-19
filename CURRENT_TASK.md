# CURRENT_TASK

更新时间：2026-08-19

## 当前阶段

停止继续删除文件。本阶段改造 SKU 资料编辑、事实优先级、属性编译和 Ozon 请求体生成，目标是用户选好 SKU、最终类目和店铺后，系统可按每个 SKU 自己的事实自动填写并提交，不再依赖单一商品级重量/尺寸/颜色。

## 本轮已完成

- 2026-08-19：新增「Ozon 关键词增长雷达」选品工具（独立于上架流水线）。详见 `PROJECT_HANDOFF.md` 的「当前已完成」第一条；skill 在 `.agents/skills/ozon-keyword-growth-radar/`，后端接口 `POST /api/workbench/market-intelligence/keyword-growth-report` + `GET .../latest`，前端顶部命令栏「关键词周报」按钮，数据源为 `market-intelligence/reports/seerfar-keyword-imports/`。已 git 提交推送。
- 2026-07-20：修复所有商品通用的图片失败续跑逻辑。`needs_review`/失败图位不再因为缺少人工恢复请求而被跳过；失败图位会自动重新进入 `image_generation` 队列，已通过并有有效文件/回执/hash 的图位不会重做。
- 2026-07-20：图片失败重试改为按图位计数，不再用整件商品的 `image_generation` 单次重试把任务打回人工处理；启动前异常不消耗真实生图次数。
- 2026-07-20：P000020 正在真实验证该规则：当前从失败图位 `main-5811835345115` 继续，已通过的 5 张图片保留，Ozon 写接口仍为 0。
- 商品详情面板已增加 SKU 资料表：每个已选 SKU 一行，显示真实图、1688 SKU、颜色、容量/规格、商品重量、商品长宽高、包装重量、包装长宽高、装量和 SKU 动态属性。
- 工作台基础单位已统一显示为：长度 mm、重量 g、容量 ml、装量 件。
- SKU 行编辑会自动保存到 `input/workbench-sku-overrides.json`，不改写 `input/source.json`。
- 用户点击运行任务时会冻结 `output/sku-run-snapshot.json`，后续分析、属性填值、图片文案、定价和上传编译使用本次冻结快照。
- 商品事实合并已按 SKU 独立处理：SKU 级尺寸/重量/容量/颜色优先，商品级数据只能补缺，用户修改优先级最高。
- `output/ozon-attributes-final.json` 已分成 `common_attributes` 和 `attributes_by_sku`，不再把 SKU 属性摊平成 attribute_id 后只保留第一个值。
- Ozon 上传器生成每个 SKU 请求体时只合并 `common_attributes + attributes_by_sku[当前sku_id]`，包装重量和包装尺寸也按当前 SKU 取值。
- 字段编译器已按属性含义和目标单位执行确定性转换：mm/cm、g/kg、ml/L，包装重量/尺寸需要整数时向上取整，且避免重复转换。
- 2026-07-22：全局 Ozon 字段收口已加入最终 payload 层：标签仅保留合规俄文搜索词（最多 30 个，不再凑数）；字典属性只使用当前类目允许值；可自动修复的单位、类型和范围问题自动转换，非必填非法字段自动删除。
- 2026-07-22：通用商品尺寸不会再误填“线长/电源线长度”；只有本次采集资料明确存在电源线或线长事实时才会提交该类属性。字段修复明细会写入 `output/ozon-field-repair-report.json`。
- 已补充多 SKU 最大边界验证：单 SKU、双 SKU、10 SKU 均覆盖；10 SKU 测试验证按 `sku_id` 绑定，不按数组顺序复制属性或 offer_id。
- 已重启真实工作台服务并验证真实接口：商品详情接口返回的每个已选 SKU 都包含非空 `sku_row`，工作台前端缓存版本已更新。
- 已完成一次本地双 SKU 实操验证：第一 SKU 修改颜色、规格、重量和完整长宽高后刷新仍保留；第二 SKU 未被串值；运行快照和最终 payload 均按 SKU 区分。

## 当前唯一流水线

1688 采集 → 选择 SKU/类目/店铺 → 自动生成并可选编辑 SKU 资料表 → 点击运行并冻结 SKU 快照 → 商品事实合并 → 属性填值输入 → `$ozon-ecommerce-designer` → Ozon 属性编译 → 图片计划 → 图片生成 → 技术检查 → 自动建立 24 小时图片公网通道 → 自动提交目标店铺。

## 本轮自动提交收口

- 新建批次默认且强制使用自动提交；历史工作台设置中的“手动上传”不会再为新任务插入第二次点击。
- 图片技术检查通过后，后台自动建立固定 24 小时的临时 HTTPS 图片通道，再提交本批次已选择的店铺。
- 已知 macOS 本机 TLS 探测偶发失败会被记录为本机探测不可用，不再错误终止已运行的图片通道；HTTP 错误、无效通道和图片缺失仍会正常失败并安全重建。
- 本轮只改自动提交与图片通道衔接；没有运行商品、没有提交 Ozon、没有调用库存接口。

## 关键业务限制

- 不调用库存接口。
- 不提交库存字段。
- 开发和测试不得调用 Ozon 创建、更新或只读回查。
- 已收到 `task_id` 的店铺不重复创建。
- 商品资料和图片只读取当前 `product_id + collection_id` 的工作台采集输入。
- 测试素材不能进入正式商品输入。
- 每个 SKU 的尺寸、重量、容量、颜色、包装资料优先使用本次 SKU 采集结果；缺失才允许低风险估算。
- 顶部商品级汇总只用于展示，禁止作为最终 Ozon 上传字段来源。

## 最近测试结果

- 图片失败续跑定向测试：`./.venv/bin/python -m unittest tests.test_pipeline_speed_optimizations tests.test_image_host_recovery`：24 项通过，1 项跳过。
- 图片/批次/上传相关回归：`./.venv/bin/python -m unittest tests.test_image_host_recovery tests.test_pipeline_speed_optimizations tests.test_stage34_image_qc tests.test_stage42_ozon_uploader tests.test_batch_pipeline tests.test_image_workflow_fix tests.test_multi_store_upload`：107 项通过，31 项跳过。
- SKU/属性/payload 定向测试：`./.venv/bin/python -m unittest tests.test_sku_fact_attribute_payload tests.test_workbench.WorkbenchTest.test_sku_override_api_saves_empty_dimensions_and_category_dynamic_fields tests.test_workbench.WorkbenchTest.test_workbench_persists_ten_sku_rows_and_selected_overrides_by_sku_id`：10 项通过。
- 本轮字段修复定向测试：95 项通过，43 项跳过，失败 0，错误 0。
- 全量回归：`./.venv/bin/python -m unittest discover -s tests -p 'test*.py'`：525 项通过，86 项跳过，失败 0，错误 0。

## 本地实操验证

- 工作台服务：`127.0.0.1:8765`，当前进程于 2026-07-18 22:03:47 启动。
- 验证样品：`test-data/manual-output/sku-workbench-validation-20260718/P000880`，已从正式 `products/` 迁出，不污染正式商品编号。
- 验证摘要：`test-data/manual-output/sku-workbench-validation-20260718/summary.json`。
- 结果：2 行 `sku_row` 非空；SKU-A 修改后刷新保留；SKU-B 保持独立；冻结快照 `selected_sku_count=2`；最终 payload 生成 2 个 item，offer_id、颜色、规格、商品重量、包装重量和包装尺寸按 `sku_id` 对应。
- 十 SKU 边界证据：`test-data/manual-output/sku-10-boundary-20260718/summary.json`。
- 十 SKU 结果：10 行 `sku_row`、10 条冻结快照、10 组 `attributes_by_sku`、10 个 payload item；第 1/6/10 个 SKU 修改后保留，其他 SKU 未串值；图片合同为 10 张 SKU 主图 + 8 张共享详情图。

## 本轮 Ozon 调用

- CREATE/UPDATE：0
- 只读回查：0
- 库存接口：0
