import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import { AlertTriangle, Boxes, Download, FileText, ImageIcon, ListTodo, RefreshCcw, Save, Sparkles, Store, Tag, Trash2, UploadCloud, Zap } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { CategoryChangeDialog } from "@/components/product/CategoryChangeDialog";
import { ProductImageGallery } from "@/components/product/ProductImageGallery";
import { SkuImageBindingPanel } from "@/components/product/SkuImageBindingPanel";
import { assetUrl, exportImagesUrl, loadProductDeletePreview } from "@/services/workbenchApi";
import { cn, formatTime, truncate } from "@/lib/utils";
import { isProductRunning, isSubmittedReadOnly, readableStageName, selectedRegenerationSlot, shouldRecover, statusLabel, timelineEventKind } from "@/lib/workbenchFormat";
import type { DeletePreviewResponse, LogEntry, ProductCard, ProductDetail, ProductDraftPayload } from "@/types/workbench";

export type ProductDrawerFocus = "overview" | "stores" | "question" | "risks" | "timeline";
type SkuDetail = NonNullable<ProductDetail["skus"]>[number];
type MeasurementDraft = Record<string, {
  product_weight_g: string;
  product_length_cm: string;
  product_width_cm: string;
  product_height_cm: string;
  package_weight_g: string;
  package_length_cm: string;
  package_width_cm: string;
  package_height_cm: string;
}>;

function money(value?: number, currency = "CNY") {
  if (typeof value !== "number" || Number.isNaN(value)) return "--";
  if (currency === "RUB") return `${Math.round(value)} ₽`;
  return `¥${value.toFixed(value >= 100 ? 0 : 2)}`;
}

function percent(value?: number) {
  if (typeof value !== "number" || Number.isNaN(value)) return "--";
  return `${Math.round(value * 100)}%`;
}

function selectedStores(detail: ProductDetail | null) {
  const publications = detail?.publications?.stores || {};
  return (detail?.stores || []).filter((store) => publications[store.id]?.selected);
}

function publicationStatus(detail: ProductDetail | null, storeId: string) {
  const publication = detail?.publications?.stores?.[storeId];
  return publication?.status || publication?.upload_status || (publication?.selected ? "selected" : "not selected");
}

function skuBindingLabel(sku: SkuDetail) {
  if (sku.binding_required) return "需要绑定图片";
  if (sku.image_missing) return "缺少SKU图";
  if (sku.binding_status === "sku_owned_image") return "SKU图已匹配";
  return sku.binding_status || "图片状态未知";
}

function normalizeTagInput(value: string) {
  return value
    .split(/[\n,，]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => (item.startsWith("#") ? item : `#${item}`));
}

function skuDraftKey(sku: SkuDetail) {
  return sku.sku_id || sku.offer_id || "";
}

function draftPriceValue(sku: SkuDetail) {
  const value = sku.selling_price_cny ?? sku.price_cny;
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "";
}

function parseDraftPrice(value: string) {
  const normalized = value.trim().replace(",", ".");
  if (!normalized) return null;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) && parsed > 0 ? Math.round(parsed * 100) / 100 : null;
}

function numericText(value?: number) {
  return typeof value === "number" && Number.isFinite(value) ? String(Math.round(value * 1000) / 1000) : "";
}

function positiveNumber(value: string) {
  const text = value.trim().replace(",", ".");
  if (!text) return null;
  const parsed = Number(text);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function skuRowFact(sku: SkuDetail, key: string) {
  const row = sku.sku_row as Record<string, unknown> | undefined;
  const value = row?.[key];
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function skuWeightValue(sku: SkuDetail, key: "product_weight" | "package_weight", fallback?: number) {
  const rowValue = skuRowFact(sku, key).canonical_value ?? skuRowFact(sku, key).value;
  const parsed = Number(rowValue);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function dimensionsFromRow(sku: SkuDetail, key: "product_dimensions" | "package_dimensions", fallback?: { length?: number; width?: number; height?: number }) {
  const rowValue = skuRowFact(sku, key).canonical_value ?? skuRowFact(sku, key).value;
  if (rowValue && typeof rowValue === "object") {
    const record = rowValue as Record<string, unknown>;
    const length = Number(record.length_mm ?? record.length);
    const width = Number(record.width_mm ?? record.width);
    const height = Number(record.height_mm ?? record.height);
    if ([length, width, height].some((item) => Number.isFinite(item) && item > 0)) {
      const unitIsMm = record.length_mm !== undefined || record.width_mm !== undefined || record.height_mm !== undefined;
      return {
        length: Number.isFinite(length) && length > 0 ? (unitIsMm ? length / 10 : length) : undefined,
        width: Number.isFinite(width) && width > 0 ? (unitIsMm ? width / 10 : width) : undefined,
        height: Number.isFinite(height) && height > 0 ? (unitIsMm ? height / 10 : height) : undefined,
      };
    }
  }
  return fallback || {};
}

function measurementDraftValue(sku: SkuDetail): MeasurementDraft[string] {
  const productDimensions = dimensionsFromRow(sku, "product_dimensions", sku.dimensions_cm);
  const packageDimensions = dimensionsFromRow(sku, "package_dimensions", sku.package_dimensions_cm);
  return {
    product_weight_g: numericText(skuWeightValue(sku, "product_weight", sku.weight_g)),
    product_length_cm: numericText(productDimensions.length),
    product_width_cm: numericText(productDimensions.width),
    product_height_cm: numericText(productDimensions.height),
    package_weight_g: numericText(skuWeightValue(sku, "package_weight", sku.package_weight_g)),
    package_length_cm: numericText(packageDimensions.length),
    package_width_cm: numericText(packageDimensions.width),
    package_height_cm: numericText(packageDimensions.height),
  };
}

function measurementChanged(current: MeasurementDraft[string], initial: MeasurementDraft[string]) {
  return Object.keys(current).some((key) => current[key as keyof MeasurementDraft[string]] !== initial[key as keyof MeasurementDraft[string]]);
}

function measurementDraftIsValid(draft: MeasurementDraft[string]) {
  const values = Object.values(draft);
  if (!values.every((value) => !value.trim() || positiveNumber(value) !== null)) return false;
  const productWeight = positiveNumber(draft.product_weight_g);
  const packageWeight = positiveNumber(draft.package_weight_g);
  if (productWeight !== null && packageWeight !== null && packageWeight <= productWeight) return false;
  for (const axis of ["length", "width", "height"] as const) {
    const productValue = positiveNumber(draft[`product_${axis}_cm`]);
    const packageValue = positiveNumber(draft[`package_${axis}_cm`]);
    if (productValue !== null && packageValue !== null && packageValue <= productValue) return false;
  }
  return true;
}

function dimensionStringFromDraft(draft: MeasurementDraft[string]) {
  const dims = {
    length: positiveNumber(draft.product_length_cm) ?? undefined,
    width: positiveNumber(draft.product_width_cm) ?? undefined,
    height: positiveNumber(draft.product_height_cm) ?? undefined,
  };
  return dimensionText(dims);
}

function attributeValueText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未填";
  if (Array.isArray(value)) return value.map(attributeValueText).join(", ") || "未填";
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    return String(record.value ?? record.name ?? record.text ?? JSON.stringify(record));
  }
  return String(value);
}

function analysisPointText(value: unknown) {
  if (typeof value === "string") return value;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return String(record.text || record.title || record.value || record.message || "");
  }
  return "";
}

