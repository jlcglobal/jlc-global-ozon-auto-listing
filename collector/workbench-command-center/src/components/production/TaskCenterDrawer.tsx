import { useEffect, useState } from "react";
import { AlertTriangle, Boxes, CheckCircle2, Clock3, ExternalLink, Link2, PackageSearch, RefreshCw, SearchCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { productStepLabel } from "@/lib/workbenchFormat";
import type { BatchCard, OzonReferenceTask, OzonReferenceTasksResponse, ProductCard, SearchVisibilityPlan, TrafficPerformancePlan } from "@/types/workbench";

type TaskTone = "running" | "danger" | "ok" | "idle" | "inbox";

function productTone(product: ProductCard): TaskTone {
  const raw = String(product.raw_status || "").toUpperCase();
  const bucket = String(product.workflow_bucket || "");
  if (raw === "COLLECTED" || bucket.includes("采集箱") || product.current_step === "collect_source") return "inbox";
  if (raw === "OZON_REFERENCE_IMAGES_PARTIAL") return "idle";
  if (["FAILED", "NEEDS_ATTENTION", "STOPPED", "PARTIAL"].includes(raw) || product.risk?.level === "high") return "danger";
  if (["PROCESSING", "RUNNING", "QUEUED", "UPLOADING", "WAITING_FOR_AI_SERVICE"].includes(raw)) return "running";
  if (raw === "OZON_REFERENCE_DRAFT") return "idle";
  if (raw === "OZON_REFERENCE_IMAGES_GENERATED") return "ok";
  if (["HANDED_OFF_TO_OZON", "PENDING_REMOTE", "OZON_MODERATION", "UPLOADED", "ACTIVE", "CREATED", "COMPLETE", "COMPLETED"].includes(raw)) return "ok";
  if ((product.progress || 0) >= 100) return "ok";
  return "idle";
}

function batchTone(batch: BatchCard): TaskTone {
  const raw = String(batch.status || "").toUpperCase();
  if (["FAILED", "COMPLETED_WITH_ERRORS"].includes(raw)) return "danger";
  if (["RUNNING", "QUEUED", "AWAITING_CONFIRMATION", "INCOMPLETE"].includes(raw)) return "running";
  if (["COMPLETE", "COMPLETED"].includes(raw)) return "ok";
  return "idle";
}

function referenceTone(task: OzonReferenceTask): TaskTone {
  const raw = String(task.status || "").toLowerCase();
  if (raw === "failed") return "danger";
  if (["queued", "processing", "waiting_ai_design", "processing_ai_design", "captured", "waiting_adapter"].includes(raw)) return "running";
  if (raw === "completed") return "ok";
  return task.created_product_id ? "ok" : "idle";
}

function badgeVariant(tone: TaskTone): "default" | "warning" | "danger" | "muted" {
  if (tone === "danger") return "danger";
  if (tone === "running") return "warning";
  if (tone === "ok") return "default";
  return "muted";
}

function toneLabel(tone: TaskTone) {
  if (tone === "danger") return "失败/需要处理";
  if (tone === "running") return "进行中";
  if (tone === "ok") return "已完成";
  if (tone === "inbox") return "采集箱";
  return "待处理";
}

function batchLabel(batch: BatchCard) {
  if (batch.display_status) return batch.display_status;
  const raw = String(batch.status || "").toUpperCase();
  return {
    RUNNING: "运行中",
    QUEUED: "排队中",
    COMPLETE: "已完成",
    COMPLETED: "已完成",
    COMPLETED_WITH_ERRORS: "完成但有错误",
    FAILED: "失败",
    INCOMPLETE: "未完成",
    AWAITING_CONFIRMATION: "等待确认",
    AWAITING_MANUAL_UPLOAD: "待确认上传",
  }[raw] || batch.status || "未知";
}

function referenceLabel(task: OzonReferenceTask) {
  if (task.display_status) return task.display_status;
  const raw = String(task.status || "").toLowerCase();
  return {
    queued: "待处理",
    processing: "抓取中",
    waiting_ai_design: "等待 AI 生成商品卡",
    processing_ai_design: "AI 正在生成商品卡",
    captured: "已抓取",
    completed: "已生成商品草稿",
    failed: "抓取失败",
  }[raw] || task.status || "未知";
}

function productReason(product: ProductCard) {
  const item = product.risk?.items?.[0];
  if (item?.message) return item.message;
  if (item?.title) return item.title;
  return product.state || product.workflow_bucket || "暂无异常原因";
}

function matchesTone(tone: TaskTone, filter: string) {
  if (filter === "all") return true;
  if (filter === "running") return tone === "running";
  if (filter === "danger") return tone === "danger";
  if (filter === "ok") return tone === "ok";
  if (filter === "inbox") return tone === "inbox";
  if (filter === "reference") return false;
  return true;
}

function searchLayerLabel(layer?: string) {
  if (layer === "query_without_count") return "无人数候选";
  if (layer === "stable_seller") return "稳定出单：补齐标签";
  if (layer === "title_optimization_candidate") return "低效商品：可改标题";
  if (layer === "tag_only_candidate") return "主题标签补齐";
  if (layer === "insufficient_data") return "数据不足";
  return layer || "未知分层";
}

function searchLayerTone(layer?: string): "default" | "warning" | "danger" | "muted" {
  if (layer === "query_without_count") return "muted";
  if (layer === "stable_seller" || layer === "tag_only_candidate") return "default";
  if (layer === "title_optimization_candidate") return "warning";
  if (layer === "insufficient_data") return "muted";
  return "muted";
}

function metricNumber(value: unknown) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "0";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(number);
}

