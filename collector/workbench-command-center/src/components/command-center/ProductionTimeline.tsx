import { CheckCircle2, Circle, Clock3, Link2, PackageCheck, SearchCheck, UploadCloud } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import { readableStageName } from "@/lib/workbenchFormat";
import type {
  OzonReferenceTask,
  OzonReferenceTasksResponse,
  ProductCard,
  ProductDetail,
  SearchVisibilityAction,
  SearchVisibilityPlan,
} from "@/types/workbench";

const MAIN_FLOW = [
  { key: "source", label: "采集资料", steps: ["collect_source", "validate_source"] },
  { key: "recognize", label: "识别商品", steps: ["product_analysis", "category_match", "variant_rules", "measurements"] },
  { key: "copy", label: "生成俄文资料", steps: ["product_positioning", "ecommerce_design", "russian_copy"] },
  { key: "images", label: "生成图片", steps: ["image_plan", "image_generation", "image_qc"] },
  { key: "attributes", label: "填写属性", steps: ["field_completion"] },
  { key: "upload", label: "上传Ozon", steps: ["ozon_upload"] },
];

function flowState(detail: ProductDetail | null, item: (typeof MAIN_FLOW)[number]) {
  const completed = new Set(detail?.status?.completed_steps || []);
  const current = String(detail?.status?.current_step || "");
  const status = String(detail?.status?.status || "").toUpperCase();
  const failed = String(detail?.status?.failed_step || "");
  const allDone = item.steps.every((step) => completed.has(step));
  const active = item.steps.includes(current);
  const hasFailure = ["FAILED", "NEEDS_ATTENTION", "PARTIAL"].includes(status) && item.steps.includes(failed || current);
  if (hasFailure) return "failed";
  if (allDone) return "done";
  if (active || ["PROCESSING", "QUEUED", "UPLOADING", "WAITING_FOR_AI_SERVICE"].includes(status)) return active ? "running" : "waiting";
  return "waiting";
}

function money(value?: number) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "未生成";
  return `¥${value.toFixed(2)}`;
}

function rub(value?: number) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "未生成";
  return `₽${Math.round(value)}`;
}

function cmText(value?: { length?: number; width?: number; height?: number } | null) {
  if (!value?.length || !value?.width || !value?.height) return "未生成";
  return `${value.length}×${value.width}×${value.height} cm`;
}

function weightText(value?: number | null) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "未生成";
  return `${Math.round(value)} g`;
}

function metricNumber(value: unknown) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "0";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(number);
}

function ozonTopQuery(action?: SearchVisibilityAction | null) {
  return action?.evidence?.top_queries?.[0]?.query
    || action?.title_terms?.[0]
    || action?.subject_tags?.[0]
    || "暂无搜索词";
}

function ozonSearchCount(action?: SearchVisibilityAction | null) {
  return action?.evidence?.totals?.impressions || action?.evidence?.top_queries?.[0]?.metrics?.impressions || 0;
}

function ozonQueryCount(action?: SearchVisibilityAction | null) {
  return Number(action?.evidence?.totals?.query_count || action?.evidence?.top_queries?.length || 0);
}

function ozonYandexSearchCount(action?: SearchVisibilityAction | null) {
  return action?.evidence?.reference_totals?.yandex_wordstat_searches
    || action?.evidence?.top_yandex_wordstat?.[0]?.count
    || action?.evidence?.top_yandex_wordstat?.[0]?.metrics?.search_count
    || 0;
}

function ozonTrialSearchCount(action?: SearchVisibilityAction | null) {
  return action?.evidence?.reference_totals?.trial_reference_searches
    || action?.evidence?.top_trial_terms?.[0]?.count
    || action?.evidence?.top_trial_terms?.[0]?.metrics?.search_count
    || 0;
}

type ReferenceState = "done" | "running" | "failed" | "waiting";

function referenceStatus(task: OzonReferenceTask) {
  return String(task.status || "").toLowerCase();
}

function referenceState(task: OzonReferenceTask): ReferenceState {
  const raw = referenceStatus(task);
  if (raw === "failed") return "failed";
  if (task.created_product_id || raw === "completed" || raw === "listing_draft_ready") return "done";
  if (task.missing_fields?.length) return "waiting";
  if (["queued", "processing", "waiting_adapter", "waiting_ai_design", "processing_ai_design", "captured"].includes(raw)) return "running";
  return "waiting";
}

