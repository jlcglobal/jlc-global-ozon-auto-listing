# CURRENT TASK

更新时间：2026-07-13 07:05（Asia/Shanghai）

## 当前任务

先用`P000011`完成第一件真实可售商品闭环；同时把工作台收敛为按“我的商品和下一步动作”组织的工作方式，为30个真实商品的工作室试跑做准备：

`1688选品 -> 选择SKU -> 选择最终Ozon类目 -> 采集箱 -> 运行任务 -> 生成资料与图片 -> 预览/自动审核 -> 选择店铺上传 -> 逐店异步回查`

## 最新产品化审查

- 功能矩阵53项：38项完整、12项部分、2项静态UI、1项未实现；完整实现率约72%。
- 工作室可用成熟度评估约45%–50%；对外商业化成熟度约25%–30%。
- 当前最大缺口：尚无一件真实可售商品闭环、工作台仍按系统模块和内部状态组织、员工商品隔离未落地、系统通知未落地、全量测试不干净、项目尚无Git基线。
- 全量自动测试：292项，216项通过、7项失败、69项错误。多数错误来自测试依赖已删除的历史商品目录；图片风格选择和旧图片目录校验仍有真实兼容问题。
- 当前体验审查截图和完整结论保存在`logs/product-audit-20260713/`。
- 工作台目标导航确定为：`我的采集箱`、`需要我处理`、`已上架商品`；每张商品卡只保留一个随状态变化的主操作。
- 员工只能看到和修改自己采集的商品；该限制必须由后端`owner_id`强制执行，不能只做前端隐藏。
- 图片改进重点不是要求用户写固定长提示词，而是系统先生成一次商品视觉简报，让每张图回答不同买家问题；用户只需给“更明亮/更科技感”等简单整组或单图意见。

## 当前完成状态

- 当前商品：`P000011`。
- 采集、类目、资料、价格、10张图片、类目属性和最终上传检查均已完成。
- SKU为`40斤装【透明色】`和`20斤装【实色】`；已确认核心变体差异是容量。
- 已使用当前类目的官方`is_aspect=true`容量属性`Объем, мл`（attribute_id `6788`）。
- 按用户允许的近似规则`1斤≈625毫升`转换：
  - 40斤装 -> 约25000毫升，Ozon本地字典值ID `970824500`；
  - 20斤装 -> 约12500毫升，Ozon本地字典值ID `971392619`。
- 两个值均保存`estimated=true`、置信度`0.75`、原始SKU文本和换算说明，不作为精确营销承诺。
- Ozon分组已从错误的`separate_cards`改为`single_card_variants`：一张商品卡、两个容量变体。
- 本地模拟上传`PASS`，生产阻断项为0；两个SKU使用同一型号名称、不同容量字典值。

## 当前上传状态

- 上一次目标店铺：`zhonglian1`。
- 最新重试批次：`B-B1D3F742EE13`。
- 最新失败原因：隔离店铺工作区启动图片公网通道时没有加载真实项目的`ozon-adapter`路径；子进程立即退出，上传器等待60秒后安全失败。
- 图片通道已改为始终从真实项目源码树加载`ozon-adapter`，不再错误查找隔离商品目录中的模块。
- 店铺记录保持`FAILED`，供工作台“只重试这家店”使用；页面现在直接显示中文失败原因。
- 商品总状态保持`FAILED_HARD_BLOCKER / ozon_upload`，`next_action=retry_failed_store`，保留完整失败历史。
- Ozon CREATE 0次、UPDATE 0次、商品写接口0次、库存接口0次。
- `task_id/product_id/payload_hash`仍为unknown，当前没有远端异步任务可回查。

## 最近验证

