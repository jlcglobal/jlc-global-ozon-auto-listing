import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AttentionPanel } from "@/components/attention/AttentionPanel";
import { CommandBar } from "@/components/command-center/CommandBar";
import { OzonOptimizationStage } from "@/components/command-center/OzonOptimizationStage";
import { PipelinePanel } from "@/components/command-center/PipelinePanel";
import { ProductionStage } from "@/components/command-center/ProductionStage";
import { ProductionTimeline } from "@/components/command-center/ProductionTimeline";
import { TelemetryPanel } from "@/components/command-center/TelemetryPanel";
import { ProductDetailDrawer, type ProductDrawerFocus } from "@/components/product/ProductDetailDrawer";
import { FinanceCenterDialog } from "@/components/finance/FinanceCenterDialog";
import { BatchConfirmationDialog } from "@/components/production/BatchConfirmationDialog";
import { BatchDetailDialog } from "@/components/production/BatchDetailDialog";
import { BatchLauncherDrawer } from "@/components/production/BatchLauncherDrawer";
import { OzonReferenceLauncherDrawer } from "@/components/production/OzonReferenceLauncherDrawer";
import { TaskCenterDrawer } from "@/components/production/TaskCenterDrawer";
import { StoreManagerDialog } from "@/components/settings/StoreManagerDialog";
import { useBatches } from "@/hooks/useBatches";
import { useLogs } from "@/hooks/useLogs";
import { useProducts } from "@/hooks/useProducts";
import { useSearchVisibilityPlan } from "@/hooks/useSearchVisibilityPlan";
import { useSystemStatus } from "@/hooks/useSystemStatus";
import { useTrafficPerformancePlan } from "@/hooks/useTrafficPerformancePlan";
import { useWorkbenchSettings } from "@/hooks/useWorkbenchSettings";
import { commandCenterConfig } from "@/config/commandCenterConfig";
import { selectedRegenerationSlot, statusTone } from "@/lib/workbenchFormat";
import type { CommandResult } from "@/lib/workbenchFormat";
import {
  answerProductQuestion,
  applySuggestion,
  bindSkuImage,
  deleteProductImage,
  deleteLocalProduct,
  loadBatchConfirmation,
  regenerateProductImage,
  retryFailedProductStores,
  retryProductStore,
  runProduct,
  saveProductDraft,
  saveProductStores,
  saveVisualPreference,
  stopRunningBatch,
  replaceProductImage,
  refreshProductOzonStatus,
  updateProductImage,
} from "@/services/workbenchApi";
import type {
  BatchCard,
  BatchConfirmationResponse,
  CreateBatchResponse,
  CreateOzonReferenceTasksResponse,
  ProductDraftPayload,
  ProductCard,
  ProductDetail,
  OzonReferenceManualInputs,
  OzonReferenceTask,
  SearchVisibilityAction,
} from "@/types/workbench";

const SUBMITTED_OR_DONE_STATUSES = new Set(["HANDED_OFF_TO_OZON", "PENDING_REMOTE", "OZON_MODERATION", "UPLOADED", "ACTIVE", "CREATED", "COMPLETE", "COMPLETED"]);
const ATTENTION_STATUSES = new Set(["FAILED", "NEEDS_ATTENTION", "STOPPED", "PARTIAL", "PARTIAL_FAILED"]);
const ACTIVE_PRODUCTION_STATUSES = new Set(["PROCESSING", "RUNNING", "UPLOADING", "WAITING_FOR_AI_SERVICE"]);
const QUEUED_PRODUCTION_STATUSES = new Set(["QUEUED", "READY"]);
const MANUAL_INPUT_STATUSES = new Set(["COLLECTED", "WAITING_MANUAL_REVIEW", "NEEDS_ATTENTION", "STOPPED", "PARTIAL", "PARTIAL_FAILED"]);

function isInboxProduct(product: { raw_status?: string; workflow_bucket?: string; current_step?: string }) {
  const status = String(product.raw_status || "").toUpperCase();
  const bucket = String(product.workflow_bucket || "");
  const step = String(product.current_step || "");
  return status === "COLLECTED" || bucket.includes("采集箱") || step === "collect_source";
}

function isActiveProductionProduct(product: { raw_status?: string; workflow_bucket?: string; current_step?: string; progress?: number }) {
  const status = String(product.raw_status || "").toUpperCase();
  const bucket = String(product.workflow_bucket || "");
  const step = String(product.current_step || "").toLowerCase();
  if (ATTENTION_STATUSES.has(status)) return false;
  if (QUEUED_PRODUCTION_STATUSES.has(status) || step === "queue") return false;
  if (SUBMITTED_OR_DONE_STATUSES.has(status) || statusTone(status) === "ok") return false;
  return ACTIVE_PRODUCTION_STATUSES.has(status)
    || bucket.includes("生成中")
    || bucket.includes("生产中")
    || (step !== "queue" && Number(product.progress || 0) > 0 && Number(product.progress || 0) < 100);
}

function isQueuedProductionProduct(product: { raw_status?: string; current_step?: string }) {
  const status = String(product.raw_status || "").toUpperCase();
  const step = String(product.current_step || "").toLowerCase();
  return QUEUED_PRODUCTION_STATUSES.has(status) || step === "queue";
}

function detailStatus(detail?: ProductDetail | null, card?: ProductCard) {
  return String(detail?.status?.status || detail?.raw_status || card?.raw_status || "").toUpperCase();
}

function selectedStoreCount(detail?: ProductDetail | null, card?: ProductCard) {
  const stores = detail?.publications?.stores || {};
  const selected = Object.values(stores).filter((record) => record?.selected).length;
  return selected || Number(card?.selected_store_count || 0);
}

function availableStoreCount(detail?: ProductDetail | null) {
  return (detail?.stores || []).filter((store) => Boolean(store.enabled) && store.connection_status === "connected").length;
}

