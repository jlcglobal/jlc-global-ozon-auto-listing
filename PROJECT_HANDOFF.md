# PROJECT HANDOFF

更新时间：2026-07-13 07:05（Asia/Shanghai）

## 2026-07-13 产品化审查结论

- 功能矩阵完整实现率为38/53，约72%；若把部分实现折半计入，约84%，但这不等于生产可用率。
- 工作室可用成熟度评估约45%–50%；对外商业化成熟度约25%–30%。主要原因是尚未完成一件真实可售商品、员工商品隔离和系统通知未落地、全量测试不干净、无Git基线、README已过时。
- 当前工作台的主要问题不是功能太少，而是按“系统模块/内部状态”组织，用户必须寻找下一步。目标结构已确定为`我的采集箱 / 需要我处理 / 已上架商品`，每张商品卡只有一个当前主操作。
- 员工数据隔离要求：每个员工只能看、改、运行和上传自己采集的商品；产品、批次、通知和发布记录都必须绑定`owner_id`并由后端过滤。员工可选择任意已配置店铺，但不能修改全局设置。
- 系统通知要求：关键问题只通知商品所属员工的所有工作台电脑；任一设备完成处理后其他设备同步消除通知。
- 图片对照结论：当前P000011主体真实度和统一性尚可，但场景、角度和卖点重复，缺少手动流程里的容量对比、结构证明、图标化信息和多场景差异。根因是视觉简报信息不足，不是需要用户编写固定长提示词。
- 审查截图和详细记录：`logs/product-audit-20260713/`。

## 商业化验收线

- 首先完成P000011第一件真实可售商品闭环。
- 工作室试跑30个真实商品，至少覆盖5个类目；中位处理时间不超过10分钟（不含Ozon审核）。
- 至少95%的商品无需开发者介入即可提交，至少90%的图片无需整套重做。
- 重复CREATE 0、库存接口0、员工越权0；全量测试清零失败和错误；连续7天无数据丢失。
- 预计外部小范围测试需要约6–8周；具备安装、升级、备份、诊断和支持后正式售卖约8–12周。

## 当前真实状态

- 项目根目录：`/Users/apple/Documents/crossborder-ai-factory`
- 工作台：`http://127.0.0.1:8765/workbench`
- 本地服务：运行中，端口8765。
- 当前商品：`P000011`。
- 商品资料：俄文标题、简介、30标签、价格、SKU、类目属性和上传草稿已生成。
- 图片：2张SKU主图 + 8张详情图，共10张，均通过当前图片硬错误检查。
- 最新上传批次：`B-B1D3F742EE13 / COMPLETED_WITH_ERRORS`。
- 店铺`zhonglian1`：明确失败、可只重试该店；远端任务号未知。
- Ozon商品写接口0次，库存接口0次。
- 项目根目录没有`.git`。

## 主流程进度

已完成：

`1688采集 -> SKU选择 -> 最终Ozon类目 -> 采集箱 -> 运行任务 -> 商品分析 -> 定价 -> 俄文资料 -> 生图 -> 图片检查 -> 类目属性补全 -> 上传前检查`

尚未完成：

`安全CREATE/UPDATE -> 保存逐店逐SKU发布记录 -> Ozon异步状态回查`

## 最新容量变体修复

### 原因

上次上传前，系统把`40斤装【透明色】`与`20斤装【实色】`当成无法映射的普通卖家规格，因此错误地要求拆成两张商品卡，并在请求Ozon前阻断。

### 修复结果

- SKU中的`斤装`现在识别为容量/规格差异。
- 当前Ozon类目缓存确认官方容量字段`Объем, мл`，attribute_id `6788`，`is_aspect=true`。
- 使用用户同意的近似规则`1斤≈625毫升`：
  - `40斤装` -> `25000 мл`，dictionary_value_id `970824500`；
  - `20斤装` -> `12500 мл`，dictionary_value_id `971392619`。
- 换算结果保留原始SKU文字、`estimated=true`、置信度0.75和换算说明。
- 平台策略现为`single_card_variants`：一个Ozon商品卡、两个容量变体。
- 上传器会把每个变体写成对应的官方字典值，而不是普通文本。
- 两个SKU仍各自保留独立offer_id、价格和主图。

### 安全验证

- 本地模拟上传成功，`production_blockers=[]`。
- 模拟`/v3/product/import`请求包含2个items，并共用同一型号名称。
- 两个item分别包含容量属性6788的25000毫升和12500毫升字典值。
- 请求中不存在`stock`、`warehouse_id`或`stocks`。
- 本轮未连接Ozon执行写入，CREATE 0次、UPDATE 0次、库存接口0次。

## 工作台和资料现状