- 容量识别、官方变体映射、Ozon字典值、每SKU独立容量和无库存字段：通过。
- 本地`ozon-uploader --prepare`：通过，`production_blockers=[]`，`api_writes_performed=false`。
- 模拟请求包含2个items，同一型号名称；容量分别为25000毫升和12500毫升。
- 模拟请求递归检查：无`stock`、`warehouse_id`或`stocks`字段。
- 图片通道隔离工作区、失败原因传递、工作台、容量映射重点回归：59/59通过。
- 真实工作台页面已验证店铺卡片显示“失败原因：图片公网通道启动失败……上传在请求Ozon前安全停止”。
- 工作台已增加正式上架成功提示：仅`UPLOADED/ACTIVE`时显示9秒，同一商品同一批次只显示一次；等待审核不提示。
- 旧通用商品校验器仍要求历史目录`output/images/main|detail`，而当前真实图片位于`output/generated-images/stage3.4`；这是旧校验器路径兼容问题，不是本次容量或上传请求失败。
- 本次全量测试实跑：292项，216项通过、7项失败、69项错误。
- 历史P000011完整运行约42–48分钟，最慢为生图；最近缓存局部重跑约78秒。10分钟目标需要并行生图、结果复用和按hash跳过未变化步骤。

## 当前未完成

- 尚未重新点击`zhonglian1`的失败店铺重试，因此尚未执行真实Ozon CREATE/UPDATE。
- 真实重试时必须重新做店铺级存在/状态检查；若发现pending或状态不明必须停止，禁止重复CREATE。
- 成功提交后还需逐SKU保存`offer_id/task_id/product_id/payload_hash`并异步回查。
- 旧通用商品校验器需兼容当前`generated-images/stage3.4`图片目录。

## 下一步直接动作

1. 先修复旧图片目录校验和剩余全量测试基础问题，保证P000011本地流程无已知阻断。
2. 用户在工作台点击`只重试这家店`后，仅重试`zhonglian1`的明确失败记录；写入前重新核对两个offer状态。
3. 提交后按SKU保存远端任务信息并回查，直到得到可售或明确审核失败结果。
4. 完成第一件可售商品后，实施任务式工作台、`owner_id`数据隔离、系统通知和视觉简报生成。
5. 开始30个真实商品、至少5个类目的工作室试跑；库存字段和库存接口继续保持0。

## 关键限制

- 不提交库存字段，不设置`stock=0`，不调用库存接口。
- pending、UPLOADING、OZON_MODERATION或状态不明确时禁止重传。
- 已成功CREATE不得重复CREATE；已存在商品只能走安全UPDATE。
- 单商品最多10个SKU；单商品或单店失败不能阻塞整个批次。
- 类目由1688采集时最终确认；任务运行时不得重新猜测或替换。
- SKU合并或拆卡严格依赖当前类目的官方`is_aspect`。
- 不虚构材质、认证、承重、功能和配件；估算数据必须保留标记、置信度和来源。

## 本阶段修改文件

- `variant-compatibility-checker/variant_compatibility_checker/service.py`
- `ozon-uploader/ozon_uploader/service.py`
- `ozon-uploader/ozon_uploader/image_channels.py`
- `scripts/multi_store_upload.py`
- `collector/local-ingest/static/workbench.js`
- `collector/local-ingest/static/workbench.css`
- `templates/variant-grouping-result.schema.json`
- `tests/test_capacity_variant_mapping.py`
- `tests/test_image_channel_isolated_workspace.py`
- `tests/test_multi_store_upload.py`
- `tests/test_visual_first_image_flow.py`
- `.agents/skills/full-product-pipeline/SKILL.md`
- `products/P000011/output/variant-decision.json`
- `products/P000011/output/variant-grouping-result.json`
- `products/P000011/output/platform-grouping-result.json`
- `products/P000011/output/ozon-upload-payload.json`
- `products/P000011/output/store-publications.json`
- `PROJECT_HANDOFF.md`
- `CURRENT_TASK.md`
- `logs/product-audit-20260713/audit-notes.md`

## Git状态

项目根目录当前没有`.git`；没有远程仓库，也没有上传GitHub。