function manualInputPrompt(detail?: ProductDetail | null, card?: ProductCard): null | {
  productId: string;
  focus: ProductDrawerFocus;
  key: string;
  message: string;
} {
  const productId = detail?.product_id || card?.product_id || "";
  if (!productId || !detail) return null;
  const status = detailStatus(detail, card);
  if (SUBMITTED_OR_DONE_STATUSES.has(status) || ACTIVE_PRODUCTION_STATUSES.has(status) || QUEUED_PRODUCTION_STATUSES.has(status)) return null;
  if (detail.pending_question && Object.keys(detail.pending_question).length > 0) {
    return {
      productId,
      focus: "question",
      key: `${productId}:question:${detail.pending_question.question_id || detail.pending_question.field || status}`,
      message: "这件商品缺一个回答，我已经打开填写页；保存后会自动继续任务。",
    };
  }
  const errorText = `${detail.error?.title || ""} ${detail.error?.message || ""}`.toLowerCase();
  const shouldPickStore = (
    MANUAL_INPUT_STATUSES.has(status)
    && availableStoreCount(detail) > 0
    && selectedStoreCount(detail, card) <= 0
  ) || errorText.includes("店铺") || errorText.includes("store");
  if (shouldPickStore) {
    return {
      productId,
      focus: "stores",
      key: `${productId}:stores:${status}:${selectedStoreCount(detail, card)}`,
      message: "这件商品缺目标店铺，我已经打开填写页；保存后会自动继续任务。",
    };
  }
  return null;
}

function shouldTrackOzonReferenceTask(task: OzonReferenceTask) {
  const raw = String(task.status || "").toLowerCase();
  return !task.created_product_id
    && !task.missing_fields?.length
    && ["queued", "processing", "waiting_adapter", "captured", "waiting_ai_design", "processing_ai_design"].includes(raw);
}

function clearOzonReferenceRouteState() {
  const params = new URLSearchParams(window.location.search);
  params.delete("task_center");
  params.delete("taskCenter");
  params.delete("task_id");
  params.delete("taskId");
  const nextQuery = params.toString();
  window.history.replaceState(null, "", `/command-center${nextQuery ? `?${nextQuery}` : ""}`);
}

