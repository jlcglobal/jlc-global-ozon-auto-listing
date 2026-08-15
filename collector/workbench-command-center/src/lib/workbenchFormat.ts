import {
  Activity,
  AlertTriangle,
  Bot,
  Box,
  BrainCircuit,
  CheckCircle2,
  Image,
  Layers3,
  PackageCheck,
  ShieldCheck,
  Sparkles,
  Truck,
  UploadCloud,
} from "lucide-react";
import { assetUrl } from "@/services/workbenchApi";
import type { LogEntry, ProductCard, ProductDetail, ProductsResponse, RiskItem, SystemStatus } from "@/types/workbench";

export const factoryStages = [
  { key: "SOURCE", label: "采集来源", hint: "1688 商品资料", icon: Box, steps: ["collect_source", "validate_source"] },
  { key: "VISION AI", label: "视觉分析", hint: "商品事实识别", icon: BrainCircuit, steps: ["product_analysis", "category_match", "variant_rules"] },
  { key: "SEO ENGINE", label: "SEO 引擎", hint: "俄文标题简介标签", icon: Sparkles, steps: ["product_positioning", "ecommerce_design", "russian_copy"] },
  { key: "IMAGE GENERATOR", label: "图片生成", hint: "主图与详情图", icon: Image, steps: ["image_plan", "image_generation", "image_qc"] },
  { key: "CONTENT ENGINE", label: "商品卡编译", hint: "属性与字段合法化", icon: Layers3, steps: ["field_completion"] },
  { key: "OZON UPLOADER", label: "Ozon 交接", hint: "提交商品卡", icon: UploadCloud, steps: ["ozon_upload"] },
];

export const readableStep: Record<string, string> = {
  collect_source: "采集完成",
  validate_source: "资料检查",
  product_analysis: "商品分析",
  category_match: "Ozon 类目",
  variant_rules: "SKU 规则",
  measurements: "重量尺寸",
  offer_exists_check: "重复检查",
  upload_feasibility: "上架可行性",
  product_positioning: "商品定位",
  ecommerce_design: "电商设计",
  russian_copy: "俄文 SEO",
  field_completion: "Ozon 属性",
  image_plan: "图片方案",
  image_generation: "图片生成",
  image_qc: "图片检查",
  ozon_upload: "Ozon 上传",
  read_only_status_query: "等待 Ozon 处理",
  complete: "已完成",
};

export type CommandResult = {
  tone: "ok" | "danger" | "idle";
  message: string;
};

export type AttentionKind = "image" | "product" | "upload" | "pipeline";
export type AttentionWorkflowStatus = "Open" | "Processing" | "Resolved";

export function statusTone(value?: string) {
  const raw = String(value || "").toUpperCase();
  if (["FAILED", "NEEDS_ATTENTION"].includes(raw)) return "danger";
  if (["PROCESSING", "QUEUED", "UPLOADING", "WAITING_FOR_AI_SERVICE"].includes(raw)) return "running";
  if (["OZON_REFERENCE_DRAFT"].includes(raw)) return "idle";
  if (["OZON_REFERENCE_IMAGES_PARTIAL"].includes(raw)) return "idle";
  if (["OZON_REFERENCE_IMAGES_GENERATED"].includes(raw)) return "ok";
  if (["HANDED_OFF_TO_OZON", "PENDING_REMOTE", "OZON_MODERATION", "UPLOADED", "ACTIVE", "CREATED", "COMPLETE", "COMPLETED"].includes(raw)) return "ok";
  return "idle";
}