function referenceBadgeVariant(state: ReferenceState): "default" | "warning" | "danger" | "muted" {
  if (state === "done") return "default";
  if (state === "failed") return "danger";
  if (state === "running") return "warning";
  return "muted";
}

function referenceLabel(task: OzonReferenceTask) {
  if (task.display_status) return task.display_status;
  const raw = referenceStatus(task);
  return {
    queued: "排队中",
    processing: "采集中",
    waiting_adapter: "等待采集器",
    captured: task.missing_fields?.length ? "已抓取，缺参数" : "已抓取",
    waiting_ai_design: "等待AI生成",
    processing_ai_design: "AI生成商品卡中",
    listing_draft_ready: "商品卡草稿已生成",
    completed: "已完成",
    failed: "失败",
  }[raw] || task.status || "待处理";
}

function referenceProgress(task: OzonReferenceTask) {
  const raw = referenceStatus(task);
  if (raw === "failed") return 100;
  if (task.created_product_id || raw === "completed" || raw === "listing_draft_ready") return 100;
  if (task.missing_fields?.length) return 25;
  if (raw === "captured") return 40;
  if (raw === "queued" || raw === "waiting_adapter" || raw === "processing") return 45;
  if (raw === "waiting_ai_design") return 58;
  if (raw === "processing_ai_design") return 72;
  return 20;
}

function referenceStepState(task: OzonReferenceTask, step: "capture" | "input" | "ai" | "draft"): ReferenceState {
  const raw = referenceStatus(task);
  const hasCapture = Boolean((task.captured_image_count || 0) > 0 || raw !== "queued");
  const inputDone = !task.missing_fields?.length;
  const draftDone = Boolean(task.created_product_id || raw === "completed" || raw === "listing_draft_ready");
  if (raw === "failed") return "failed";
  if (step === "capture") return hasCapture ? "done" : "running";
  if (step === "input") return inputDone ? "done" : "running";
  if (step === "ai") {
    if (draftDone) return "done";
    if (inputDone && ["waiting_ai_design", "processing_ai_design", "captured"].includes(raw)) return "running";
    return "waiting";
  }
  if (draftDone) return "done";
  if (inputDone && raw === "processing_ai_design") return "running";
  return "waiting";
}

function visibleReferenceTasks(tasks?: OzonReferenceTasksResponse | null) {
  return (tasks?.items || [])
    .filter((task) => {
      const raw = referenceStatus(task);
      return !task.created_product_id
        || ["completed", "listing_draft_ready", "failed"].includes(raw);
    })
    .slice(0, 2);
}