function clearCollectionRouteState() {
  const params = new URLSearchParams(window.location.search);
  params.delete("task_center");
  params.delete("taskCenter");
  params.delete("product_id");
  params.delete("productId");
  const nextQuery = params.toString();
  window.history.replaceState(null, "", `/command-center${nextQuery ? `?${nextQuery}` : ""}`);
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

function actualSearchVisibilityTags(action?: SearchVisibilityAction | null) {
  const check = action?.last_upload_status_check;
  const existingTags = (action?.existing_subject_tags || []).flatMap(splitSearchTagText);
  const checkedTags = check?.subject_tag_values?.length
    ? check.subject_tag_values.flatMap(splitSearchTagText)
    : check?.has_subject_tags ? splitSearchTagText(check.subject_tag_sample) : [];
  return [
    ...existingTags,
    ...checkedTags,
  ];
}

function searchVisibilityTagsOnCard(action?: SearchVisibilityAction | null) {
  if (!action) return false;
  const actualKeys = actualSearchVisibilityTags(action).map(searchTagKey).filter(Boolean);
  const suggestedKeys = (action.subject_tags || []).map(searchTagKey).filter(Boolean);
  return Boolean(suggestedKeys.length && suggestedKeys.every((key) => actualKeys.includes(key)));
}

function canUploadSearchVisibilityAction(action?: SearchVisibilityAction | null) {
  if (!action) return false;
  const hasSearchSource = Boolean(
    Number(action.evidence?.totals?.impressions || 0) > 0
    || Number(action.evidence?.reference_totals?.yandex_wordstat_searches || 0) > 0
    || Number(action.evidence?.reference_totals?.seerfar_keyword_mining_search_heat || 0) > 0
    || Number(action.evidence?.reference_totals?.seerfar_keyword_reverse_searches || 0) > 0
    || Number(action.evidence?.reference_totals?.trial_reference_searches || 0) > 0
    || Number(action.evidence?.top_queries?.[0]?.metrics?.impressions || action.evidence?.top_queries?.[0]?.metrics?.search_count || 0) > 0
    || Number(action.evidence?.top_yandex_wordstat?.[0]?.count || action.evidence?.top_yandex_wordstat?.[0]?.metrics?.search_count || 0) > 0
    || Number(action.evidence?.top_seerfar_keyword_mining?.[0]?.count || action.evidence?.top_seerfar_keyword_mining?.[0]?.metrics?.monthly_search_heat || 0) > 0
    || Number(action.evidence?.top_seerfar_keyword_reverse?.[0]?.count || action.evidence?.top_seerfar_keyword_reverse?.[0]?.metrics?.search_count || 0) > 0
    || Number(action.evidence?.top_trial_terms?.[0]?.count || action.evidence?.top_trial_terms?.[0]?.metrics?.search_count || 0) > 0
    || action.evidence?.data_source_status === "search_source"
    || action.evidence?.data_source_status === "trial_source"
    || action.data_source_status === "search_source"
    || action.data_source_status === "trial_source"
  );
  if (!hasSearchSource) return false;
  const canUploadTags = (action.allowed_changes || []).includes("subject_tags")
    && Boolean(action.subject_tag_update_required)
    && Boolean(action.subject_tags?.length)
    && !searchVisibilityTagsOnCard(action);
  const canUploadIntro = (action.allowed_changes || []).includes("intro")
    && Boolean(action.intro_update_available);
  return (canUploadTags || canUploadIntro) && action.last_upload?.status !== "submitted";
}

export default function App() {
  const [selectedProductId, setSelectedProductId] = useState("");
  const [selectedStoreId, setSelectedStoreId] = useState("");
  const [selectedWorkspaceMode, setSelectedWorkspaceMode] = useState<"local" | "ozon">("ozon");
  const [workspaceEntryTab, setWorkspaceEntryTab] = useState<"inbox" | "ozon" | "attention">("ozon");
  const [selectedOzonProductId, setSelectedOzonProductId] = useState("");
  const [ozonDetailOpen, setOzonDetailOpen] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [stopBusy, setStopBusy] = useState(false);
  const [commandResult, setCommandResult] = useState<CommandResult | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerProductId, setDrawerProductId] = useState("");
  const [drawerFocus, setDrawerFocus] = useState<ProductDrawerFocus>("overview");
  const [drawerAutoContinue, setDrawerAutoContinue] = useState(false);
  const [drawerAutoPromptKey, setDrawerAutoPromptKey] = useState("");
  const [dismissedAutoPromptKey, setDismissedAutoPromptKey] = useState("");
  const [batchLauncherOpen, setBatchLauncherOpen] = useState(false);
  const [batchLauncherProductId, setBatchLauncherProductId] = useState("");
  const [taskCenterOpen, setTaskCenterOpen] = useState(false);
  const [taskCenterInitialTab, setTaskCenterInitialTab] = useState("all");
  const [ozonReferenceLauncherOpen, setOzonReferenceLauncherOpen] = useState(false);
  const [ozonReferenceFocusTaskId, setOzonReferenceFocusTaskId] = useState("");
  const [pendingOzonReferenceTaskId, setPendingOzonReferenceTaskId] = useState("");
  const [storeManagerOpen, setStoreManagerOpen] = useState(false);
  const [financeCenterOpen, setFinanceCenterOpen] = useState(false);
  const [batchConfirmationOpen, setBatchConfirmationOpen] = useState(false);
  const [batchConfirmation, setBatchConfirmation] = useState<BatchConfirmationResponse | null>(null);
  const [batchConfirmationLoading, setBatchConfirmationLoading] = useState(false);
  const [batchConfirmationError, setBatchConfirmationError] = useState("");
  const [batchDetailOpen, setBatchDetailOpen] = useState(false);
  const [selectedBatch, setSelectedBatch] = useState<BatchCard | null>(null);
  const { system, error: systemError, refreshSystemStatus } = useSystemStatus();
  const { settings, error: settingsError } = useWorkbenchSettings();
  const {
    batches,
    shops,
    ozonReferenceTasks,
    loading: batchesLoading,
    error: batchesError,
    refreshBatchInputs,
    refreshBatches,
    refreshShops,
    refreshOzonReferenceTasks,
    createBatch,
    createOzonReferenceBatch,
    updateOzonReferenceTask,
    continueOzonReferenceQueue,
  } = useBatches();
  const {
    products,
    risks,
    detail,
    currentProduct,
    error: productError,
    loadingProducts,
    setDetail,
    refreshProducts,
    refreshProductDetail,
  } = useProducts(selectedProductId, setSelectedProductId);
  const { productLogs, activity, error: logsError, refreshProductLogs } = useLogs(selectedProductId);
  const {
    plan: searchVisibilityPlan,
    loading: searchVisibilityLoading,
    syncing: searchVisibilitySyncing,
    applying: searchVisibilityApplying,
    importingYandex: searchVisibilityImportingYandex,
    queueingSeerfar: searchVisibilityQueueingSeerfar,
    error: searchVisibilityError,
    refreshSearchVisibilityPlan,
    syncSearchVisibilityPlan,
    applySearchVisibilityAction,
    applySearchVisibilityBatch,
    checkSearchVisibilityUploadStatus,
    importYandexWordstat,
    queueSeerfarKeywordMining,
  } = useSearchVisibilityPlan();
  const {
    plan: trafficPerformancePlan,
    loading: trafficPerformanceLoading,
    error: trafficPerformanceError,
    refreshTrafficPerformancePlan,
  } = useTrafficPerformancePlan();
  const readError = productError || systemError || logsError || batchesError || settingsError;
  const activeProductId = selectedProductId || detail?.product_id || "";
  const activeDetail = detail?.product_id === activeProductId ? detail : null;
  const activeCard = products?.items.find((product) => product.product_id === activeProductId) || currentProduct || undefined;
  const drawerActiveProductId = drawerProductId || activeProductId;
  const drawerDetail = detail?.product_id === drawerActiveProductId ? detail : null;
  const drawerCard = products?.items.find((product) => product.product_id === drawerActiveProductId) || undefined;
  const inboxProducts = useMemo(() => (products?.items || []).filter(isInboxProduct), [products?.items]);
  const runningOzonReferenceTaskId = useMemo(() => {
    return (ozonReferenceTasks?.items || []).find(shouldTrackOzonReferenceTask)?.task_id || "";
  }, [ozonReferenceTasks?.items]);
  const selectedShop = shops?.items.find((shop) => shop.id === selectedStoreId);
  const selectedOzonAction = useMemo(() => {
    const actions = searchVisibilityPlan?.actions || [];
    return actions.find((action) => action.product_id === selectedOzonProductId) || null;
  }, [searchVisibilityPlan?.actions, selectedOzonProductId]);

  useEffect(() => {
    if (!selectedOzonProductId) {
      if (ozonDetailOpen) setOzonDetailOpen(false);
      return;
    }
    const actions = searchVisibilityPlan?.actions || [];
    if (actions.length && !actions.some((action) => action.product_id === selectedOzonProductId)) {
      setSelectedOzonProductId("");
      setOzonDetailOpen(false);
    }
  }, [ozonDetailOpen, searchVisibilityPlan?.actions, selectedOzonProductId]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const path = window.location.pathname;
    const target = params.get("task_center") || params.get("taskCenter") || "";
    const capturedProductId = params.get("product_id") || params.get("productId") || "";
    if (path.includes("1688-collection") || target === "inbox") {
      setSelectedWorkspaceMode("local");
      setWorkspaceEntryTab("inbox");
      if (capturedProductId) {
        setSelectedProductId(capturedProductId);
        setDetail(null);
        // A capture route is a one-time handoff. Clear it immediately so a
        // browser refresh cannot reopen the store picker for an already
        // running or submitted product.
        clearCollectionRouteState();
        refreshProductDetail(capturedProductId)
          .then((capturedDetail) => {
            const prompt = manualInputPrompt(capturedDetail);
            if (prompt?.focus === "stores") {
              setBatchLauncherProductId(capturedProductId);
              setBatchLauncherOpen(true);
            }
          })
          .catch(() => null);
        refreshProductLogs(capturedProductId).catch(() => null);
        setCommandResult({ tone: "ok", message: `已进入商品 ${capturedProductId}。缺少店铺时会自动打开填写页。` });
      } else {
        setCommandResult({ tone: "idle", message: "已进入采集/生产列表，可选择商品后再启动。" });
      }
      return;
    }
    if (path.includes("ozon-reference") || target === "reference" || target === "ozon-reference") {
      const taskId = params.get("task_id") || params.get("taskId") || "";
      if (taskId) {
        setWorkspaceEntryTab("inbox");
        setSelectedWorkspaceMode("local");
        setPendingOzonReferenceTaskId(taskId);
        setOzonReferenceFocusTaskId(taskId);
        setOzonReferenceLauncherOpen(true);
        refreshOzonReferenceTasks().catch(() => null);
        setCommandResult({ tone: "idle", message: `Ozon参考页已采集：${taskId}，正在进入生产页。` });
        clearOzonReferenceRouteState();
        return;
      }
      setWorkspaceEntryTab("ozon");
      setSelectedWorkspaceMode("ozon");
      setOzonReferenceLauncherOpen(true);
      setCommandResult({
        tone: "ok",
        message: "已进入 Ozon 参考采集入口。",
      });
      return;
    }
    if (!target) return;
    setTaskCenterInitialTab(target === "ozon-reference" ? "reference" : target);
    setTaskCenterOpen(true);
  }, []);

  useEffect(() => {
    if (!pendingOzonReferenceTaskId) return undefined;
    const timer = window.setInterval(() => {
      refreshOzonReferenceTasks().catch(() => null);
    }, 2500);
    const task = (ozonReferenceTasks?.items || []).find((item) => item.task_id === pendingOzonReferenceTaskId);
    if (task?.created_product_id) {
      window.clearInterval(timer);
      const productId = task.created_product_id;
      setPendingOzonReferenceTaskId("");
      setOzonReferenceFocusTaskId("");
      setOzonReferenceLauncherOpen(false);
      setWorkspaceEntryTab("inbox");
      setSelectedWorkspaceMode("local");
      setSelectedProductId(productId);
      setDetail(null);
      refreshProducts().catch(() => null);
      refreshProductDetail(productId).catch(() => null);
      refreshProductLogs(productId).catch(() => null);
      setCommandResult({ tone: "ok", message: `Ozon采集已生成商品草稿 ${productId}，已进入生产页。` });
      return undefined;
    }
    const needsManualInput = Boolean(task && !task.created_product_id && (task.missing_fields?.length || task.status === "failed"));
    if (needsManualInput) {
      window.clearInterval(timer);
      const latestTaskId = (ozonReferenceTasks?.items || [])[0]?.task_id || "";
      setPendingOzonReferenceTaskId("");
      if (task?.task_id === latestTaskId) {
        setOzonReferenceFocusTaskId(task.task_id);
        setOzonReferenceLauncherOpen(true);
        setCommandResult({ tone: "idle", message: "这个 Ozon 采集还缺尺寸、重量、售价或类目，补齐后才能进入生产页。" });
      } else {
        setOzonReferenceFocusTaskId("");
        setOzonReferenceLauncherOpen(false);
        setCommandResult({ tone: "idle", message: "历史 Ozon 参考任务还缺参数，已放在任务中心，不再自动弹出。" });
      }
      return undefined;
    }
    return () => {
      window.clearInterval(timer);
    };
  }, [
    ozonReferenceTasks?.items,
    pendingOzonReferenceTaskId,
    refreshOzonReferenceTasks,
    refreshProductDetail,
    refreshProductLogs,
    refreshProducts,
    setDetail,
  ]);

  useEffect(() => {
    if (!runningOzonReferenceTaskId) return undefined;
    const timer = window.setInterval(() => {
      refreshOzonReferenceTasks().catch(() => null);
    }, 2500);
    return () => {
      window.clearInterval(timer);
    };
  }, [refreshOzonReferenceTasks, runningOzonReferenceTaskId]);

  useEffect(() => {
    if (selectedStoreId || !shops?.items?.length) return;
    const preferred = shops.default_shop
      || shops.items.find((shop) => shop.connection_status === "connected")?.id
      || shops.items[0]?.id
      || "";
    if (preferred) setSelectedStoreId(preferred);
  }, [selectedStoreId, shops?.default_shop, shops?.items]);

  useEffect(() => {
    const liveProduct = products?.items.find(isActiveProductionProduct) || products?.items.find(isQueuedProductionProduct);
    if (!liveProduct) return;
    const selectedStillExists = products?.items.some((product) => product.product_id === selectedProductId);
    if (!selectedProductId || !selectedStillExists) {
      setSelectedProductId(liveProduct.product_id);
      setDetail(null);
      refreshProductDetail(liveProduct.product_id).catch(() => null);
      refreshProductLogs(liveProduct.product_id).catch(() => null);
    }
  }, [products?.items, refreshProductDetail, refreshProductLogs, selectedProductId, setDetail]);

  async function refreshSelectedProduct(productId = selectedProductId) {
    if (!productId) return;
    await Promise.all([refreshProductDetail(productId), refreshProductLogs(productId), refreshProducts()]);
  }

  async function openProductDrawer(
    productId = selectedProductId,
    focus: ProductDrawerFocus = "overview",
    autoContinue = false,
    promptKey = "",
  ) {
    if (!productId) return;
    setSelectedWorkspaceMode("local");
    setSelectedProductId(productId);
    setDrawerProductId(productId);
    setDrawerFocus(focus);
    setDrawerAutoContinue(autoContinue);
    setDrawerAutoPromptKey(promptKey);
    setDetail(null);
    setDrawerOpen(true);
    await refreshSelectedProduct(productId);
  }

  useEffect(() => {
    if (selectedWorkspaceMode !== "local" || drawerOpen || batchLauncherOpen || actionBusy || loadingProducts) return;
    const prompt = manualInputPrompt(activeDetail, activeCard);
    if (!prompt || dismissedAutoPromptKey === prompt.key) return;
    setCommandResult({ tone: "idle", message: prompt.message });
    openProductDrawer(prompt.productId, prompt.focus, true, prompt.key).catch(() => null);
  }, [
    actionBusy,
    activeCard?.product_id,
    activeCard?.raw_status,
    activeCard?.selected_store_count,
    activeDetail?.product_id,
    activeDetail?.pending_question,
    activeDetail?.publications?.stores,
    activeDetail?.status?.status,
    activeDetail?.error?.message,
    batchLauncherOpen,
    dismissedAutoPromptKey,
    drawerOpen,
    loadingProducts,
    selectedWorkspaceMode,
  ]);

  function handleSelectProduct(productId: string) {
    setCommandResult(null);
    setSelectedWorkspaceMode("local");
    setSelectedProductId(productId);
    if (drawerOpen) setDrawerProductId(productId);
    setDetail(null);
    refreshProductDetail(productId).catch(() => null);
    refreshProductLogs(productId).catch(() => null);
  }

  function handleSelectOzonProduct(productId: string) {
    setCommandResult(null);
    setSelectedWorkspaceMode("ozon");
    setSelectedOzonProductId(productId);
    setOzonDetailOpen(true);
  }

  function openBatchLauncher(productId = "") {
    setBatchLauncherProductId(productId);
    setBatchLauncherOpen(true);
    if (productId) handleSelectProduct(productId);
  }

  async function handleRunProduct() {
    const productId = activeProductId;
    if (!productId) return;
    setActionBusy(true);
    setCommandResult(null);
    try {
      const result = await runProduct(productId);
      setCommandResult({ tone: "ok", message: result.message || `任务已提交：${result.status}` });
      await refreshSelectedProduct(productId);
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "继续生产失败" });
    } finally {
      setActionBusy(false);
    }
  }

  async function handleRefreshProductOzonStatus() {
    const productId = activeProductId;
    if (!productId) return;
    setActionBusy(true);
    setCommandResult(null);
    try {
      const result = await refreshProductOzonStatus(productId);
      setCommandResult({
        tone: "ok",
        message: result.notice || `Ozon 状态已只读查询：${result.import_status || result.status || "等待处理"}`,
      });
      await refreshSelectedProduct(productId);
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "Ozon 结果查询失败" });
    } finally {
      setActionBusy(false);
    }
  }

  async function handleRegenerateImage() {
    const productId = activeProductId;
    const slot = selectedRegenerationSlot(detail || null);
    if (!productId || !slot) return;
    setActionBusy(true);
    setCommandResult(null);
    try {
      const result = await regenerateProductImage(productId, slot);
      setCommandResult({ tone: "ok", message: result.message || `图片已加入重生成队列：${result.slot}` });
      await refreshSelectedProduct(productId);
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "图片重生成失败" });
    } finally {
      setActionBusy(false);
    }
  }

  async function handleRegenerateRiskImage(productId: string, slot: string) {
    setActionBusy(true);
    setCommandResult(null);
    try {
      const result = await regenerateProductImage(productId, slot);
      setCommandResult({ tone: "ok", message: result.message || `图片已加入重生成队列：${result.slot}` });
      await refreshSelectedProduct(productId);
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "图片重生成失败" });
    } finally {
      setActionBusy(false);
    }
  }

  async function handleRegenerateImageSlot(slot: string, prompt?: string) {
    const productId = activeProductId;
    if (!productId || !slot) return;
    setActionBusy(true);
    setCommandResult(null);
    try {
      const result = await regenerateProductImage(productId, slot, prompt);
      setCommandResult({ tone: "ok", message: result.message || `图片已加入重生成队列：${result.slot}` });
      await refreshSelectedProduct(productId);
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "图片重生成失败" });
      throw err;
    } finally {
      setActionBusy(false);
    }
  }

  async function handleImageAction(
    slot: string,
    payload:
      | { action: "keep" | "accept" }
      | { action: "move"; direction: "up" | "down" }
      | { action: "set_role"; role: "main" | "detail" | "disclaimer" | "color_sample" }
      | { action: "delete" },
  ) {
    const productId = activeProductId;
    if (!productId || !slot) return;
    setActionBusy(true);
    setCommandResult(null);
    try {
      if (payload.action === "delete") {
        const result = await deleteProductImage(productId, slot);
        setCommandResult({ tone: "ok", message: result.message || `图片已拒绝：${result.slot}` });
      } else {
        const result = await updateProductImage(productId, slot, payload);
        setCommandResult({ tone: "ok", message: result.message || `图片计划已更新：${result.slot}` });
      }
      await refreshSelectedProduct(productId);
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "图片操作失败" });
      throw err;
    } finally {
      setActionBusy(false);
    }
  }

  async function handleReplaceImage(slot: string, dataUrl: string) {
    const productId = activeProductId;
    if (!productId || !slot || !dataUrl) return;
    setActionBusy(true);
    setCommandResult(null);
    try {
      const result = await replaceProductImage(productId, slot, dataUrl);
      setCommandResult({ tone: "ok", message: result.message || `图片已替换：${result.slot}` });
      await refreshSelectedProduct(productId);
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "图片替换失败" });
      throw err;
    } finally {
      setActionBusy(false);
    }
  }

  async function handleSaveVisualPreference(setHint: string) {
    const productId = activeProductId;
    if (!productId) return;
    setActionBusy(true);
    setCommandResult(null);
    try {
      const result = await saveVisualPreference(productId, { set_hint: setHint });
      const invalidated = result.invalidated_steps?.length || 0;
      setCommandResult({
        tone: "ok",
        message: invalidated ? `图片风格意见已保存，${invalidated} 个旧步骤会在继续生产时重做` : "图片风格意见已保存",
      });
      await refreshSelectedProduct(productId);
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "图片风格意见保存失败" });
      throw err;
    } finally {
      setActionBusy(false);
    }
  }

  async function handleBatchCreated(result: CreateBatchResponse) {
    const okStatuses = ["started", "queued"];
    setCommandResult({
      tone: okStatuses.includes(result.status) ? "ok" : "idle",
      message: result.message || (result.batch_id ? `批次 ${result.batch_id}：${result.status}` : `批次创建结果：${result.status}`),
    });
    const productId = batchLauncherProductId || selectedProductId;
    await Promise.all([
      refreshProducts(),
      productId ? refreshProductDetail(productId) : Promise.resolve(null),
      productId ? refreshProductLogs(productId) : Promise.resolve(null),
    ]).catch(() => null);
  }

  async function handleOzonReferenceCreated(result: CreateOzonReferenceTasksResponse) {
    setCommandResult({
      tone: result.created_count > 0 ? "ok" : "idle",
      message: result.message || `Ozon参考任务：${result.status}`,
    });
  }

  async function handleOzonReferenceUpdated(taskId: string, manualInputs: OzonReferenceManualInputs, storeIds: string[]) {
    const result = await updateOzonReferenceTask(taskId, manualInputs, storeIds);
    const nextTaskId = result.task?.task_id || taskId;
    if (nextTaskId) {
      setPendingOzonReferenceTaskId(nextTaskId);
      setOzonReferenceFocusTaskId(nextTaskId);
    }
    setCommandResult({
      tone: result.task?.created_product_id ? "ok" : "idle",
      message: result.message || "参数已保存，Ozon参考任务已继续，底部会显示进度。",
    });
    return result;
  }

  async function handleContinueOzonReferenceQueue() {
    const result = await continueOzonReferenceQueue();
    setCommandResult({ tone: "ok", message: result.message || "Ozon参考队列已继续。" });
    return result;
  }

  async function handleOpenBatchConfirmation(batchId: string) {
    setBatchConfirmationOpen(true);
    setBatchConfirmation(null);
    setBatchConfirmationError("");
    setBatchConfirmationLoading(true);
    try {
      const result = await loadBatchConfirmation(batchId);
      setBatchConfirmation(result);
    } catch (err) {
      setBatchConfirmationError(err instanceof Error ? err.message : "批次确认资料读取失败");
    } finally {
      setBatchConfirmationLoading(false);
    }
  }

  function handleOpenBatch(batch: BatchCard) {
    setSelectedBatch(batch);
    setBatchDetailOpen(true);
  }

  async function handleBindSkuImage(skuId: string, selectedImagePath: string) {
    const productId = activeProductId;
    if (!productId) return;
    setActionBusy(true);
    setCommandResult(null);
    try {
      const result = await bindSkuImage(productId, skuId, selectedImagePath);
      setCommandResult({ tone: "ok", message: result.message || "SKU参考图已绑定" });
      await refreshSelectedProduct(productId);
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "SKU图绑定失败" });
      throw err;
    } finally {
      setActionBusy(false);
    }
  }

  async function handleProductChanged(message: string) {
    const productId = activeProductId;
    setCommandResult({ tone: "ok", message });
    if (productId) await refreshSelectedProduct(productId);
  }

  async function handleSaveStores(storeIds: string[]) {
    const productId = activeProductId;
    if (!productId) return;
    setActionBusy(true);
    setCommandResult(null);
    try {
      const result = await saveProductStores(productId, storeIds);
      setCommandResult({ tone: "ok", message: `已保存 ${result.store_ids.length} 家目标店铺` });
      await refreshSelectedProduct(productId);
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "目标店铺保存失败" });
      throw err;
    } finally {
      setActionBusy(false);
    }
  }

  async function handleRetryStore(storeId: string) {
    const productId = activeProductId;
    if (!productId || !storeId) return;
    setActionBusy(true);
    setCommandResult(null);
    try {
      const result = await retryProductStore(productId, storeId);
      setCommandResult({ tone: "ok", message: result.message || `失败店铺已进入重试批次：${result.batch_id || result.status}` });
      await Promise.all([refreshSelectedProduct(productId), refreshBatches()]).catch(() => null);
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "店铺重试失败" });
      throw err;
    } finally {
      setActionBusy(false);
    }
  }

  async function handleRetryFailedStores(storeIds: string[]) {
    const productId = activeProductId;
    if (!productId || !storeIds.length) return;
    setActionBusy(true);
    setCommandResult(null);
    try {
      const result = await retryFailedProductStores(productId, storeIds);
      const count = result.store_ids?.length || storeIds.length;
      setCommandResult({ tone: "ok", message: result.message || `${count} 家失败店铺已进入重试批次：${result.batch_id || result.status}` });
      await Promise.all([refreshSelectedProduct(productId), refreshBatches()]).catch(() => null);
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "失败店铺批量重试失败" });
      throw err;
    } finally {
      setActionBusy(false);
    }
  }

  async function handleSuggestionAction(suggestionId: string, action: "accept" | "ignore" | "mute_similar") {
    const productId = activeProductId;
    if (!productId) return;
    setActionBusy(true);
    setCommandResult(null);
    try {
      const result = await applySuggestion(productId, suggestionId, action);
      setCommandResult({ tone: "ok", message: `AI建议已处理：${result.action}` });
      await refreshSelectedProduct(productId);
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "AI建议处理失败" });
      throw err;
    } finally {
      setActionBusy(false);
    }
  }

  async function handleAnswerQuestion(answer: string) {
    const productId = activeProductId;
    if (!productId) return;
    setActionBusy(true);
    setCommandResult(null);
    try {
      const result = await answerProductQuestion(productId, answer);
      setCommandResult({ tone: "ok", message: result.next_action === "run" ? "回答已保存，可以继续生产" : "回答已保存" });
      await refreshSelectedProduct(productId);
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "回答保存失败" });
      throw err;
    } finally {
      setActionBusy(false);
    }
  }

  async function handleSaveProductDraft(payload: ProductDraftPayload) {
    const productId = activeProductId;
    if (!productId) return;
    setActionBusy(true);
    setCommandResult(null);
    try {
      const result = await saveProductDraft(productId, payload);
      setCommandResult({ tone: "ok", message: `商品卡草稿已保存：v${result.version}` });
      await refreshSelectedProduct(productId);
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "商品卡草稿保存失败" });
      throw err;
    } finally {
      setActionBusy(false);
    }
  }

  async function handleDeleteProduct() {
    const productId = activeProductId;
    if (!productId) return;
    setActionBusy(true);
    setCommandResult(null);
    try {
      const result = await deleteLocalProduct(productId);
      setCommandResult({ tone: "ok", message: result.message || `本地商品 ${productId} 已删除` });
      setSelectedProductId("");
      setDrawerProductId("");
      await refreshProducts();
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "本地商品删除失败" });
      throw err;
    } finally {
      setActionBusy(false);
    }
  }

  function handleStoreResult(message: string, tone: "ok" | "danger" | "idle" = "idle") {
    setCommandResult({ tone, message });
  }

  async function refreshTaskCenter() {
    await Promise.all([
      refreshProducts(),
      refreshBatchInputs(),
      refreshSearchVisibilityPlan(),
      refreshTrafficPerformancePlan(),
      activeProductId ? refreshProductLogs(activeProductId) : Promise.resolve(null),
    ]);
  }

  async function handleSyncSearchVisibility(storeId = selectedStoreId) {
    setCommandResult(null);
    try {
      const result = await syncSearchVisibilityPlan({
        store_id: storeId || undefined,
        product_limit: 0,
        period_days: 15,
      });
      setCommandResult({
        tone: result.available ? "ok" : "idle",
        message: result.notice || "Ozon 商品信息读取完成",
      });
      const selectedStillExists = (result.actions || []).some((action) => action.product_id === selectedOzonProductId);
      if (!selectedStillExists || storeId !== searchVisibilityPlan?.shop_id) {
        setSelectedOzonProductId("");
      }
      setOzonDetailOpen(false);
      await refreshSearchVisibilityPlan().catch(() => null);
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "Ozon 搜索词读取失败" });
      throw err;
    }
  }

  async function handleApplySearchVisibility(productId = selectedOzonAction?.product_id || "") {
    if (!productId) return;
    setCommandResult(null);
    try {
      const result = await applySearchVisibilityAction({
        store_id: selectedStoreId || searchVisibilityPlan?.shop_id || undefined,
        product_id: productId,
      });
      setCommandResult({
        tone: "ok",
        message: result.notice || "主题标签已上传",
      });
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "主题标签上传失败" });
      throw err;
    }
  }

  async function handleCheckSearchVisibilityUploadStatus(productId = selectedOzonAction?.product_id || "") {
    if (!productId) return;
    setCommandResult(null);
    try {
      const result = await checkSearchVisibilityUploadStatus({
        store_id: selectedStoreId || searchVisibilityPlan?.shop_id || undefined,
        product_id: productId,
      });
      setCommandResult({
        tone: result.status === "verified" ? "ok" : "idle",
        message: result.notice || `Ozon 处理状态：${result.import_status || result.status}`,
      });
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "Ozon 结果查询失败" });
      throw err;
    }
  }

  async function handleApplyAllSearchVisibility() {
    const eligibleCount = (searchVisibilityPlan?.actions || []).filter(canUploadSearchVisibilityAction).length;
    if (!eligibleCount) {
      setCommandResult({ tone: "idle", message: "当前店铺没有可上传的新主题标签或简介" });
      return;
    }
    const confirmed = window.confirm(`将给当前店铺 ${eligibleCount} 个商品上传主题标签和简介。只改标签/简介，不改标题、不改价格、不动库存。继续吗？`);
    if (!confirmed) return;
    setCommandResult(null);
    try {
      const result = await applySearchVisibilityBatch({
        store_id: selectedStoreId || searchVisibilityPlan?.shop_id || undefined,
        max_products: 1000,
        confirm_upload: true,
      });
      setCommandResult({
        tone: "ok",
        message: result.notice || `已批量上传 ${result.uploaded_product_count || 0} 个商品的主题标签`,
      });
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "批量上传主题标签失败" });
      throw err;
    }
  }

  async function handleImportYandexWordstat(productId: string, text: string) {
    if (!productId) return;
    setCommandResult(null);
    try {
      const result = await importYandexWordstat({
        store_id: selectedStoreId || searchVisibilityPlan?.shop_id || undefined,
        product_id: productId,
        text,
        period_days: 30,
      });
      setSelectedOzonProductId(productId);
      setOzonDetailOpen(true);
      setCommandResult({
        tone: "ok",
        message: result.notice || `已导入 ${result.imported_count || 0} 个 Yandex 参考词`,
      });
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "Yandex 词表导入失败" });
      throw err;
    }
  }

  async function handleQueueSeerfarKeywordMining(productId: string) {
    if (!productId) return;
    setCommandResult(null);
    try {
      const result = await queueSeerfarKeywordMining({
        store_id: selectedStoreId || searchVisibilityPlan?.shop_id || undefined,
        product_id: productId,
      });
      setCommandResult({
        tone: "ok",
        message: result.notice || "已加入 Seerfar 关键词挖掘队列",
      });
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "Seerfar 关键词读取失败" });
      throw err;
    }
  }

  async function handleStopBatch() {
    setStopBusy(true);
    setCommandResult(null);
    try {
      const result = await stopRunningBatch();
      setCommandResult({ tone: "ok", message: result.message || "已请求安全停止当前批次" });
      await Promise.all([refreshSystemStatus(), refreshBatches(), refreshProducts()]).catch(() => null);
    } catch (err) {
      setCommandResult({ tone: "danger", message: err instanceof Error ? err.message : "安全停止请求失败" });
    } finally {
      setStopBusy(false);
    }
  }

  return (
    <TooltipProvider>
      <div className="min-h-screen overflow-auto bg-[#040D0C] text-emerald-50">
        <div className="orb orb-a" />
        <div className="orb orb-b" />
        <div className="grid-noise" />
        <motion.main
          className="command-center"
          data-build-version={commandCenterConfig.buildVersion}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.5 }}
        >
          <CommandBar
            system={system}
            onOpenBatchLauncher={() => openBatchLauncher("")}
            onOpenTaskCenter={() => setTaskCenterOpen(true)}
            onOpenOzonReferenceLauncher={() => setOzonReferenceLauncherOpen(true)}
            onOpenFinanceCenter={() => setFinanceCenterOpen(true)}
            onOpenStoreManager={() => setStoreManagerOpen(true)}
            workspaceMode={selectedWorkspaceMode}
            onSelectWorkspaceMode={(mode) => {
              setSelectedWorkspaceMode(mode);
              setWorkspaceEntryTab(mode === "ozon" ? "ozon" : "inbox");
              setOzonDetailOpen(false);
              if (mode === "ozon") setSelectedOzonProductId("");
            }}
            canStopBatch={Boolean(system?.batch_running || batches?.running_pid)}
            stoppingBatch={stopBusy}
            onStopBatch={handleStopBatch}
          />
          <div className="cockpit-grid">
            <PipelinePanel
              products={products?.items}
              shops={shops?.items}
              entryTab={workspaceEntryTab}
              selectedStoreId={selectedStoreId}
              selectedProductId={selectedProductId}
              selectedOzonProductId={selectedOzonProductId}
              productsLoading={loadingProducts}
              productsError={productError}
              searchVisibilityPlan={searchVisibilityPlan}
              syncingSearchVisibility={searchVisibilitySyncing}
              onSelectStore={(storeId) => {
                setSelectedStoreId(storeId);
                setSelectedOzonProductId("");
                setOzonDetailOpen(false);
                // A full Ozon catalog sync runs separately for every shop.
                // Switching stores must only load its cache, never start a duplicate read.
                void refreshSearchVisibilityPlan(storeId).catch(() => null);
              }}
              onSelectProduct={handleSelectProduct}
              onSelectOzonProduct={handleSelectOzonProduct}
              onLaunchProduct={openBatchLauncher}
              onSyncSearchVisibility={() => handleSyncSearchVisibility(selectedStoreId)}
            />
            {selectedWorkspaceMode === "ozon" ? (
              <OzonOptimizationStage
                action={selectedOzonAction}
                actions={searchVisibilityPlan?.actions || []}
                plan={searchVisibilityPlan}
                shopName={selectedShop?.display_name || selectedStoreId}
                selectedProductId={selectedOzonProductId}
                detailOpen={ozonDetailOpen}
                syncing={searchVisibilitySyncing}
                applying={searchVisibilityApplying}
                importingYandex={searchVisibilityImportingYandex}
                queueingSeerfar={searchVisibilityQueueingSeerfar}
                commandResult={commandResult}
                onSelectAction={handleSelectOzonProduct}
                onBackToList={() => {
                  setOzonDetailOpen(false);
                  setSelectedOzonProductId("");
                }}
                onRefreshSearch={() => handleSyncSearchVisibility(selectedStoreId)}
                onApplyOptimization={() => handleApplySearchVisibility()}
                onApplyAllOptimizations={handleApplyAllSearchVisibility}
                onCheckUploadStatus={() => handleCheckSearchVisibilityUploadStatus()}
                onImportYandexWordstat={handleImportYandexWordstat}
                onQueueSeerfarKeywordMining={handleQueueSeerfarKeywordMining}
              />
            ) : (
              <ProductionStage
                detail={activeDetail}
                card={activeCard}
                products={products?.items}
                error={readError}
                loadingProducts={loadingProducts}
                commandResult={commandResult}
                actionBusy={actionBusy}
                onRunProduct={handleRunProduct}
                onRefreshOzonStatus={handleRefreshProductOzonStatus}
                onRegenerateImage={handleRegenerateImage}
                onOpenDetail={() => openProductDrawer(activeProductId)}
                onSelectProduct={handleSelectProduct}
              />
            )}
            <div className="right-stack">
              <TelemetryPanel
                system={system}
                products={products}
                settings={settings}
                currentDetail={activeDetail}
                currentProduct={activeCard}
                mode={selectedWorkspaceMode}
                searchAction={ozonDetailOpen ? selectedOzonAction : null}
              />
              <AttentionPanel
                risks={risks?.items || []}
                products={products?.items}
                currentDetail={activeDetail}
                onOpenProduct={openProductDrawer}
                onRegenerateImage={handleRegenerateRiskImage}
              />
            </div>
          </div>
          <ProductionTimeline
            mode={selectedWorkspaceMode}
            detail={selectedWorkspaceMode === "local" ? activeDetail : null}
            card={selectedWorkspaceMode === "local" ? activeCard : undefined}
            searchAction={ozonDetailOpen ? selectedOzonAction : null}
            searchPlan={searchVisibilityPlan}
            syncingSearchVisibility={searchVisibilitySyncing}
            ozonReferenceTasks={ozonReferenceTasks}
          />
          <ProductDetailDrawer
            open={drawerOpen}
            onOpenChange={(open) => {
              setDrawerOpen(open);
              if (!open) {
                if (drawerAutoPromptKey) setDismissedAutoPromptKey(drawerAutoPromptKey);
                setDrawerProductId("");
                setDrawerFocus("overview");
                setDrawerAutoContinue(false);
                setDrawerAutoPromptKey("");
              }
            }}
            detail={drawerDetail}
            card={drawerCard}
            logs={productLogs.items}
            actionBusy={actionBusy}
            initialFocus={drawerFocus}
            autoContinueAfterInput={drawerAutoContinue}
            onRunProduct={handleRunProduct}
            onRefreshOzonStatus={handleRefreshProductOzonStatus}
            onRegenerateImage={handleRegenerateImage}
            onRegenerateImageSlot={handleRegenerateImageSlot}
            onImageAction={handleImageAction}
            onReplaceImage={handleReplaceImage}
            onSaveVisualPreference={handleSaveVisualPreference}
            onBindSkuImage={handleBindSkuImage}
            onProductChanged={handleProductChanged}
            onSaveStores={handleSaveStores}
            onRetryStore={handleRetryStore}
            onRetryFailedStores={handleRetryFailedStores}
            onSuggestionAction={handleSuggestionAction}
            onAnswerQuestion={handleAnswerQuestion}
            onSaveProductDraft={handleSaveProductDraft}
            onDeleteProduct={handleDeleteProduct}
          />
          <BatchLauncherDrawer
            open={batchLauncherOpen}
            onOpenChange={(open) => {
              setBatchLauncherOpen(open);
              if (!open) setBatchLauncherProductId("");
            }}
            products={batchLauncherProductId ? products?.items : inboxProducts}
            shops={shops?.items}
            initialProductId={batchLauncherProductId}
            loading={batchesLoading || loadingProducts}
            error={batchesError}
            onRefresh={refreshBatchInputs}
            onCreateBatch={createBatch}
            onCreated={handleBatchCreated}
          />
          <OzonReferenceLauncherDrawer
            open={ozonReferenceLauncherOpen}
            onOpenChange={(open) => {
              setOzonReferenceLauncherOpen(open);
              if (!open) {
                setOzonReferenceFocusTaskId("");
              }
            }}
            focusTaskId={ozonReferenceFocusTaskId}
            shops={shops?.items}
            tasks={ozonReferenceTasks}
            loading={batchesLoading && !ozonReferenceTasks}
            error={batchesError}
            onRefresh={refreshBatchInputs}
            onCreateTasks={createOzonReferenceBatch}
            onUpdateTask={handleOzonReferenceUpdated}
            onContinueQueue={handleContinueOzonReferenceQueue}
            onCreated={handleOzonReferenceCreated}
            onOpenProduct={openProductDrawer}
          />
          <TaskCenterDrawer
            open={taskCenterOpen}
            onOpenChange={setTaskCenterOpen}
            initialTab={taskCenterInitialTab}
            products={products?.items}
            batches={batches?.items}
            ozonReferenceTasks={ozonReferenceTasks}
            searchVisibilityPlan={searchVisibilityPlan}
            trafficPerformancePlan={trafficPerformancePlan}
            loading={batchesLoading || loadingProducts || searchVisibilityLoading || trafficPerformanceLoading}
            syncingSearchVisibility={searchVisibilitySyncing}
            error={readError || searchVisibilityError || trafficPerformanceError}
            onRefresh={refreshTaskCenter}
            onSyncSearchVisibility={handleSyncSearchVisibility}
            onOpenProduct={(productId) => {
              setTaskCenterOpen(false);
              openProductDrawer(productId);
            }}
            onOpenBatch={(batch) => {
              setTaskCenterOpen(false);
              handleOpenBatch(batch);
            }}
            onOpenOzonReferenceLauncher={(taskId) => {
              setTaskCenterOpen(false);
              setOzonReferenceFocusTaskId(taskId);
              setOzonReferenceLauncherOpen(true);
            }}
          />
          <BatchConfirmationDialog
            open={batchConfirmationOpen}
            onOpenChange={setBatchConfirmationOpen}
            data={batchConfirmation}
            loading={batchConfirmationLoading}
            error={batchConfirmationError}
          />
          <BatchDetailDialog
            open={batchDetailOpen}
            onOpenChange={setBatchDetailOpen}
            batch={selectedBatch}
          />
          <StoreManagerDialog
            open={storeManagerOpen}
            onOpenChange={setStoreManagerOpen}
            shops={shops?.items}
            loading={batchesLoading}
            onRefresh={refreshShops}
            onResult={handleStoreResult}
          />
          <FinanceCenterDialog
            open={financeCenterOpen}
            onOpenChange={setFinanceCenterOpen}
            onResult={(message, tone) => setCommandResult({ message, tone })}
          />
        </motion.main>
      </div>
    </TooltipProvider>
  );
}