export function statusLabel(value?: string) {
  const raw = String(value || "").toUpperCase();
  return {
    FAILED: "失败",
    NEEDS_ATTENTION: "需要处理",
    PROCESSING: "生产中",
    QUEUED: "排队中",
    UPLOADING: "上传中",
    WAITING_FOR_AI_SERVICE: "等待 AI",
    OZON_REFERENCE_DRAFT: "参考草稿",
    OZON_REFERENCE_IMAGES_PARTIAL: "参考图片部分完成",
    OZON_REFERENCE_IMAGES_GENERATED: "参考图片已生成",
    STOPPED: "已停止",
    PARTIAL: "部分完成",
    PENDING_REMOTE: "已提交 Ozon",
    OZON_MODERATION: "Ozon 审核中",
    HANDED_OFF_TO_OZON: "已提交 Ozon",
    UPLOADED: "已上传",
    ACTIVE: "已上架",
    CREATED: "已创建",
    COMPLETE: "完成",
    COMPLETED: "完成",
    UNKNOWN: "未知",
  }[raw] || value || "未知";
}

export function serviceText(ok: boolean) {
  return ok ? "在线" : "检查";
}

export function stepStatus(detail: ProductDetail | null, stage: (typeof factoryStages)[number]) {
  const completed = new Set(detail?.status?.completed_steps || []);
  const pending = new Set(detail?.status?.pending_steps || []);
  const current = detail?.status?.current_step;
  const done = stage.steps.filter((step) => completed.has(step)).length;
  const percent = Math.round((done / stage.steps.length) * 100);
  const running = stage.steps.includes(String(current)) && pending.size > 0;
  const active = stage.steps.includes(String(current));
  return {
    percent,
    active,
    state: running ? "RUNNING" : percent === 100 ? "COMPLETE" : pending.size ? "WAITING" : "STANDBY",
  };
}

export function readableStageName(step?: string) {
  if (!step) return "待命";
  return readableStep[step] || step.replaceAll("_", " ");
}

export function productStepLabel(product?: Pick<ProductCard, "current_step" | "pipeline_progress"> | null) {
  return product?.pipeline_progress?.step_label || readableStageName(product?.pipeline_progress?.step || product?.current_step);
}

export function pipelineStateLabel(state?: string) {
  return {
    RUNNING: "运行中",
    COMPLETE: "完成",
    WAITING: "等待",
    STANDBY: "待命",
  }[String(state || "").toUpperCase()] || String(state || "待命");
}

export function currentProductionState(detail: ProductDetail | null, card?: ProductCard) {
  const detailStatus = String(detail?.status?.status || detail?.raw_status || "").toUpperCase();
  const cardStatus = String(card?.raw_status || "").toUpperCase();
  const activeStatuses = new Set(["PROCESSING", "RUNNING", "QUEUED", "UPLOADING", "WAITING_FOR_AI_SERVICE"]);
  const preferLiveCard = activeStatuses.has(cardStatus) && !activeStatuses.has(detailStatus);
  const progressStep = detail?.pipeline_progress?.step;
  const step = String(detail?.pipeline_progress?.is_running) === "false" && progressStep
    ? progressStep
    : preferLiveCard
    ? card?.current_step || detail?.status?.current_step || ""
    : detail?.status?.current_step || card?.current_step || "";
  const status = preferLiveCard ? cardStatus : detailStatus || cardStatus || "UNKNOWN";
  const submittedReadOnly = isSubmittedReadOnly(detail, card);
  const waitingRemote = ["HANDED_OFF_TO_OZON", "PENDING_REMOTE", "OZON_MODERATION"].includes(status);
  const progress = submittedReadOnly
    ? waitingRemote ? 99 : 100
    : preferLiveCard ? card?.progress ?? detail?.status?.progress ?? 0 : detail?.status?.progress ?? card?.progress ?? 0;
  const completed = detail?.status?.completed_steps?.length || 0;
  const pending = submittedReadOnly ? 0 : detail?.status?.pending_steps?.length || 0;
  const imageReady = detail?.images?.filter((image) => image.state && image.state !== "failed").length || card?.image_count || 0;

  return {
    step,
    stepLabel: submittedReadOnly ? statusLabel(status) : detail?.pipeline_progress?.step_label || readableStageName(step),
    progress,
    status,
    completed,
    pending,
    imageReady,
  };
}

export function selectedRegenerationSlot(detail: ProductDetail | null) {
  const images = detail?.images || [];
  const failed = images.find((image) => ["failed", "rejected"].includes(String(image.state || image.status || "").toLowerCase()));
  return failed?.slot || images[0]?.slot || "";
}

