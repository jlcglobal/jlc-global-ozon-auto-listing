# CURRENT TASK

更新时间：2026-07-13（Asia/Shanghai）

## 当前目标

在已建立的本地Git基线之上，用一件新采集的真实商品验证完整主流程：

`1688选择SKU和最终Ozon类目 -> 我的采集箱 -> 运行任务 -> 资料/价格/图片生成 -> 手动检查或自动审核 -> 多店上传 -> Ozon异步回查`

旧商品没有在Ozon形成可售商品，已按用户确认全部永久删除，不作为后续测试数据恢复。

## 本阶段已完成

- 已确认项目根目录并建立本地Git仓库，分支为`main`。
- 基线提交：`a8f8b58df5f3e06f7f924e45b348a555d3fc0375`。
- 基线标签：`baseline-before-category-selection`。
- 工作台与成员隔离阶段提交：`a6491560c3033af5b41153b11d89437189774e81`。
- 未配置远程仓库，未上传GitHub。
- 已清空`products/`、`batches/`、`runtime/`、运行日志、图片缓存、任务队列和旧发布包，仅保留目录占位文件。
- 已保留真实店铺配置、店铺密钥本地文件、局域网访问配置、Ozon本地类目树、属性规则、字典值和缓存。
- 工作台主入口已收敛为：`我的采集箱 / 需要我处理 / 已上架商品`。
- 商品卡只显示当前一个主要动作；运行、回答问题、修复、检查上传、查看状态按商品状态自动切换。
- 已实现成员级数据隔离：每个成员只能看、改、运行、删除和上传自己采集的商品。
- 负责人只能管理成员、店铺和全局设置，也不能查看其他成员商品。
- Edge插件提交的成员访问码决定商品归属；成员可选择任意已配置店铺。
- 产品、批次、通知、导出和发布记录均由后端按当前成员过滤。
- 已接入电脑通知入口；关键问题、明确失败和待上传事项只通知商品所属成员。
- 只在商品身份、SKU映射、变体、颜色、结构、配件数量等关键事实不明确时提问；普通可选属性不打断任务。
- 已接入整组图片风格意见和单图意见；简单描述如“更明亮”“更科技感”可进入视觉规划。
- 图片风格意见不能覆盖真实商品结构、颜色、SKU差异和配件数量。
- 图片步骤支持输入哈希复用；风格未变时复用结果，风格变化时从风格选择阶段失效，不重跑无关前序步骤。
- Edge插件源码与发布镜像升级到`0.4.6`。
- 工作台已在真实浏览器验证电脑端1280×720和手机端390×844，页面无控制台错误或警告。

## 当前真实数据状态

- 商品：0。
- 批次：0。
- 运行队列：0。
- 图片运行数据：0。
- 店铺`zhonglian1`：配置保留、已启用、凭证已配置、上次只读验证状态保留。
- Ozon商品写接口调用：0次。
- Ozon库存接口调用：0次。
- 当前没有pending、task_id或远端商品需要回查。

## 最近测试结果

- 本阶段提交范围测试：318项，结果`OK`。
- 实际执行：198项通过。
- 跳过：120项。它们是历史集成测试，依赖已按用户要求删除的旧真实商品目录；没有恢复旧商品数据。
- 当前整个工作区自动发现327项并全部`OK`；多出的9项属于用户另行创建且未纳入本阶段提交的市场情报测试。
- 新增的成员隔离、负责人权限、关键问题、视觉意见、工作台主入口和插件版本测试均已执行并通过。
- Python语法检查通过。
- 浏览器真实页面验证通过，控制台错误/警告0。

## 当前未完成

- 还没有用清理后的新商品完成第一件真实可售商品闭环。
- 120项历史集成测试尚未改造成完全独立的合成夹具；当前默认测试虽为绿色，但测试体系仍需去除对旧运行数据的历史依赖。
- 真实多店CREATE/UPDATE和逐店异步回查仍需在新商品上验收。
- 图片质量仍需用新商品与用户手动流程的结果做一次真实对照，确认视觉效果而不仅是结构检查。
- 对外商业化前仍需30个真实商品、至少5个类目的工作室试跑。

## 下一步直接动作

1. 在1688用最新版Edge插件采集一件新商品，选择不超过10个SKU和最终Ozon类目。
2. 在“我的采集箱”点击运行任务，目标为10分钟内完成资料、定价、属性和图片。
3. 对照用户手动生图质量检查整组图片，只用简单整组/单图意见纠偏。
4. 上传前执行本地预检和只读远端存在检查；状态明确后再由用户主动运行真实上传。
5. 一旦提交，逐店逐SKU保存`offer_id/task_id/product_id/payload_hash`并回查到明确结果。
6. 将120项旧数据依赖测试逐步改造成自带夹具，再开始30商品工作室试跑。

## 关键限制

- 不提交库存字段，不设置`stock=0`，不调用库存接口。
- pending、上传中、审核中或状态不明确时禁止重传。
- 已成功CREATE不得重复CREATE；明确存在的商品只能安全UPDATE。
- 单商品最多10个SKU；单商品或单店失败不能阻塞整个批次。
- 类目必须在1688采集时由用户最终选择，运行中不得重新猜测或替换。
- 必填属性、字典值和变体分组必须读取该类目的本地规则快照及`is_aspect`。
- 不虚构材质、承重、认证、功能和配件；尺寸、重量估算必须带`estimated`和置信度。
- 原始事实、估算、人工修改和Ozon字段分层保存；不得覆盖原始图片和原始输入。

## 本阶段主要修改文件

- `.gitignore`
- `config/operators.example.json`
- `scripts/workbench_operators.py`
- `collector/local-ingest/app.py`
- `collector/local-ingest/static/workbench.html`
- `collector/local-ingest/static/workbench.js`
- `collector/local-ingest/static/workbench.css`
- `scripts/pipeline_runtime.py`
- `scripts/style_selector.py`
- `scripts/image_planner.py`
- `scripts/pipeline_observability.py`
- `scripts/run_batch.py`
- `collector/edge-extension/manifest.json`
- `collector/edge-extension/package.json`
- `collector/edge-extension/popup.html`
- `release/edge-extension/manifest.json`
- `release/edge-extension/package.json`
- `release/edge-extension/popup.html`
- `tests/test_workbench_operators.py`
- `tests/test_workbench_ownership.py`
- 相关工作台、图片、类目和历史数据依赖测试
- `README.md`
- `WORKBENCH_REQUIREMENTS_MATRIX.md`
- `PROJECT_HANDOFF.md`
- `CURRENT_TASK.md`

## Git状态说明

- 当前仓库仅为本地仓库，没有remote。
- 基线提交和标签已完成。
- 本阶段实现已提交为`a6491560c3033af5b41153b11d89437189774e81`，纯净提交副本318项测试通过。
- 工作区还有用户另行创建的选品/市场情报文件，本阶段不修改、不删除、不纳入本阶段提交。