function searchTagKey(value: unknown) {
  return String(value || "").trim().replace(/^#+/, "").replace(/[\s_#-]+/g, "").toLowerCase();
}

function splitSearchTagText(value: unknown) {
  return String(value || "")
    .split(/[\s,;，；]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function actionActualSubjectTags(action: NonNullable<SearchVisibilityPlan["actions"]>[number]) {
  const check = action.last_upload_status_check;
  const existingTags = (action.existing_subject_tags || []).flatMap(splitSearchTagText);
  const checkedTags = check?.subject_tag_values?.length
    ? check.subject_tag_values.flatMap(splitSearchTagText)
    : check?.has_subject_tags ? splitSearchTagText(check.subject_tag_sample) : [];
  return [
    ...existingTags,
    ...checkedTags,
  ];
}

function actionHasSuggestedTagsOnCard(action: NonNullable<SearchVisibilityPlan["actions"]>[number], tag?: string) {
  const actualKeys = actionActualSubjectTags(action).map(searchTagKey).filter(Boolean);
  if (tag) {
    const key = searchTagKey(tag);
    return Boolean(key && actualKeys.includes(key));
  }
  const suggestedKeys = (action.subject_tags || []).map(searchTagKey).filter(Boolean);
  return Boolean(suggestedKeys.length && suggestedKeys.every((key) => actualKeys.includes(key)));
}

function searchBasis(action: NonNullable<SearchVisibilityPlan["actions"]>[number], periodDays = 7) {
  const topQuery = action.evidence?.top_queries?.[0]?.query
    || action.evidence?.top_yandex_wordstat?.[0]?.query
    || action.evidence?.top_trial_terms?.[0]?.query
    || action.title_terms?.[0]
    || action.subject_tags?.[0]
    || "暂无搜索词";
  const count = action.evidence?.totals?.impressions || action.evidence?.top_queries?.[0]?.metrics?.impressions || 0;
  const queryCount = Number(action.evidence?.totals?.query_count || action.evidence?.top_queries?.length || 0);
  const yandexCount = Number(
    action.evidence?.reference_totals?.yandex_wordstat_searches
    || action.evidence?.top_yandex_wordstat?.[0]?.count
    || action.evidence?.top_yandex_wordstat?.[0]?.metrics?.search_count
    || 0,
  );
  const trialCount = Number(
    action.evidence?.reference_totals?.trial_reference_searches
    || action.evidence?.top_trial_terms?.[0]?.count
    || action.evidence?.top_trial_terms?.[0]?.metrics?.search_count
    || 0,
  );
  const trialQueryCount = Number(action.evidence?.reference_totals?.trial_reference_query_count || action.evidence?.top_trial_terms?.length || 0);
  const missingTitleTerms = (action.title_terms || []).slice(0, 3);
  const titleText = action.title_locked
    ? "标题已锁定，先补标签"
    : missingTitleTerms.length
      ? `建议补充「${missingTitleTerms.join("、")}」`
      : "标题已覆盖当前搜索词";
  if (count > 0) {
    return `依据：过去${periodDays}天有 ${metricNumber(count)} 人搜「${topQuery}」，${titleText}。`;
  }
  if (queryCount > 0) {
    return `依据：Ozon后台返回 ${metricNumber(queryCount)} 个搜索词「${topQuery}」，但没有搜索人数。`;
  }
  if (yandexCount > 0) {
    return `依据：Yandex 有 ${metricNumber(yandexCount)} 次搜索「${topQuery}」，${titleText}。`;
  }
  if (trialCount > 0) {
    return `依据：竞品查询有 ${metricNumber(trialCount)} 人搜「${topQuery}」，${titleText}。`;
  }
  if (trialQueryCount > 0) {
    return `依据：竞品/类目提取 ${metricNumber(trialQueryCount)} 个词「${topQuery}」，${titleText}。`;
  }
  return `依据：只有标题识别，${titleText}。`;
}

function trafficLayerLabel(layer?: string) {
  if (layer === "recommendation_led") return "推荐主导";
  if (layer === "search_led") return "搜索主导";
  if (layer === "ad_spend_risk") return "广告风险";
  if (layer === "click_no_order") return "点击未转化";
  if (layer === "exposure_no_click") return "曝光未点击";
  if (layer === "insufficient_data") return "数据不足";
  return layer || "未知流量";
}

function trafficLayerTone(layer?: string): "default" | "warning" | "danger" | "muted" {
  if (layer === "recommendation_led" || layer === "search_led") return "default";
  if (layer === "ad_spend_risk" || layer === "click_no_order") return "danger";
  if (layer === "exposure_no_click") return "warning";
  return "muted";
}

export function TaskCenterDrawer({
  open,
  onOpenChange,
  products,
  batches,
  ozonReferenceTasks,
  searchVisibilityPlan,
  trafficPerformancePlan,
  loading,
  syncingSearchVisibility,
  error,
  onRefresh,
  onSyncSearchVisibility,
  onOpenProduct,
  onOpenBatch,
  onOpenOzonReferenceLauncher,
  initialTab = "all",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  products?: ProductCard[];
  batches?: BatchCard[];
  ozonReferenceTasks?: OzonReferenceTasksResponse | null;
  searchVisibilityPlan?: SearchVisibilityPlan | null;
  trafficPerformancePlan?: TrafficPerformancePlan | null;
  loading?: boolean;
  syncingSearchVisibility?: boolean;
  error?: string;
  onRefresh: () => Promise<unknown>;
  onSyncSearchVisibility?: () => Promise<unknown>;
  onOpenProduct: (productId: string) => void;
  onOpenBatch: (batch: BatchCard) => void;
  onOpenOzonReferenceLauncher: (taskId: string) => void;
  initialTab?: string;
}) {
  const [activeTab, setActiveTab] = useState(initialTab);
  const productItems = products || [];
  const batchItems = batches || [];
  const referenceItems = ozonReferenceTasks?.items || [];
  const searchPlan = searchVisibilityPlan || null;
  const searchSummary = searchPlan?.summary || {};
  const searchActions = searchPlan?.actions || [];
  const trafficPlan = trafficPerformancePlan || null;
  const trafficSummary = trafficPlan?.summary || {};
  const trafficActions = trafficPlan?.actions || [];
  const runningCount = productItems.filter((item) => productTone(item) === "running").length
    + batchItems.filter((item) => batchTone(item) === "running").length
    + referenceItems.filter((item) => referenceTone(item) === "running").length;
  const dangerCount = productItems.filter((item) => productTone(item) === "danger").length
    + batchItems.filter((item) => batchTone(item) === "danger").length
    + referenceItems.filter((item) => referenceTone(item) === "danger").length;
  const okCount = productItems.filter((item) => productTone(item) === "ok").length
    + batchItems.filter((item) => batchTone(item) === "ok").length
    + referenceItems.filter((item) => referenceTone(item) === "ok").length;
  const inboxCount = productItems.filter((item) => productTone(item) === "inbox").length;
  const searchOptimizationCount = (searchSummary.title_optimization_candidates || 0) + (searchSummary.stable_tag_only || 0) + (searchSummary.tag_only_candidates || 0);
  const trafficInsightCount = (trafficSummary.recommendation_led || 0) + (trafficSummary.search_led || 0) + (trafficSummary.ad_spend_risk || 0) + (trafficSummary.click_no_order || 0) + (trafficSummary.exposure_no_click || 0);

  useEffect(() => {
    if (open) setActiveTab(initialTab || "all");
  }, [initialTab, open]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="task-center-drawer">
        <SheetHeader>
          <SheetTitle>任务进度中心</SheetTitle>
          <SheetDescription>
            汇总采集箱商品、生产批次、Ozon参考任务和商品制作进度。这里不会调用 Ozon 上传或库存接口。
          </SheetDescription>
        </SheetHeader>

        <div className="task-center-summary">
          <div><span>进行中</span><strong>{runningCount}</strong></div>
          <div><span>失败/需处理</span><strong>{dangerCount}</strong></div>
          <div><span>已完成</span><strong>{okCount}</strong></div>
          <div><span>采集箱</span><strong>{inboxCount}</strong></div>
          <div><span>搜索优化</span><strong>{searchOptimizationCount}</strong></div>
          <div><span>流量分析</span><strong>{trafficInsightCount}</strong></div>
        </div>

        <div className="task-center-actions">
          <Button variant="secondary" size="sm" onClick={() => onRefresh()} disabled={loading}>
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            刷新任务
          </Button>
          {error && <span>{error}</span>}
        </div>

        <Tabs value={activeTab} onValueChange={setActiveTab} className="task-center-tabs">
          <TabsList>
            <TabsTrigger value="all">全部</TabsTrigger>
            <TabsTrigger value="running">进行中</TabsTrigger>
            <TabsTrigger value="danger">失败</TabsTrigger>
            <TabsTrigger value="ok">已完成</TabsTrigger>
            <TabsTrigger value="inbox">采集箱</TabsTrigger>
            <TabsTrigger value="reference">Ozon参考</TabsTrigger>
            <TabsTrigger value="search">搜索优化</TabsTrigger>
            <TabsTrigger value="traffic">流量分析</TabsTrigger>
          </TabsList>
          {["all", "running", "danger", "ok", "inbox", "reference", "search", "traffic"].map((tab) => (
            <TabsContent key={tab} value={tab}>
              <ScrollArea className="task-center-scroll">
                <div className="task-center-list">
                  {tab !== "reference" && tab !== "search" && tab !== "traffic" && productItems.filter((product) => matchesTone(productTone(product), tab)).map((product) => {
                    const tone = productTone(product);
                    return (
                      <article key={`product-${product.product_id}`} className={cn("task-center-item", tone)}>
                        <div className="task-center-icon"><PackageSearch className="h-4 w-4" /></div>
                        <div className="task-center-main">
                          <div className="task-center-title">
                            <strong>{product.title_ru || product.title_cn || product.product_id}</strong>
                            <Badge variant={badgeVariant(tone)}>{toneLabel(tone)}</Badge>
                          </div>
                          <small>{product.product_id} · {productStepLabel(product)} · {product.sku_count} SKU</small>
                          {tone === "danger" && <p>{productReason(product)}</p>}
                          <Progress value={product.progress || 0} className="h-1.5" />
                        </div>
                        <Button size="sm" variant="secondary" onClick={() => onOpenProduct(product.product_id)}>
                          进入制作页
                        </Button>
                      </article>
                    );
                  })}

                  {tab !== "inbox" && tab !== "reference" && tab !== "search" && tab !== "traffic" && batchItems.filter((batch) => matchesTone(batchTone(batch), tab)).map((batch) => {
                    const tone = batchTone(batch);
                    return (
                      <article key={`batch-${batch.batch_id}`} className={cn("task-center-item batch", tone)}>
                        <div className="task-center-icon"><Boxes className="h-4 w-4" /></div>
                        <div className="task-center-main">
                          <div className="task-center-title">
                            <strong>{batch.batch_id}</strong>
                            <Badge variant={badgeVariant(tone)}>{batchLabel(batch)}</Badge>
                          </div>
                          <small>{batch.product_count || batch.products?.length || 0} 个商品 · 成功 {batch.success_count || batch.submitted_count || 0} · 失败 {batch.failed_count || 0}</small>
                          {(batch.products || []).slice(0, 4).length > 0 && (
                            <div className="task-center-product-chips">
                              {(batch.products || []).slice(0, 4).map((product) => (
                                <button key={`${batch.batch_id}-${product.product_id}`} type="button" onClick={() => onOpenProduct(product.product_id)}>
                                  {product.product_id}
                                </button>
                              ))}
                            </div>
                          )}
                          <Progress value={batch.progress || 0} className="h-1.5" />
                        </div>
                        <Button size="sm" variant="secondary" onClick={() => onOpenBatch(batch)}>
                          查看批次
                        </Button>
                      </article>
                    );
                  })}

                  {(tab === "all" || tab === "running" || tab === "danger" || tab === "ok" || tab === "reference") && referenceItems.filter((task) => tab === "reference" || matchesTone(referenceTone(task), tab)).map((task) => {
                    const tone = referenceTone(task);
                    return (
                      <article key={`reference-${task.task_id}`} className={cn("task-center-item reference", tone)}>
                        <div className="task-center-icon">{tone === "danger" ? <AlertTriangle className="h-4 w-4" /> : tone === "ok" ? <CheckCircle2 className="h-4 w-4" /> : <Link2 className="h-4 w-4" />}</div>
                        <div className="task-center-main">
                          <div className="task-center-title">
                            <strong>{task.reference_title || task.source_url}</strong>
                            <Badge variant={badgeVariant(tone)}>{referenceLabel(task)}</Badge>
                          </div>
                          <small>{task.task_id} · 图片 {task.captured_image_count || 0} · {task.created_product_id || "未生成商品草稿"}</small>
                          {task.message && <p>{task.message}</p>}
                        </div>
                        {task.created_product_id ? (
                          <Button size="sm" variant="secondary" onClick={() => onOpenProduct(task.created_product_id || "")}>
                            进入草稿
                          </Button>
                        ) : (
                          <Button size="sm" variant="ghost" onClick={() => onOpenOzonReferenceLauncher(task.task_id)}>
                            <ExternalLink className="h-4 w-4" />
                            继续任务
                          </Button>
                        )}
                      </article>
                    );
                  })}

	                  {tab === "search" && (
	                    <div className="search-visibility-panel">
	                      <div className="search-visibility-head">
	                        <div>
	                          <span>Ozon 搜索词</span>
	                          <strong>{searchPlan?.available === false ? "暂无搜索词" : "搜索词方案"}</strong>
	                          <small>
	                            {searchPlan?.period_days || 7} 天窗口 · {searchPlan?.notice || "只读读取，不提交商品或库存"}
	                          </small>
	                        </div>
	                        <div className="search-visibility-actions">
	                          <Button size="sm" variant="secondary" onClick={() => onSyncSearchVisibility?.()} disabled={loading || syncingSearchVisibility || !onSyncSearchVisibility}>
	                            <RefreshCw className={cn("h-4 w-4", syncingSearchVisibility && "animate-spin")} />
	                            读取搜索词
	                          </Button>
	                          <Badge variant={searchPlan?.mode ? "default" : "muted"}>
	                            {searchPlan?.mode ? "未提交Ozon" : "未生成"}
	                          </Badge>
	                        </div>
	                      </div>
                      <div className="search-visibility-summary">
                        <div><span>商品</span><strong>{searchSummary.products || 0}</strong></div>
                        <div><span>主题标签补齐</span><strong>{(searchSummary.stable_tag_only || 0) + (searchSummary.tag_only_candidates || 0)}</strong></div>
                        <div><span>可改标题</span><strong>{searchSummary.title_optimization_candidates || 0}</strong></div>
                        <div><span>数据不足</span><strong>{searchSummary.insufficient_data || 0}</strong></div>
                      </div>
                      {searchActions.length > 0 ? (
                        searchActions.slice(0, 40).map((action) => {
                          const totals = action.evidence?.totals || {};
                          const topQuery = action.evidence?.top_queries?.[0]?.query
                            || action.evidence?.top_yandex_wordstat?.[0]?.query
                            || action.evidence?.top_trial_terms?.[0]?.query
                            || action.title_terms?.[0]
                            || action.subject_tags?.[0]
                            || "暂无搜索词";
                          const searchCount = Number(totals.impressions || 0);
                          const referenceSearchCount = Number(
                            action.evidence?.reference_totals?.yandex_wordstat_searches
                            || action.evidence?.reference_totals?.trial_reference_searches
                            || 0,
                          );
                          const addedToProductCard = actionHasSuggestedTagsOnCard(action);
                          const submittedNotAdded = action.last_upload?.status === "submitted" && !addedToProductCard;
                          return (
                            <article key={`search-${action.product_id}`} className="search-visibility-item">
                              <div className="task-center-icon"><SearchCheck className="h-4 w-4" /></div>
	                              <div className="task-center-main">
	                                <div className="task-center-title">
	                                  <strong>{action.product_id}</strong>
	                                  <Badge variant={addedToProductCard ? "default" : submittedNotAdded ? "warning" : searchLayerTone(action.risk_layer)}>
                                      {addedToProductCard ? "已添加到商品卡" : submittedNotAdded ? "待确认/可重传" : searchLayerLabel(action.risk_layer)}
                                    </Badge>
	                                </div>
	                                <small>
	                                  搜索词 {totals.query_count || 0} · 搜索人数 {searchCount > 0 ? metricNumber(searchCount) : referenceSearchCount > 0 ? metricNumber(referenceSearchCount) : "--"} · 浏览人数 {metricNumber(totals.clicks)} · 订单 {metricNumber(totals.orders)}
	                                  {typeof action.existing_subject_tag_count === "number" && ` · 当前标签 ${action.existing_subject_tag_count}/30`}
	                                  {typeof action.missing_subject_tag_count === "number" && action.missing_subject_tag_count > 0 && ` · 还差 ${action.missing_subject_tag_count}`}
	                                  {addedToProductCard && " · 已添加到商品卡"}
	                                  {submittedNotAdded && " · 已提交但未读到建议标签"}
	                                </small>
                                <p>{searchBasis(action, searchPlan?.period_days || 7)}</p>
                                <div className="search-visibility-terms">
                                  <span>{topQuery}</span>
                                  {(action.subject_tags || []).slice(0, 5).map((tag) => (
                                    <em
                                      key={`${action.product_id}-${tag}`}
                                      className={cn(actionHasSuggestedTagsOnCard(action, tag) && "added")}
                                    >
                                      {tag}
                                    </em>
                                  ))}
                                </div>
                              </div>
                            </article>
                          );
                        })
                      ) : (
	                        <div className="task-center-empty">
	                          <SearchCheck className="h-5 w-5" />
	                          <strong>{searchPlan?.notice || "当前没有可显示的搜索词结果"}</strong>
	                          <span>不会提交商品，不会调用库存接口。</span>
	                        </div>
                      )}
                    </div>
                  )}

                  {tab === "traffic" && (
                    <div className="search-visibility-panel">
                      <div className="search-visibility-head">
                        <div>
                          <span>Ozon 流量表现 dry-run</span>
                          <strong>{trafficPlan?.available === false ? "暂无方案" : "本地诊断方案"}</strong>
                          <small>
                            {trafficPlan?.period_days || 30} 天窗口 · 搜索/推荐/广告分开看 · 只读本地结果
                          </small>
                        </div>
                        <Badge variant={trafficPlan?.mode === "dry_run" ? "default" : "muted"}>
                          {trafficPlan?.mode === "dry_run" ? "未提交Ozon" : "未生成"}
                        </Badge>
                      </div>
                      <div className="search-visibility-summary">
                        <div><span>商品</span><strong>{trafficSummary.products || 0}</strong></div>
                        <div><span>推荐主导</span><strong>{trafficSummary.recommendation_led || 0}</strong></div>
                        <div><span>搜索主导</span><strong>{trafficSummary.search_led || 0}</strong></div>
                        <div><span>广告风险</span><strong>{trafficSummary.ad_spend_risk || 0}</strong></div>
                        <div><span>转化问题</span><strong>{(trafficSummary.click_no_order || 0) + (trafficSummary.exposure_no_click || 0)}</strong></div>
                      </div>
                      {trafficActions.length > 0 ? (
                        trafficActions.slice(0, 40).map((action) => {
                          const totals = action.evidence?.totals || {};
                          const shares = action.evidence?.shares || {};
                          return (
                            <article key={`traffic-${action.product_id}`} className="search-visibility-item">
                              <div className="task-center-icon"><SearchCheck className="h-4 w-4" /></div>
                              <div className="task-center-main">
                                <div className="task-center-title">
                                  <strong>{action.title || action.product_id}</strong>
                                  <Badge variant={trafficLayerTone(action.traffic_layer)}>{trafficLayerLabel(action.traffic_layer)}</Badge>
                                </div>
                                <small>
                                  订单 {totals.orders || 0} · 收入 {totals.revenue_rub || 0} ₽ · 点击 {totals.clicks || 0} · 曝光 {totals.impressions || 0}
                                  {action.title_locked && " · 标题锁定"}
                                </small>
                                <p>{action.reason_cn || "本地规则已生成流量诊断。"}</p>
                                <div className="search-visibility-terms">
                                  <span>搜索 {Math.round((shares.search_orders || 0) * 100)}%</span>
                                  <span>推荐 {Math.round((shares.recommendation_orders || 0) * 100)}%</span>
                                  <span>广告 {Math.round((shares.ads_orders || 0) * 100)}%</span>
                                  {(action.focus || []).slice(0, 4).map((focus) => <em key={`${action.product_id}-${focus}`}>{focus}</em>)}
                                </div>
                              </div>
                            </article>
                          );
                        })
                      ) : (
                        <div className="task-center-empty">
                          <SearchCheck className="h-5 w-5" />
                          <strong>还没有流量分析方案</strong>
                          <span>先导入或生成一次 Ozon 流量表现 dry-run，结果会显示在这里。</span>
                        </div>
                      )}
                    </div>
                  )}

                  {!productItems.length && !batchItems.length && !referenceItems.length && tab !== "search" && tab !== "traffic" && (
                    <div className="task-center-empty">
                      <Clock3 className="h-5 w-5" />
                      <strong>暂无任务</strong>
                      <span>采集商品或添加 Ozon 参考链接后，这里会显示进度。</span>
                    </div>
                  )}
                </div>
              </ScrollArea>
            </TabsContent>
          ))}
        </Tabs>
      </SheetContent>
    </Sheet>
  );
}