export function shouldRecover(detail: ProductDetail | null, card?: ProductCard) {
  const status = String(detail?.status?.status || card?.raw_status || "").toUpperCase();
  return ["FAILED", "NEEDS_ATTENTION", "STOPPED", "WAITING_FOR_AI_SERVICE", "PARTIAL"].includes(status);
}

export function isProductRunning(detail: ProductDetail | null, card?: ProductCard) {
  const status = String(detail?.status?.status || detail?.raw_status || card?.raw_status || "").toUpperCase();
  const cardStatus = String(card?.raw_status || "").toUpperCase();
  if (isSubmittedReadOnly(detail, card)) return false;
  if (shouldRecover(detail, card)) return false;
  if (["PROCESSING", "QUEUED", "UPLOADING", "RUNNING"].includes(cardStatus)) return true;
  if (status === "OZON_REFERENCE_DRAFT") return false;
  if (["PROCESSING", "QUEUED", "UPLOADING", "RUNNING"].includes(status)) return true;
  return false;
}

export function isSubmittedReadOnly(detail: ProductDetail | null, card?: ProductCard) {
  const status = String(detail?.status?.status || detail?.raw_status || card?.raw_status || "").toUpperCase();
  const readinessState = String(detail?.production_readiness?.state || "").toLowerCase();
  return readinessState === "submitted_read_only" || ["HANDED_OFF_TO_OZON", "PENDING_REMOTE", "OZON_MODERATION", "UPLOADED", "ACTIVE", "CREATED"].includes(status);
}

export function translateLog(message: string) {
  const text = message.toLowerCase();
  if (text.includes("1688")) return "1688 资料已采集";
  if (text.includes("分析")) return "商品事实分析完成";
  if (text.includes("标题") || text.includes("简介") || text.includes("标签")) return "俄文 SEO 资料已生成";
  if (text.includes("属性")) return "Ozon 属性已编译";
  if (text.includes("生图") || text.includes("image generation")) return "图片引擎正在生成";
  if (text.includes("图片生成")) return "图片包已生成";
  if (text.includes("图片质检")) return "图片检查完成";
  if (text.includes("失败") || text.includes("error")) return "生产事件需要处理";
  if (text.includes("提交ozon")) return "已提交 Ozon 交接";
  if (text.includes("任务号")) return "已收到 Ozon 任务号";
  if (text.includes("ozon_update")) return "Ozon 状态记录已更新";
  return message;
}

export function timelineEventKind(entry: LogEntry) {
  const text = `${entry.message} ${entry.status || ""} ${entry.step || ""}`.toLowerCase();
  if (text.includes("点击") || text.includes("手动") || text.includes("user") || text.includes("operation")) return "user";
  return "system";
}

export function eventTone(entry: LogEntry) {
  const message = entry.message.toLowerCase();
  if (entry.level === "error" || message.includes("失败") || message.includes("error")) return "danger";
  if (message.includes("图片") || message.includes("生图") || message.includes("image")) return "visual";
  if (message.includes("ozon") || message.includes("上传") || message.includes("提交")) return "handoff";
  return "normal";
}

export function eventIcon(entry: LogEntry) {
  if (eventTone(entry) === "danger") return AlertTriangle;
  if (timelineEventKind(entry) === "user") return CheckCircle2;
  return Activity;
}

export function topServices(system?: SystemStatus | null) {
  const normal = system?.state === "normal";
  return [
    { label: "系统状态", value: system?.label || "读取中", ok: normal, icon: ShieldCheck },
    { label: "AI 引擎", value: serviceText(Boolean(system?.codex_ready)), ok: Boolean(system?.codex_ready), icon: Bot },
    { label: "Ozon API", value: "未写入", ok: true, icon: Truck },
    { label: "图片引擎", value: `${system?.image_slot_concurrency ?? 0} 路`, ok: normal, icon: Image },
    { label: "上传服务", value: system?.batch_running ? "运行中" : "待命", ok: true, icon: UploadCloud },
  ];
}