function ReferenceProgressFooter({ tasks }: { tasks: OzonReferenceTask[] }) {
  if (!tasks.length) return null;
  return (
    <section className="ozon-reference-footer" aria-label="Ozon参考任务进度">
      <div className="ozon-reference-footer-head">
        <div>
          <span>Ozon参考进度</span>
          <strong>参考商品生成我方商品卡</strong>
        </div>
        <Badge variant={referenceBadgeVariant(referenceState(tasks[0]))}>
          {referenceLabel(tasks[0])}
        </Badge>
      </div>
      <div className="ozon-reference-progress-list">
        {tasks.map((task) => {
          const state = referenceState(task);
          const progress = referenceProgress(task);
          const steps = [
            { key: "capture" as const, label: "采集参考" },
            { key: "input" as const, label: "补参数" },
            { key: "ai" as const, label: "AI生成" },
            { key: "draft" as const, label: "商品草稿" },
          ];
          return (
            <article key={task.task_id} className={cn("ozon-reference-progress-card", state)}>
              <div className="ozon-reference-progress-main">
                <span className="ozon-reference-progress-icon"><Link2 className="h-4 w-4" /></span>
                <div>
                  <strong>{task.reference_title || task.source_url}</strong>
                  <small>
                    {task.task_id} · {referenceLabel(task)}
                    {task.captured_image_count ? ` · 图片 ${task.captured_image_count} 张` : ""}
                    {task.created_product_id ? ` · 草稿 ${task.created_product_id}` : ""}
                  </small>
                  <p>{task.message || "正在处理 Ozon 参考任务。"}</p>
                </div>
              </div>
              <div className="ozon-reference-progress-meter">
                <Progress value={progress} className="h-1.5" />
                <em>{state === "failed" ? "失败" : `${progress}%`}</em>
              </div>
              <div className="ozon-reference-mini-flow">
                {steps.map((step) => {
                  const stepState = referenceStepState(task, step.key);
                  return (
                    <span key={`${task.task_id}-${step.key}`} className={stepState}>
                      {stepState === "done" ? <CheckCircle2 /> : stepState === "running" ? <Clock3 /> : <Circle />}
                      {step.label}
                    </span>
                  );
                })}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

export function ProductionTimeline({
  mode = "local",
  detail,
  card,
  searchAction,
  searchPlan,
  syncingSearchVisibility,
  ozonReferenceTasks,
}: {
  mode?: "local" | "ozon";
  detail?: ProductDetail | null;
  card?: ProductCard;
  searchAction?: SearchVisibilityAction | null;
  searchPlan?: SearchVisibilityPlan | null;
  syncingSearchVisibility?: boolean;
  ozonReferenceTasks?: OzonReferenceTasksResponse | null;
}) {
  const priceItems = detail?.pricing?.sku_pricing || [];
  const firstPrice = priceItems[0] || null;
  const profitRate = firstPrice?.profit_rate_markup ?? firstPrice?.profit_rate;
  const packageCheck = detail?.package_check || null;
  const currentStep = detail?.pipeline_progress?.step || detail?.status?.current_step || card?.pipeline_progress?.step || card?.current_step || "";
  const status = detail?.status?.status || card?.raw_status || "";
  const failure = detail?.error?.message || detail?.production_readiness?.errors?.[0] || detail?.production_readiness?.message || "";
  const referenceTasks = visibleReferenceTasks(ozonReferenceTasks);

  if (mode === "ozon") {
    const hasAction = Boolean(searchAction);
    const hasSearch = Boolean(searchPlan?.available && hasAction);
    const hasAdvice = Boolean(searchAction?.allowed_changes?.length || searchAction?.subject_tags?.length || searchAction?.title_terms?.length);
    const uploaded = searchAction?.last_upload?.status === "submitted";
    const searchCount = Number(ozonSearchCount(searchAction) || 0);
    const yandexSearchCount = Number(ozonYandexSearchCount(searchAction) || 0);
    const trialSearchCount = Number(ozonTrialSearchCount(searchAction) || 0);
    const bestReferenceCount = yandexSearchCount || trialSearchCount;
    const queryCount = ozonQueryCount(searchAction);
    const ozonSteps = [
      { key: "info", label: "读取商品资料", state: hasAction ? "done" : "waiting" },
      { key: "terms", label: "读取搜索词", state: syncingSearchVisibility ? "running" : hasSearch ? "done" : "waiting" },
      { key: "advice", label: "生成建议", state: hasAdvice ? "done" : "waiting" },
      { key: "upload", label: "上传优化", state: uploaded ? "done" : "waiting" },
      { key: "complete", label: "完成", state: uploaded ? "done" : "waiting" },
    ];
    return (
      <Card className="activity-stream glass-panel timeline-panel main-flow-footer">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div>
              <div className="panel-kicker">底部过程</div>
              <CardTitle>搜索词优化过程</CardTitle>
            </div>
            <Badge variant={hasAdvice ? "default" : syncingSearchVisibility ? "warning" : "muted"}>
              {syncingSearchVisibility ? "读取中" : hasAdvice ? "建议已生成" : "待选择"}
            </Badge>
          </div>
        </CardHeader>
        <CardContent>
          <ReferenceProgressFooter tasks={referenceTasks} />
          <div className="main-flow-footer-grid ozon-footer-grid">
            <section className="main-flow-steps ozon-flow-steps">
              {ozonSteps.map((item, index) => (
                <article key={item.key} className={cn("main-flow-step", item.state)}>
                  <span>{item.state === "done" ? <CheckCircle2 /> : item.state === "running" ? <Clock3 /> : <Circle />}</span>
                  <strong>{item.label}</strong>
                  <small>{item.state === "done" ? "已完成" : item.state === "running" ? "运行中" : "等待"}</small>
                  {index < ozonSteps.length - 1 && <i />}
                </article>
              ))}
            </section>
            <section className="price-package-check search-basis-check">
              <div className="check-title">
                <SearchCheck className="h-4 w-4" />
                <strong>搜索词依据</strong>
                <Badge variant={hasSearch ? "default" : "muted"}>{hasSearch ? "已读取" : "未读取"}</Badge>
              </div>
              <div className="check-grid">
                <div><span>搜索人数</span><strong>{searchCount > 0 ? metricNumber(searchCount) : bestReferenceCount > 0 ? metricNumber(bestReferenceCount) : "--"}</strong></div>
                <div><span>搜索词</span><strong>{metricNumber(queryCount)}</strong></div>
                <div><span>浏览人数</span><strong>{metricNumber(searchAction?.evidence?.totals?.clicks)}</strong></div>
                <div><span>订单</span><strong>{metricNumber(searchAction?.evidence?.totals?.orders)}</strong></div>
              </div>
              <p>
                <UploadCloud className="h-3.5 w-3.5" />
                {searchCount > 0
                  ? `依据：过去${searchPlan?.period_days || 7}天有 ${metricNumber(searchCount)} 人搜「${ozonTopQuery(searchAction)}」。`
                  : bestReferenceCount > 0
                    ? `依据：参考来源有 ${metricNumber(bestReferenceCount)} 次搜索「${ozonTopQuery(searchAction)}」。`
                    : `依据：Ozon后台返回 ${metricNumber(queryCount)} 个搜索词「${ozonTopQuery(searchAction)}」，但没有搜索人数。`}
              </p>
            </section>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="activity-stream glass-panel timeline-panel main-flow-footer">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div>
            <div className="panel-kicker">底部过程</div>
            <CardTitle>运行过程</CardTitle>
          </div>
          <Badge variant={String(status).toUpperCase().includes("FAILED") ? "danger" : "muted"}>
            {detail?.pipeline_progress?.step_label || card?.pipeline_progress?.step_label || (currentStep ? readableStageName(currentStep) : "待命")}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <ReferenceProgressFooter tasks={referenceTasks} />
        <div className="main-flow-footer-grid">
          <section className="main-flow-steps">
            {MAIN_FLOW.map((item, index) => {
              const state = flowState(detail || null, item);
              return (
                <article key={item.key} className={cn("main-flow-step", state)}>
                  <span>{state === "done" ? <CheckCircle2 /> : state === "running" ? <Clock3 /> : <Circle />}</span>
                  <strong>{item.label}</strong>
                  <small>{state === "done" ? "已完成" : state === "running" ? "运行中" : state === "failed" ? "失败" : "等待"}</small>
                  {index < MAIN_FLOW.length - 1 && <i />}
                </article>
              );
            })}
            {failure && <p className="main-flow-failure">失败原因：{failure}</p>}
          </section>
          <section className="price-package-check">
            <div className="check-title">
              <PackageCheck className="h-4 w-4" />
              <strong>价格 & 包装检查</strong>
              <Badge variant={packageCheck?.passed ? "default" : "warning"}>{packageCheck?.passed ? "通过" : "待检查"}</Badge>
            </div>
            <div className="check-grid">
              <div><span>成本价</span><strong>{money(firstPrice?.purchase_cost_cny)}</strong></div>
              <div><span>建议售价</span><strong>{rub(firstPrice?.selling_price_rub)}</strong></div>
              <div><span>毛利率</span><strong>{typeof profitRate === "number" ? `${Math.round(profitRate * 100)}%` : "未生成"}</strong></div>
              <div><span>包装重量</span><strong>{weightText(packageCheck?.package_weight_g)}</strong></div>
              <div><span>商品重量</span><strong>{weightText(packageCheck?.product_weight_g)}</strong></div>
              <div><span>包装尺寸</span><strong>{cmText(packageCheck?.package_dimensions_cm)}</strong></div>
            </div>
            <p>
              <UploadCloud className="h-3.5 w-3.5" />
              {packageCheck?.message || "选择商品后显示检查结果"}
            </p>
          </section>
        </div>
      </CardContent>
    </Card>
  );
}