function dimensionText(value?: { length?: number; width?: number; height?: number }) {
  if (!value || (!value.length && !value.width && !value.height)) return "--";
  return `${value.length ?? "--"} x ${value.width ?? "--"} x ${value.height ?? "--"} cm`;
}

function dynamicAttributeText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未填";
  if (typeof value === "object" && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    const raw = record.canonical_value ?? record.target_value ?? record.value;
    const unit = record.canonical_unit ?? record.target_unit ?? "";
    return `${attributeValueText(raw)}${unit ? ` ${unit}` : ""}`;
  }
  return attributeValueText(value);
}

function dynamicAttributeName(value: unknown) {
  if (!value || typeof value !== "object") return "动态属性";
  const record = value as Record<string, unknown>;
  return String(record.attribute_name_zh || record.attribute_name || record.name || record.attribute_id || "动态属性");
}

export function ProductDetailDrawer({
  open,
  onOpenChange,
  detail,
  card,
  logs,
  actionBusy,
  onRunProduct,
  onRefreshOzonStatus,
  onRegenerateImage,
  onRegenerateImageSlot,
  onReplaceImage,
  onImageAction,
  onSaveVisualPreference,
  onBindSkuImage,
  onProductChanged,
  onSaveStores,
  onRetryStore,
  onRetryFailedStores,
  onSuggestionAction,
  onAnswerQuestion,
  onSaveProductDraft,
  onDeleteProduct,
  initialFocus = "overview",
  autoContinueAfterInput,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  detail: ProductDetail | null;
  card?: ProductCard;
  logs: LogEntry[];
  actionBusy?: boolean;
  onRunProduct: () => void;
  onRefreshOzonStatus: () => void;
  onRegenerateImage: () => void;
  onRegenerateImageSlot: (slot: string, prompt?: string) => Promise<void> | void;
  onReplaceImage: (slot: string, dataUrl: string) => Promise<void> | void;
  onImageAction: (
    slot: string,
    payload:
      | { action: "keep" | "accept" }
      | { action: "move"; direction: "up" | "down" }
      | { action: "set_role"; role: "main" | "detail" | "disclaimer" | "color_sample" }
      | { action: "delete" },
  ) => Promise<void> | void;
  onSaveVisualPreference: (setHint: string) => Promise<void>;
  onBindSkuImage: (skuId: string, selectedImagePath: string) => Promise<void>;
  onProductChanged: (message: string) => Promise<void> | void;
  onSaveStores: (storeIds: string[]) => Promise<void>;
  onRetryStore: (storeId: string) => Promise<void>;
  onRetryFailedStores: (storeIds: string[]) => Promise<void>;
  onSuggestionAction: (suggestionId: string, action: "accept" | "ignore" | "mute_similar") => Promise<void>;
  onAnswerQuestion: (answer: string) => Promise<void>;
  onSaveProductDraft: (payload: ProductDraftPayload) => Promise<void>;
  onDeleteProduct: () => Promise<void>;
  initialFocus?: ProductDrawerFocus;
  autoContinueAfterInput?: boolean;
}) {
  const [focus, setFocus] = useState<ProductDrawerFocus>(initialFocus);
  const sectionRefs = useRef<Partial<Record<ProductDrawerFocus, HTMLElement | null>>>({});
  const [categoryOpen, setCategoryOpen] = useState(false);
  const [bindingBusyKey, setBindingBusyKey] = useState("");
  const [selectedStoreIds, setSelectedStoreIds] = useState<string[]>([]);
  const [storeBusy, setStoreBusy] = useState(false);
  const [suggestionBusy, setSuggestionBusy] = useState("");
  const [answer, setAnswer] = useState("");
  const [answerBusy, setAnswerBusy] = useState(false);
  const [copyDraft, setCopyDraft] = useState({ title_ru: "", description_ru: "", tagsText: "" });
  const [copyBusy, setCopyBusy] = useState(false);
  const [pricingDraft, setPricingDraft] = useState<Record<string, string>>({});
  const [pricingBusy, setPricingBusy] = useState(false);
  const [measurementDraft, setMeasurementDraft] = useState<MeasurementDraft>({});
  const [measurementBusy, setMeasurementBusy] = useState(false);
  const [visualHint, setVisualHint] = useState("");
  const [visualBusy, setVisualBusy] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [showAllTags, setShowAllTags] = useState(false);
  const [deletePreview, setDeletePreview] = useState<DeletePreviewResponse | null>(null);
  const [deletePreviewBusy, setDeletePreviewBusy] = useState(false);
  const [deletePreviewError, setDeletePreviewError] = useState("");
  const riskItems = detail?.risk?.items || [];
  const suggestions = detail?.ai_suggestions || [];
  const regenSlot = selectedRegenerationSlot(detail);
  const running = isProductRunning(detail, card);
  const actionLabel = shouldRecover(detail, card) ? "恢复任务" : "继续生产";
  const submittedReadOnly = isSubmittedReadOnly(detail, card);
  const remoteWaiting = ["PENDING_REMOTE", "OZON_MODERATION", "HANDED_OFF_TO_OZON"].includes(String(detail?.status?.status || detail?.raw_status || card?.raw_status || "").toUpperCase());
  const sortedLogs = useMemo(() => logs.slice(0, 12), [logs]);
  const stores = selectedStores(detail);
  const allStores = detail?.stores || [];
  const publicationStores = detail?.publications?.stores || {};
  const failedStoreIds = Object.entries(publicationStores)
    .filter(([, record]) => String(record?.status || record?.upload_status || "").toUpperCase() === "FAILED")
    .map(([storeId]) => storeId);
  const categoryLabel = detail?.category?.category_name_zh || detail?.category?.category_name || "未选择类目";
  const readiness = detail?.production_readiness;
  const imageContract = detail?.image_contract;
  const attributeSummary = detail?.attributes?.summary;
  const missingAttributes = detail?.attributes?.missing_required_attributes || [];
  const visibleAttributes = (detail?.attributes?.attributes || []).slice(0, 14);
  const sellingPoints = (detail?.analysis?.selling_points || []).map(analysisPointText).filter(Boolean).slice(0, 6);
  const analysisRisks = (detail?.analysis?.risks || []).slice(0, 4);
  const tags = detail?.content?.tags || [];
  const visibleTags = showAllTags ? tags : tags.slice(0, 12);
  const richContentKeys = Object.keys(detail?.rich_content || {}).slice(0, 8);
  const hasPricingDraft = useMemo(() => (
    (detail?.skus || []).some((sku) => {
      const key = skuDraftKey(sku);
      return key && pricingDraft[key] !== undefined && pricingDraft[key] !== draftPriceValue(sku);
    })
  ), [detail?.skus, pricingDraft]);
  const pricingDraftValid = useMemo(() => (
    Object.values(pricingDraft).every((value) => !value.trim() || parseDraftPrice(value) !== null)
  ), [pricingDraft]);
  const hasMeasurementDraft = useMemo(() => (
    (detail?.skus || []).some((sku) => {
      const key = skuDraftKey(sku);
      return key && measurementDraft[key] && measurementChanged(measurementDraft[key], measurementDraftValue(sku));
    })
  ), [detail?.skus, measurementDraft]);
  const measurementDraftValid = useMemo(() => (
    Object.values(measurementDraft).every(measurementDraftIsValid)
  ), [measurementDraft]);

  function bindSection(name: ProductDrawerFocus) {
    return (node: HTMLElement | null) => {
      sectionRefs.current[name] = node;
    };
  }

  async function continueAfterInput() {
    if (!autoContinueAfterInput || submittedReadOnly || running || actionBusy) return;
    await onRunProduct();
  }

  async function bindSku(skuId: string, selectedImagePath: string) {
    const key = `${skuId}:${selectedImagePath}`;
    setBindingBusyKey(key);
    try {
      await onBindSkuImage(skuId, selectedImagePath);
    } finally {
      setBindingBusyKey("");
    }
  }

  function toggleStore(storeId: string) {
    setSelectedStoreIds((current) =>
      current.includes(storeId) ? current.filter((id) => id !== storeId) : [...current, storeId],
    );
  }

  async function saveStores() {
    setStoreBusy(true);
    try {
      await onSaveStores(selectedStoreIds);
      await continueAfterInput();
    } finally {
      setStoreBusy(false);
    }
  }

  async function actOnSuggestion(suggestionId: string, action: "accept" | "ignore" | "mute_similar") {
    setSuggestionBusy(`${suggestionId}:${action}`);
    try {
      await onSuggestionAction(suggestionId, action);
    } finally {
      setSuggestionBusy("");
    }
  }

  async function submitAnswer() {
    if (!answer.trim()) return;
    setAnswerBusy(true);
    try {
      await onAnswerQuestion(answer.trim());
      setAnswer("");
      await continueAfterInput();
    } finally {
      setAnswerBusy(false);
    }
  }

  async function saveCopyDraft() {
    setCopyBusy(true);
    try {
      await onSaveProductDraft({
        title_ru: copyDraft.title_ru.trim(),
        description_ru: copyDraft.description_ru.trim(),
        tags: normalizeTagInput(copyDraft.tagsText),
      });
    } finally {
      setCopyBusy(false);
    }
  }

  async function savePricingDraft() {
    const overrides: Record<string, Record<string, unknown>> = {};
    for (const sku of detail?.skus || []) {
      const key = skuDraftKey(sku);
      if (!key) continue;
      const current = pricingDraft[key];
      if (current === undefined || current === draftPriceValue(sku)) continue;
      const parsed = parseDraftPrice(current);
      if (parsed === null) continue;
      overrides[key] = { selling_price_cny: parsed };
    }
    if (!Object.keys(overrides).length) return;
    setPricingBusy(true);
    try {
      await onSaveProductDraft({ sku_overrides: overrides });
    } finally {
      setPricingBusy(false);
    }
  }

  async function saveMeasurementDraft() {
    const overrides: Record<string, Record<string, unknown>> = {};
    for (const sku of detail?.skus || []) {
      const key = skuDraftKey(sku);
      if (!key) continue;
      const current = measurementDraft[key];
      if (!current || !measurementChanged(current, measurementDraftValue(sku))) continue;
      const values: Record<string, unknown> = {};
      const productWeight = positiveNumber(current.product_weight_g);
      const packageWeight = positiveNumber(current.package_weight_g);
      const productLength = positiveNumber(current.product_length_cm);
      const productWidth = positiveNumber(current.product_width_cm);
      const productHeight = positiveNumber(current.product_height_cm);
      const packageLength = positiveNumber(current.package_length_cm);
      const packageWidth = positiveNumber(current.package_width_cm);
      const packageHeight = positiveNumber(current.package_height_cm);
      if (productWeight !== null) values.product_weight_g = Math.round(productWeight);
      if (packageWeight !== null) values.package_weight_g = Math.round(packageWeight);
      if (productLength !== null) values.product_length_mm = Math.round(productLength * 10);
      if (productWidth !== null) values.product_width_mm = Math.round(productWidth * 10);
      if (productHeight !== null) values.product_height_mm = Math.round(productHeight * 10);
      if (packageLength !== null) values.package_length_mm = Math.round(packageLength * 10);
      if (packageWidth !== null) values.package_width_mm = Math.round(packageWidth * 10);
      if (packageHeight !== null) values.package_height_mm = Math.round(packageHeight * 10);
      if (Object.keys(values).length) overrides[key] = values;
    }
    if (!Object.keys(overrides).length) return;
    setMeasurementBusy(true);
    try {
      await onSaveProductDraft({ sku_overrides: overrides });
    } finally {
      setMeasurementBusy(false);
    }
  }

  async function saveVisualHint() {
    setVisualBusy(true);
    try {
      await onSaveVisualPreference(visualHint.trim());
    } finally {
      setVisualBusy(false);
    }
  }

  useEffect(() => {
    const publications = detail?.publications?.stores || {};
    setSelectedStoreIds(Object.entries(publications).filter(([, record]) => record?.selected).map(([storeId]) => storeId));
  }, [detail?.product_id, detail?.publications?.stores]);

  useEffect(() => {
    setCopyDraft({
      title_ru: detail?.content?.title_ru || card?.title_ru || "",
      description_ru: detail?.content?.description_ru || "",
      tagsText: (detail?.content?.tags || []).join("\n"),
    });
  }, [detail?.product_id, detail?.content?.title_ru, detail?.content?.description_ru, detail?.content?.tags, card?.title_ru]);

  useEffect(() => {
    const next: Record<string, string> = {};
    for (const sku of detail?.skus || []) {
      const key = skuDraftKey(sku);
      if (key) next[key] = draftPriceValue(sku);
    }
    setPricingDraft(next);
  }, [detail?.product_id, detail?.skus]);

  useEffect(() => {
    const next: MeasurementDraft = {};
    for (const sku of detail?.skus || []) {
      const key = skuDraftKey(sku);
      if (key) next[key] = measurementDraftValue(sku);
    }
    setMeasurementDraft(next);
  }, [detail?.product_id, detail?.skus]);

  useEffect(() => {
    setVisualHint(detail?.visual_preference?.set_hint || "");
  }, [detail?.product_id, detail?.visual_preference?.set_hint]);

  useEffect(() => {
    if (!open) return;
    setFocus(initialFocus);
  }, [open, initialFocus, detail?.product_id]);

  useEffect(() => {
    if (!open) return;
    const timer = window.setTimeout(() => {
      sectionRefs.current[focus]?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 80);
    return () => window.clearTimeout(timer);
  }, [focus, open, detail?.product_id]);

  useEffect(() => {
    if (!deleteOpen || !detail?.product_id) return;
    let alive = true;
    setDeletePreviewBusy(true);
    setDeletePreviewError("");
    setDeletePreview(null);
    loadProductDeletePreview(detail.product_id)
      .then((preview) => {
        if (alive) setDeletePreview(preview);
      })
      .catch((error) => {
        if (alive) setDeletePreviewError(error instanceof Error ? error.message : "删除预览读取失败");
      })
      .finally(() => {
        if (alive) setDeletePreviewBusy(false);
      });
    return () => {
      alive = false;
    };
  }, [deleteOpen, detail?.product_id]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent>
        <motion.div
          className="detail-drawer"
          initial={{ opacity: 0, x: 24 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.28, ease: [0.2, 0.8, 0.2, 1] }}
        >
          <SheetHeader className="detail-drawer-header">
            <div className="panel-kicker">商品卡</div>
            <SheetTitle>{truncate(detail?.source?.title_cn || card?.title_cn, 86)}</SheetTitle>
            <SheetDescription>
              {detail?.product_id || card?.product_id || "LOCAL"} · {detail?.skus?.length || card?.sku_count || 0} SKU · {statusLabel(detail?.status?.status || detail?.raw_status || card?.raw_status || "UNKNOWN")}
            </SheetDescription>
          </SheetHeader>

          <div className="drawer-actions">
            <Button
              size="sm"
              onClick={remoteWaiting ? onRefreshOzonStatus : onRunProduct}
              disabled={!detail?.product_id || (submittedReadOnly && !remoteWaiting) || running || actionBusy}
            >
              {remoteWaiting ? <UploadCloud className="h-3.5 w-3.5" /> : <Zap className="h-3.5 w-3.5" />}
              {submittedReadOnly ? (remoteWaiting ? "查询Ozon结果" : "已创建") : running ? "生产中" : actionLabel}
            </Button>
            <Button size="sm" variant="secondary" onClick={onRegenerateImage} disabled={!detail?.product_id || submittedReadOnly || !regenSlot || actionBusy}>
              <RefreshCcw className="h-3.5 w-3.5" />
              重新生成图片
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setFocus("risks")}>
              <AlertTriangle className="h-3.5 w-3.5" />
              查看异常
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setFocus("timeline")}>
              <ListTodo className="h-3.5 w-3.5" />
              查看记录
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setCategoryOpen(true)} disabled={!detail?.product_id}>
              <Tag className="h-3.5 w-3.5" />
              修改类目
            </Button>
          </div>

          {autoContinueAfterInput && !submittedReadOnly && (
            <div className="drawer-auto-continue-note">
              缺资料已定位，保存后自动继续当前任务。
            </div>
          )}

          <ScrollArea className="detail-drawer-scroll">
            <div className="detail-section-grid">
              <section className={cn("detail-section", focus === "overview" && "active-section")}>
                <h4>商品卡概览</h4>
                <dl className="detail-facts">
                  <div><dt>SKU</dt><dd>{detail?.skus?.length || card?.sku_count || 0}</dd></div>
                  <div><dt>状态</dt><dd>{statusLabel(detail?.status?.status || detail?.raw_status || card?.raw_status || "UNKNOWN")}</dd></div>
                  <div><dt>当前步骤</dt><dd>{detail?.pipeline_progress?.step_label || readableStageName(detail?.pipeline_progress?.step || detail?.status?.current_step || detail?.current_step || card?.current_step)}</dd></div>
                  <div><dt>进度</dt><dd>{detail?.status?.progress ?? card?.progress ?? 0}%</dd></div>
                  <div><dt>类目</dt><dd>{categoryLabel}</dd></div>
                  <div><dt>店铺</dt><dd>{stores.length || detail?.publication_summary?.selected || card?.selected_store_count || 0}</dd></div>
                  <div><dt>图片</dt><dd>{imageContract ? `${imageContract.actual_main_count || 0}+${imageContract.actual_shared_detail_count || 0}/${imageContract.expected_total_count || 0}` : card?.image_count || 0}</dd></div>
                  <div><dt>上传</dt><dd>{detail?.ozon?.upload_status || detail?.publication_summary?.success ? "已交接" : "未交接"}</dd></div>
                </dl>
                <div className="source-facts">
                  <div>
                    <span>本地商品</span>
                    <strong>{detail?.product_id || card?.product_id || "--"}</strong>
                  </div>
                  <div>
                    <span>采集时间</span>
                    <strong>{formatTime(detail?.source?.captured_at)}</strong>
                  </div>
                  <div>
                    <span>1688 来源</span>
                    {detail?.source?.source_url && detail.source.source_url !== "unknown" ? (
                      <a href={detail.source.source_url} target="_blank" rel="noreferrer">打开原始商品</a>
                    ) : (
                      <strong>未记录链接</strong>
                    )}
                  </div>
                </div>
              </section>

              <section className="detail-section operations-section">
                <h4>生产状态</h4>
                <div className="operation-grid">
                  <article>
                    <UploadCloud className="h-4 w-4" />
                    <span>Ozon 交接</span>
                    <strong>{detail?.ozon?.upload_status || "unknown"}</strong>
                    <small>{detail?.ozon?.shop_name || "未记录店铺"} · task {detail?.ozon?.task_id || "--"}</small>
                  </article>
                  <article>
                    <Zap className="h-4 w-4" />
                    <span>生产就绪</span>
                    <strong>{readiness?.blocking ? "有阻断" : readiness?.state || "可继续"}</strong>
                    <small>{readiness?.message || "未发现阻断信息"}</small>
                  </article>
                  <article>
                    <ImageIcon className="h-4 w-4" />
                    <span>图片合同</span>
                    <strong>{imageContract?.expected_total_count || detail?.images?.length || 0} 张</strong>
                    <small>主图 {imageContract?.actual_main_count || 0} · 详情 {imageContract?.actual_shared_detail_count || 0}</small>
                  </article>
                </div>
              </section>

              <section ref={bindSection("stores")} className={cn("detail-section category-store-section", focus === "stores" && "active-section")}>
                <h4>类目与店铺</h4>
                <div className="category-card">
                  <Tag className="h-4 w-4" />
                  <div>
                    <strong>{categoryLabel}</strong>
                    <span>category {detail?.category?.category_id || "--"} · type {detail?.category?.type_id || "--"} · {detail?.category?.match_status || "unknown"}</span>
                  </div>
                  <Button size="sm" variant="secondary" onClick={() => setCategoryOpen(true)} disabled={!detail?.product_id}>修改</Button>
                </div>
                <div className="store-chip-grid">
                  {(allStores.length ? allStores : stores).slice(0, 8).map((store) => {
                    const selected = selectedStoreIds.includes(store.id);
                    const available = Boolean(store.enabled) && store.connection_status === "connected";
                    return (
                      <article key={store.id} className={cn(selected && "selected", !available && "disabled")}>
                        <Store className="h-4 w-4" />
                        <div>
                          <strong>{store.display_name || store.id}</strong>
                          <span>{store.connection_status || "unknown"} · {publicationStatus(detail, store.id)}</span>
                        </div>
                        <Button size="sm" variant={selected ? "default" : "secondary"} disabled={!available || actionBusy || storeBusy} onClick={() => toggleStore(store.id)}>
                          {selected ? "目标" : "选择"}
                        </Button>
                      </article>
                    );
                  })}
                  {!allStores.length && <p className="drawer-empty">暂无店铺数据</p>}
                </div>
                <div className="store-save-row">
                  <span>已选择 {selectedStoreIds.length} 家店铺</span>
                  <Button size="sm" onClick={saveStores} disabled={!detail?.product_id || !selectedStoreIds.length || storeBusy || actionBusy}>
                    保存目标店铺
                  </Button>
                </div>
              </section>

              <section className="detail-section store-publication-section">
                <h4>上传店铺状态</h4>
                <div className="publication-summary-row">
                  <span>目标 {detail?.publication_summary?.selected || 0}</span>
                  <span>成功 {detail?.publication_summary?.success || 0}</span>
                  <span>处理中 {detail?.publication_summary?.pending || 0}</span>
                  <span>失败 {detail?.publication_summary?.failed || 0}</span>
                  {failedStoreIds.length > 1 && (
                    <Button size="sm" variant="secondary" onClick={() => onRetryFailedStores(failedStoreIds)} disabled={actionBusy}>
                      重试全部失败店铺
                    </Button>
                  )}
                </div>
                <div className="publication-store-list">
                  {Object.entries(publicationStores).map(([storeId, record]) => {
                    const store = allStores.find((item) => item.id === storeId);
                    const state = String(record?.status || record?.upload_status || "unknown");
                    const failed = state.toUpperCase() === "FAILED";
                    const errors = (record?.sku_publications || []).flatMap((item) => item.errors || []).filter(Boolean);
                    return (
                      <article key={storeId} className={cn(failed && "failed")}>
                        <div>
                          <strong>{store?.display_name || storeId}</strong>
                          <span>{state} · {store?.connection_status || "unknown"}</span>
                          {errors.length > 0 && <small>{errors.slice(0, 2).join("；")}</small>}
                        </div>
                        {failed && (
                          <Button size="sm" variant="secondary" onClick={() => onRetryStore(storeId)} disabled={actionBusy}>
                            仅重试此店铺
                          </Button>
                        )}
                      </article>
                    );
                  })}
                  {!Object.keys(publicationStores).length && <p className="drawer-empty">暂无上传店铺记录</p>}
                </div>
              </section>

              <section className="detail-section attribute-overview-section">
                <h4>类目属性概览</h4>
                <div className="attribute-summary-grid">
                  <article><span>字段总数</span><strong>{attributeSummary?.total ?? visibleAttributes.length}</strong></article>
                  <article><span>已填</span><strong>{attributeSummary?.filled ?? "--"}</strong></article>
                  <article className={missingAttributes.length ? "warn" : ""}><span>缺必填</span><strong>{attributeSummary?.missing_required ?? missingAttributes.length}</strong></article>
                  <article><span>估算</span><strong>{attributeSummary?.estimated ?? "--"}</strong></article>
                  <article><span>未知</span><strong>{attributeSummary?.unknown ?? "--"}</strong></article>
                </div>
                {missingAttributes.length > 0 && (
                  <div className="missing-attribute-strip">
                    {missingAttributes.slice(0, 8).map((item) => (
                      <em key={String(item.attribute_id || item.attribute_name)}>{item.attribute_name_zh || item.attribute_name || item.attribute_id}</em>
                    ))}
                  </div>
                )}
                <div className="attribute-list">
                  {visibleAttributes.map((item) => {
                    const value = attributeValueText(item.value);
                    const status = item.validation_status || (value === "未填" ? "unknown" : "filled");
                    return (
                      <article key={String(item.attribute_id || item.attribute_name)} className={cn(item.required && value === "未填" && "warn")}>
                        <div>
                          <strong>{item.attribute_name_zh || item.attribute_name || item.attribute_id}</strong>
                          <span>{item.required ? "必填" : "可选"} · {status} · {item.source || "unknown"}</span>
                        </div>
                        <p>{value}</p>
                      </article>
                    );
                  })}
                  {!visibleAttributes.length && <p className="drawer-empty">暂无类目属性数据</p>}
                </div>
              </section>

              {detail?.pending_question && Object.keys(detail.pending_question).length > 0 && (
                <section ref={bindSection("question")} className={cn("detail-section pending-question-section", focus === "question" && "active-section")}>
                  <h4>需要补充的信息</h4>
                  <div className="pending-question-card">
                    <strong>{detail.pending_question.title || detail.pending_question.question || "商品需要补充信息"}</strong>
                    <p>{detail.pending_question.message || detail.pending_question.question || "请输入补充信息，系统会继续任务。"}</p>
                    <textarea value={answer} onChange={(event) => setAnswer(event.target.value)} maxLength={1000} placeholder="输入后保存，任务可继续执行" />
                    <Button size="sm" onClick={submitAnswer} disabled={!answer.trim() || answerBusy || actionBusy}>保存回答</Button>
                  </div>
                </section>
              )}

              <section className="detail-section analysis-section">
                <h4>商品分析</h4>
                <div className="analysis-summary-grid">
                  <article>
                    <span>产品类型</span>
                    <strong>{detail?.analysis?.product_type || detail?.source?.title_cn || "暂未生成"}</strong>
                  </article>
                  <article>
                    <span>目标用户</span>
                    <strong>{detail?.analysis?.target_customer || "未明确"}</strong>
                  </article>
                  <article>
                    <span>变体判断</span>
                    <strong>{detail?.analysis?.variant_decision || detail?.analysis?.grouping_decision || "按当前类目规则"}</strong>
                  </article>
                </div>
                <div className="analysis-points">
                  <span>核心卖点</span>
                  <div>
                    {sellingPoints.map((point) => <em key={point}>{point}</em>)}
                    {!sellingPoints.length && <small>暂无卖点分析</small>}
                  </div>
                </div>
                {analysisRisks.length > 0 && (
                  <div className="analysis-risk-list">
                    {analysisRisks.map((risk, index) => (
                      <article key={`${risk.area || "risk"}-${index}`}>
                        <span>{risk.level || risk.area || "风险"}</span>
                        <strong>{risk.message || "存在需要注意的商品事实"}</strong>
                      </article>
                    ))}
                  </div>
                )}
              </section>

              <section className="detail-section score-section">
                <h4>上架评分</h4>
                <div className="score-grid">
                  <article className="score-main">
                    <span>综合</span>
                    <strong>{detail?.prelisting_assessment?.overall_score ?? "--"}</strong>
                  </article>
                  <article>
                    <span>利润潜力</span>
                    <strong>{detail?.prelisting_assessment?.profit_potential ?? "--"}</strong>
                  </article>
                  <article>
                    <span>俄罗斯适配</span>
                    <strong>{detail?.prelisting_assessment?.russia_fit ?? "--"}</strong>
                  </article>
                  <article>
                    <span>图片销售力</span>
                    <strong>{detail?.prelisting_assessment?.image_sales_potential ?? "--"}</strong>
                  </article>
                </div>
                {detail?.prelisting_assessment?.advice && <p className="score-advice">{detail.prelisting_assessment.advice}</p>}
              </section>

              <section className="detail-section commerce-copy-section">
                <h4>俄文商品资料</h4>
                <div className="commerce-copy-card">
                  <span>标题</span>
                  <strong>{detail?.content?.title_ru || card?.title_ru || "暂未生成标题"}</strong>
                  <span>简介</span>
                  <p>{detail?.content?.description_ru || "暂未生成简介"}</p>
                  <span>主题标签</span>
                  <div className="tag-strip">
                    {visibleTags.map((tag) => (
                      <em key={tag}>{tag}</em>
                    ))}
                    {!tags.length && <small>暂无主题标签</small>}
                  </div>
                  {tags.length > 12 && (
                    <button type="button" className="tag-toggle" onClick={() => setShowAllTags((value) => !value)}>
                      {showAllTags ? "收起标签" : `展开全部 ${tags.length} 个标签`}
                    </button>
                  )}
                  <span>富内容</span>
                  <div className="rich-content-summary">
                    {richContentKeys.length ? richContentKeys.map((key) => (
                      <em key={key}>{key}</em>
                    )) : <small>暂无 Rich Content 数据</small>}
                  </div>
                </div>
                <div className="copy-edit-panel">
                  <div className="copy-edit-heading">
                    <FileText className="h-4 w-4" />
                    <div>
                      <strong>商品卡文案草稿</strong>
                      <span>可选编辑，不会提交 Ozon，只保存本地商品卡草稿。</span>
                    </div>
                  </div>
                  <label>
                    <span>俄文标题</span>
                    <input
                      value={copyDraft.title_ru}
                      onChange={(event) => setCopyDraft((current) => ({ ...current, title_ru: event.target.value }))}
                      maxLength={500}
                      placeholder="Введите название товара"
                    />
                  </label>
                  <label>
                    <span>俄文简介</span>
                    <textarea
                      value={copyDraft.description_ru}
                      onChange={(event) => setCopyDraft((current) => ({ ...current, description_ru: event.target.value }))}
                      maxLength={6000}
                      placeholder="Введите описание товара"
                    />
                  </label>
                  <label>
                    <span>主题标签</span>
                    <textarea
                      value={copyDraft.tagsText}
                      onChange={(event) => setCopyDraft((current) => ({ ...current, tagsText: event.target.value }))}
                      maxLength={1400}
                      placeholder="#товар&#10;#длядома"
                    />
                  </label>
                  <div className="copy-edit-actions">
                    <span>每行一个标签；保存时自动补 #，后端会继续做合法化。</span>
                    <Button size="sm" onClick={saveCopyDraft} disabled={!detail?.product_id || copyBusy || actionBusy}>
                      <Save className="h-3.5 w-3.5" />
                      保存文案草稿
                    </Button>
                  </div>
                </div>
              </section>

              <section className="detail-section sku-card-section">
                <h4>SKU 商品卡</h4>
                <div className="drawer-sku-list">
                  {(detail?.skus || []).slice(0, 10).map((sku, index) => (
                    <article key={sku.sku_id || sku.offer_id || index}>
                      {sku.image_url ? <img src={assetUrl(sku.image_url)} alt="" /> : <Boxes className="h-5 w-5" />}
                      <div>
                        <span>SKU {String(index + 1).padStart(2, "0")}</span>
                        <strong>{sku.name || sku.title || sku.offer_id || sku.sku_id || "未命名 SKU"}</strong>
                        <small>{sku.offer_id || sku.sku_id || "no offer"} · {money(sku.selling_price_cny ?? sku.price_cny)} · {money(sku.selling_price_rub, "RUB")}</small>
                        <small>{skuBindingLabel(sku)} · 重量 {skuWeightValue(sku, "product_weight", sku.weight_g) || "--"}g · 容量 {sku.capacity_ml || "--"}ml · 尺寸 {dimensionStringFromDraft(measurementDraftValue(sku))}</small>
                        <div className="sku-fact-chips">
                          <em>{sku.variant_decision || "按Ozon变体规则"}</em>
                          <em>{sku.aspect_basis || "按类目is_aspect规则"}</em>
                        </div>
                        {sku.sku_row?.dynamic_attributes && (
                          <div className="sku-dynamic-attrs">
                            {Object.entries(sku.sku_row.dynamic_attributes).slice(0, 4).map(([attributeId, value]) => (
                              <span key={attributeId}>
                                <b>{dynamicAttributeName(value)}</b>
                                {dynamicAttributeText(value)}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    </article>
                  ))}
                  {!detail?.skus?.length && <p className="drawer-empty">暂无 SKU 明细</p>}
                </div>
              </section>

              <section className="detail-section pricing-section">
                <h4>价格与利润</h4>
                <div className="pricing-grid">
                  {(detail?.skus || []).slice(0, 6).map((sku, index) => (
                    <article key={sku.sku_id || index}>
                      <span>{sku.name || sku.sku_id || `SKU ${index + 1}`}</span>
                      <strong>{money(sku.selling_price_cny ?? sku.price_cny)} / {money(sku.selling_price_rub, "RUB")}</strong>
                      <small>采购 {money(sku.purchase_price_cny)} · 利润 {money(sku.profit_cny)} · 毛利 {percent(sku.profit_rate)}</small>
                    </article>
                  ))}
                  {!detail?.skus?.length && <p className="drawer-empty">暂无价格数据</p>}
                </div>
                <div className="price-edit-panel">
                  <div className="copy-edit-heading">
                    <Save className="h-4 w-4" />
                    <div>
                      <strong>SKU 售价草稿</strong>
                      <span>只保存人民币售价草稿；卢布价由本地工作台按现有汇率和规则换算。</span>
                    </div>
                  </div>
                  <div className="price-edit-grid">
                    {(detail?.skus || []).slice(0, 10).map((sku, index) => {
                      const key = skuDraftKey(sku);
                      return (
                        <label key={key || sku.offer_id || index}>
                          <span>{sku.name || sku.offer_id || sku.sku_id || `SKU ${index + 1}`}</span>
                          <input
                            value={key ? pricingDraft[key] || "" : ""}
                            onChange={(event) => key && setPricingDraft((current) => ({ ...current, [key]: event.target.value }))}
                            inputMode="decimal"
                            placeholder="人民币售价"
                            disabled={!key || actionBusy || pricingBusy}
                          />
                        </label>
                      );
                    })}
                  </div>
                  <div className="copy-edit-actions">
                    <span>{pricingDraftValid ? "保存后会进入商品卡草稿，不会直接上传。" : "售价必须是大于 0 的数字。"}</span>
                    <Button
                      size="sm"
                      onClick={savePricingDraft}
                      disabled={!detail?.product_id || !hasPricingDraft || !pricingDraftValid || pricingBusy || actionBusy}
                    >
                      <Save className="h-3.5 w-3.5" />
                      保存价格草稿
                    </Button>
                  </div>
                </div>
                <div className="measurement-edit-panel">
                  <div className="copy-edit-heading">
                    <Save className="h-4 w-4" />
                    <div>
                      <strong>重量尺寸草稿</strong>
                      <span>按 SKU 保存商品重量、商品尺寸、包装重量和包装尺寸；只改本地草稿，不覆盖采集原始数据。</span>
                    </div>
                  </div>
                  <div className="measurement-edit-grid">
                    {(detail?.skus || []).slice(0, 10).map((sku, index) => {
                      const key = skuDraftKey(sku);
                      const value = key ? measurementDraft[key] : undefined;
                      return (
                        <article key={key || sku.offer_id || index}>
                          <strong>{sku.name || sku.offer_id || sku.sku_id || `SKU ${index + 1}`}</strong>
                          <div className="measurement-fields">
                            <label><span>商品重(g)</span><input value={value?.product_weight_g || ""} inputMode="decimal" onChange={(event) => key && setMeasurementDraft((current) => ({ ...current, [key]: { ...current[key], product_weight_g: event.target.value } }))} disabled={!key || actionBusy || measurementBusy || submittedReadOnly} /></label>
                            <label><span>长(cm)</span><input value={value?.product_length_cm || ""} inputMode="decimal" onChange={(event) => key && setMeasurementDraft((current) => ({ ...current, [key]: { ...current[key], product_length_cm: event.target.value } }))} disabled={!key || actionBusy || measurementBusy || submittedReadOnly} /></label>
                            <label><span>宽(cm)</span><input value={value?.product_width_cm || ""} inputMode="decimal" onChange={(event) => key && setMeasurementDraft((current) => ({ ...current, [key]: { ...current[key], product_width_cm: event.target.value } }))} disabled={!key || actionBusy || measurementBusy || submittedReadOnly} /></label>
                            <label><span>高(cm)</span><input value={value?.product_height_cm || ""} inputMode="decimal" onChange={(event) => key && setMeasurementDraft((current) => ({ ...current, [key]: { ...current[key], product_height_cm: event.target.value } }))} disabled={!key || actionBusy || measurementBusy || submittedReadOnly} /></label>
                            <label><span>包装重(g)</span><input value={value?.package_weight_g || ""} inputMode="decimal" onChange={(event) => key && setMeasurementDraft((current) => ({ ...current, [key]: { ...current[key], package_weight_g: event.target.value } }))} disabled={!key || actionBusy || measurementBusy || submittedReadOnly} /></label>
                            <label><span>包装长</span><input value={value?.package_length_cm || ""} inputMode="decimal" onChange={(event) => key && setMeasurementDraft((current) => ({ ...current, [key]: { ...current[key], package_length_cm: event.target.value } }))} disabled={!key || actionBusy || measurementBusy || submittedReadOnly} /></label>
                            <label><span>包装宽</span><input value={value?.package_width_cm || ""} inputMode="decimal" onChange={(event) => key && setMeasurementDraft((current) => ({ ...current, [key]: { ...current[key], package_width_cm: event.target.value } }))} disabled={!key || actionBusy || measurementBusy || submittedReadOnly} /></label>
                            <label><span>包装高</span><input value={value?.package_height_cm || ""} inputMode="decimal" onChange={(event) => key && setMeasurementDraft((current) => ({ ...current, [key]: { ...current[key], package_height_cm: event.target.value } }))} disabled={!key || actionBusy || measurementBusy || submittedReadOnly} /></label>
                          </div>
                        </article>
                      );
                    })}
                  </div>
                  <div className="copy-edit-actions">
                    <span>{measurementDraftValid ? "包装重量和长宽高必须大于商品本体；留空字段不保存。" : "重量尺寸必须大于 0，且包装必须大于商品本体。"}</span>
                    <Button
                      size="sm"
                      onClick={saveMeasurementDraft}
                      disabled={!detail?.product_id || submittedReadOnly || !hasMeasurementDraft || !measurementDraftValid || measurementBusy || actionBusy}
                    >
                      <Save className="h-3.5 w-3.5" />
                      保存重量尺寸草稿
                    </Button>
                  </div>
                </div>
              </section>

              <section className="detail-section">
                <h4>SKU 图绑定状态</h4>
                <SkuImageBindingPanel detail={detail} busyKey={bindingBusyKey} onBind={bindSku} />
              </section>

              <section className="detail-section">
                <div className="section-title-row">
                  <h4>图片资产区</h4>
                  {detail?.product_id && (
                    <Button size="sm" variant="secondary" asChild>
                      <a href={exportImagesUrl(detail.product_id)}>
                        <Download className="h-3.5 w-3.5" />
                        导出图片包
                      </a>
                    </Button>
                  )}
                </div>
                <div className="visual-preference-panel">
                  <label>
                    <span>整套图片风格意见</span>
                    <input
                      value={visualHint}
                      onChange={(event) => setVisualHint(event.target.value)}
                      maxLength={120}
                      placeholder="例如：商品更大、背景更真实、减少文字块、增加俄罗斯生活场景"
                    />
                  </label>
                  <Button size="sm" variant="secondary" onClick={saveVisualHint} disabled={!detail?.product_id || submittedReadOnly || visualBusy || actionBusy}>
                    <Save className="h-3.5 w-3.5" />
                    应用到图片方案
                  </Button>
                </div>
                <ProductImageGallery
                  detail={detail}
                  actionBusy={actionBusy || submittedReadOnly}
                  onRegenerateSlot={onRegenerateImageSlot}
                  onReplaceImage={onReplaceImage}
                  onImageAction={onImageAction}
                />
              </section>

              <section className="detail-section">
                <h4>AI建议区域</h4>
                <div className="suggestion-list">
                  {suggestions.length ? suggestions.slice(0, 5).map((item, index) => (
                    <article key={item.id || index}>
                      <Sparkles className="h-4 w-4" />
                      <div>
                        <strong>{item.title || item.category || "AI 建议"}</strong>
                        <p>{item.message || item.detail || "建议内容待展示"}</p>
                      </div>
                      <Badge variant="muted">{item.status || "open"}</Badge>
                      {item.id && (
                        <div className="suggestion-actions">
                          <Button size="sm" variant="secondary" disabled={Boolean(suggestionBusy) || actionBusy} onClick={() => actOnSuggestion(item.id!, "accept")}>接受</Button>
                          <Button size="sm" variant="ghost" disabled={Boolean(suggestionBusy) || actionBusy} onClick={() => actOnSuggestion(item.id!, "ignore")}>忽略</Button>
                        </div>
                      )}
                    </article>
                  )) : <p className="drawer-empty">暂无 AI 建议</p>}
                </div>
              </section>

              <section className={cn("detail-section", focus === "risks" && "active-section")}>
                <h4>风险区域</h4>
                <div className="risk-list">
                  {riskItems.length ? riskItems.slice(0, 6).map((item, index) => (
                    <article key={`${item.title}-${index}`}>
                      <AlertTriangle className="h-4 w-4" />
                      <div>
                        <strong>{item.title || item.level || "风险项"}</strong>
                        <p>{item.message || "该商品存在风险提示"}</p>
                      </div>
                    </article>
                  )) : <p className="drawer-empty">当前商品暂无风险项</p>}
                </div>
              </section>

              <section className={cn("detail-section", focus === "timeline" && "active-section")}>
                <h4>生产记录</h4>
                <div className="drawer-timeline">
                  {sortedLogs.map((entry, index) => (
                    <article className={timelineEventKind(entry)} key={`${entry.at}-${index}`}>
                      <time>{formatTime(entry.at)}</time>
                      <strong>{entry.message}</strong>
                      <span>{timelineEventKind(entry) === "user" ? "用户操作" : entry.step || entry.status || detail?.product_id || "系统"}</span>
                    </article>
                  ))}
                  {!sortedLogs.length && <p className="drawer-empty">暂无商品事件</p>}
                </div>
              </section>

              <section className="detail-section danger-zone-section">
                <h4>本地资料操作</h4>
                <div className="danger-zone-actions">
                  <Button variant="danger" onClick={() => setDeleteOpen(true)} disabled={!detail?.product_id || actionBusy}>
                    <Trash2 className="h-4 w-4" />
                    删除本地商品
                  </Button>
                </div>
                <p>删除只影响本地资料，不删除 Ozon 后台已提交商品。</p>
              </section>
            </div>
          </ScrollArea>
        </motion.div>
        <CategoryChangeDialog
          open={categoryOpen}
          onOpenChange={setCategoryOpen}
          detail={detail}
          onChanged={onProductChanged}
        />
        <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>删除本地商品资料</DialogTitle>
              <DialogDescription>
                这会彻底删除 {detail?.product_id} 的本地资料；不会删除 Ozon 后台商品。
              </DialogDescription>
            </DialogHeader>
            <div className="delete-product-warning">
              <p>如果商品已经提交到 Ozon，远端 task、offer 和商品卡不会被删除。删除后本地工作台不再显示该商品。</p>
              <div className="delete-preview-box">
                {deletePreviewBusy && <span>正在读取本地删除预览...</span>}
                {deletePreviewError && <span className="danger-text">{deletePreviewError}</span>}
                {deletePreview && (
                  <>
                    <strong>{deletePreview.title || detail?.source?.title_cn || detail?.product_id}</strong>
                    <span>本地状态：{deletePreview.public_state || deletePreview.status || "unknown"} · SKU {deletePreview.sku_count ?? "--"}</span>
                    <span>{deletePreview.submitted_to_ozon ? "已发现 Ozon 提交记录，远端不会被删除。" : "未发现 Ozon 提交记录。"}</span>
                    <span>关联店铺：{deletePreview.associated_shops?.length ? deletePreview.associated_shops.join(", ") : "无"}</span>
                    <span>Task：{deletePreview.remote_ids?.task_ids?.length ? deletePreview.remote_ids.task_ids.join(", ") : "--"}</span>
                  </>
                )}
              </div>
            </div>
            <div className="delete-product-actions">
              <Button variant="secondary" onClick={() => setDeleteOpen(false)}>取消</Button>
              <Button
                variant="danger"
                onClick={async () => {
                  await onDeleteProduct();
                  setDeleteOpen(false);
                  onOpenChange(false);
                }}
                disabled={actionBusy}
              >
                确认只删除本地资料
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </SheetContent>
    </Sheet>
  );
}