export function buildStageTimeline(detail: ProductDetail | null) {
  const steps = detail?.status?.steps || [];
  if (!steps.length) return [];
  return steps.filter((step) => ["product_analysis", "russian_copy", "image_generation", "image_qc", "ozon_upload"].includes(step.name));
}

export function productImage(detail: ProductDetail | null, card?: ProductCard) {
  const mainImage = detail?.images?.find((item) => item.url && (item.type === "main" || item.image_type === "main" || item.role === "main"));
  const image = mainImage?.url || detail?.images?.find((item) => item.url)?.url || card?.thumbnail_url;
  return assetUrl(image);
}

export function buildTrend(products?: ProductsResponse | null) {
  const items = products?.items || [];
  const source = items.length ? items.slice(0, 10).reverse() : [];
  const bars = source.map((item, index) => {
    const base = Math.max(8, Math.min(100, item.progress || 0));
    const successBoost = statusTone(item.raw_status) === "ok" ? 12 : 0;
    return {
      key: item.product_id || String(index),
      value: Math.min(100, base + successBoost),
      active: ["running", "idle"].includes(statusTone(item.raw_status)) && item.progress > 0 && item.progress < 100,
    };
  });
  return bars.length ? bars : [{ key: "idle", value: 14, active: false }];
}

export function classifyRisk(item: RiskItem): AttentionKind {
  const text = `${item.category || ""} ${item.type || ""} ${item.step || ""} ${item.title || ""} ${item.message || ""}`.toLowerCase();
  if (text.includes("image") || text.includes("图片") || text.includes("生成图")) return "image";
  if (text.includes("upload") || text.includes("ozon") || text.includes("上传")) return "upload";
  if (text.includes("ai") || text.includes("pipeline") || text.includes("流程") || text.includes("step")) return "pipeline";
  return "product";
}

export function kindLabel(kind: AttentionKind) {
  return {
    image: "图片异常",
    product: "商品资料异常",
    upload: "上传异常",
    pipeline: "AI流程异常",
  }[kind];
}

export function riskSlot(item: RiskItem) {
  const text = `${item.message || ""} ${item.title || ""}`;
  return text.match(/\b(?:main|detail|sku)[-_][a-z0-9-]+/i)?.[0] || "";
}

export function inferAttentionStatus(item: RiskItem, product?: ProductCard, detail?: ProductDetail | null): AttentionWorkflowStatus {
  const rawStatus = String(detail?.status?.status || product?.raw_status || "").toUpperCase();
  const level = String(item.level || "").toLowerCase();
  if (["HANDED_OFF_TO_OZON", "PENDING_REMOTE", "OZON_MODERATION", "UPLOADED", "ACTIVE", "CREATED", "COMPLETE", "COMPLETED"].includes(rawStatus)) return "Resolved";
  if (["PROCESSING", "QUEUED", "UPLOADING", "WAITING_FOR_AI_SERVICE", "PARTIAL"].includes(rawStatus)) return "Processing";
  if (["FAILED", "NEEDS_ATTENTION", "STOPPED"].includes(rawStatus) || level.includes("high") || level.includes("error")) return "Open";
  if ((product?.progress || 0) > 0 && (product?.progress || 0) < 100) return "Processing";
  return "Open";
}

export function workflowBadgeTone(status: AttentionWorkflowStatus) {
  if (status === "Resolved") return "default";
  if (status === "Processing") return "warning";
  return "danger";
}

export function workflowStatusLabel(status: AttentionWorkflowStatus) {
  return {
    Open: "待处理",
    Processing: "处理中",
    Resolved: "已解决",
  }[status];
}

export function severityLabel(level?: string) {
  const raw = String(level || "review").toLowerCase();
  if (raw.includes("high") || raw.includes("error")) return "高风险";
  if (raw.includes("medium") || raw.includes("warning")) return "中风险";
  if (raw.includes("low")) return "低风险";
  return "需查看";
}

export function emptyProductIcon() {
  return PackageCheck;
}