- 工作台属性区读取最终属性结果；已填写项优先显示，未知项折叠但可编辑。
- 用户人工修改值以`human_override`保存，不会被AI值覆盖。
- 型号名称使用保存的随机数字；统一计量单位商品数量默认为1。
- 安全停止可终止当前商品和生图子进程，已完成图片不会丢失。
- 每SKU一张主图，详情图按商品生成；当前P000011共10张图片。
- 类目保持采集阶段用户选择的`category_id/type_id`，任务运行中不重新猜测。

## 当前发布记录

- `products/P000011/status.json`：`FAILED_HARD_BLOCKER / ozon_upload`，下一步`retry_failed_store`。
- `products/P000011/output/store-publications.json`：`zhonglian1=FAILED`，api_write_count 0。
- 两个SKU的`task_id/product_id/payload_hash`均为unknown。
- 保持FAILED是为了保留审计历史并让工作台只重试失败店；本次修复不会静默自动上传。
- 真实重试前必须再次核对远端是否存在或pending，不能仅依赖上次本地保存的存在检查。

## 最新图片通道失败与修复

- 最新重试已经通过容量分组和本地上传门禁，并通过只读存在检查确认两个新offer当前未找到。
- 真正失败点是图片公网通道子进程：隔离工作区错误地把自身当成项目根目录，导致`ModuleNotFoundError: ozon_adapter`。
- 上传器等待通道60秒仍未就绪后终止；没有发送Ozon商品写请求。
- 修复后，图片通道固定从真实项目源码树加载`ozon-uploader`和`ozon-adapter`，隔离店铺工作区只承载商品资料。
- 多店铺运行器会从上传日志提取请求前失败原因，写入对应店铺的`last_error`，不再保存`unknown`。
- 工作台店铺卡片直接显示中文“失败原因”，已在真实页面刷新验证。
- 正式上架成功提示也已接入：只有Ozon审核通过进入`UPLOADED/ACTIVE`才提示，同一商品同一批次只提示一次；等待审核阶段不提示。

## 最近测试结果

- 容量映射专项测试：2/2通过。
- 图片通道隔离工作区、失败原因传递、工作台和容量重点回归：59/59通过。
- Python语法检查：通过。
- 本地上传模拟：通过，阻断0，写接口0。
- 请求体库存字段递归检查：通过。
- 旧通用商品校验器仍报当前图片目录与历史`output/images/main|detail`目录不一致；当前真实图片使用`output/generated-images/stage3.4`，后续需修复校验器兼容。
- 2026-07-13全量测试实跑：292项，216项通过、7项失败、69项错误；多数为测试依赖已删除的历史商品目录，另有图片风格选择和旧图片目录校验的真实问题。

## 关键业务限制

- 不提交库存字段，不设置`stock=0`，不调用库存接口。
- pending、UPLOADING、OZON_MODERATION或状态不明确时禁止重传。
- 已成功CREATE不得重复CREATE；已存在商品只能安全UPDATE。
- 单商品最多10个SKU。
- 单商品或单店失败不能阻塞整个批次。
- 每店独立保存`offer_id/task_id/product_id/payload_hash`和状态，只重试失败店。
- 类目必须在1688采集时由用户最终选择，运行时不得替换。
- 属性、字典值和SKU分组必须使用当前类目缓存的官方规则和`is_aspect`。
- 不虚构材质、承重、认证、功能和配件；估算值必须带标记、置信度和来源。

## 下一步动作

1. 在工作台对`zhonglian1`点击“只重试这家店”。
2. 系统写入前重新检查每个offer的远端存在和pending状态。
3. 明确不存在才CREATE，明确存在才安全UPDATE，状态不清楚则停止。
4. 提交后逐SKU保存任务信息并异步回查；库存接口继续保持0。

## 最近修改文件

- 容量识别与分组：`variant-compatibility-checker/variant_compatibility_checker/service.py`
- 上传请求：`ozon-uploader/ozon_uploader/service.py`
- 图片通道：`ozon-uploader/ozon_uploader/image_channels.py`
- 多店铺错误传递：`scripts/multi_store_upload.py`
- 工作台提示：`collector/local-ingest/static/workbench.js`、`collector/local-ingest/static/workbench.css`
- Schema：`templates/variant-grouping-result.schema.json`
- 测试：`tests/test_capacity_variant_mapping.py`、`tests/test_image_channel_isolated_workspace.py`、`tests/test_multi_store_upload.py`、`tests/test_workbench_gap_fill.py`
- 流程规则：`.agents/skills/full-product-pipeline/SKILL.md`
- 商品结果：`products/P000011/output/variant-decision.json`、`variant-grouping-result.json`、`platform-grouping-result.json`、`ozon-upload-payload.json`、`store-publications.json`
- 交接：`CURRENT_TASK.md`、`PROJECT_HANDOFF.md`
