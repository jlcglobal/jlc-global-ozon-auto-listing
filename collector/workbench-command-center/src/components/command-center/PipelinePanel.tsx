import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { AlertTriangle, Box, CheckCircle2, ImageIcon, PackageCheck, RefreshCw, Search, Store, UploadCloud } from "lucide-react";
import jlcLogo from "@/assets/jlc-global-logo.png";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { assetUrl } from "@/services/workbenchApi";
import { cn, truncate } from "@/lib/utils";
import { productStepLabel, readableStageName, statusTone } from "@/lib/workbenchFormat";
import type { ProductCard, SearchVisibilityAction, SearchVisibilityPlan, ShopCard } from "@/types/workbench";

type WorkspaceTab = "inbox" | "ozon" | "attention";
const SUBMITTED_OR_DONE_STATUSES = new Set(["HANDED_OFF_TO_OZON", "PENDING_REMOTE", "OZON_MODERATION", "UPLOADED", "ACTIVE", "CREATED", "COMPLETE", "COMPLETED"]);
const ATTENTION_STATUSES = new Set(["FAILED", "NEEDS_ATTENTION", "STOPPED", "PARTIAL", "PARTIAL_FAILED"]);
const WAITING_LOCAL_ACTION_STATUSES = new Set(["WAITING_MANUAL_REVIEW", "READY_FOR_OPERATOR_UPLOAD"]);
const QUEUED_LOCAL_STATUSES = new Set(["QUEUED", "READY"]);

function isInboxProduct(product: ProductCard) {
  const status = String(product.raw_status || "").toUpperCase();
  const bucket = String(product.workflow_bucket || "");
  const step = String(product.current_step || "");
  return status === "COLLECTED" || bucket.includes("采集箱") || step === "collect_source";
}

function isRunningProduct(product: ProductCard) {
  const status = String(product.raw_status || "").toUpperCase();
  const bucket = String(product.workflow_bucket || "");
  const step = String(product.current_step || "").toLowerCase();
  if (ATTENTION_STATUSES.has(status)) return false;
  if (WAITING_LOCAL_ACTION_STATUSES.has(status)) return false;
  if (QUEUED_LOCAL_STATUSES.has(status) || step === "queue") return false;
  if (SUBMITTED_OR_DONE_STATUSES.has(status) || statusTone(status) === "ok") return false;
  return statusTone(status) === "running"
    || bucket.includes("生成中")
    || bucket.includes("生产中")
    || (Number(product.progress || 0) > 0 && Number(product.progress || 0) < 100);
}

function isQueuedProduct(product: ProductCard) {
  const status = String(product.raw_status || "").toUpperCase();
  const step = String(product.current_step || "").toLowerCase();
  return QUEUED_LOCAL_STATUSES.has(status) || step === "queue";
}

function isWaitingLocalAction(product: ProductCard) {
  return WAITING_LOCAL_ACTION_STATUSES.has(String(product.raw_status || "").toUpperCase());
}

function isLocalQueueProduct(product: ProductCard) {
  return isInboxProduct(product) || isRunningProduct(product) || isQueuedProduct(product) || isWaitingLocalAction(product);
}

function isAttentionProduct(product: ProductCard) {
  if (isRunningProduct(product)) return false;
  const status = String(product.raw_status || "").toUpperCase();
  if (SUBMITTED_OR_DONE_STATUSES.has(status)) return false;
  const bucket = String(product.workflow_bucket || "");
  return ["FAILED", "NEEDS_ATTENTION", "STOPPED", "PARTIAL"].includes(status)
    || bucket.includes("需要处理")
    || product.attention_required
    || product.risk?.level === "high";
}

function productStatusLabel(product: ProductCard) {
  if (isRunningProduct(product)) return "处理中";
  if (isQueuedProduct(product)) return "排队中";
  if (isWaitingLocalAction(product)) return "待上传";
  if (isInboxProduct(product)) return "未开始";
  if (isAttentionProduct(product)) return "需要处理";
  const tone = statusTone(product.raw_status);
  if (tone === "running") return "处理中";
  if (tone === "ok") return "已完成";
  return product.state || "待处理";
}

function productBadge(product: ProductCard): "default" | "warning" | "danger" | "muted" {
  if (isRunningProduct(product)) return "warning";
  if (isQueuedProduct(product)) return "muted";
  if (isWaitingLocalAction(product)) return "warning";
  if (isAttentionProduct(product)) return "danger";
  if (isInboxProduct(product)) return "muted";
  const tone = statusTone(product.raw_status);
  if (tone === "running") return "warning";
  if (tone === "ok") return "default";
  return "muted";
}

function metricNumber(value: unknown) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "0";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(number);
}

function topQuery(action: SearchVisibilityAction) {
  return action.evidence?.top_queries?.[0]?.query
    || action.evidence?.top_yandex_wordstat?.[0]?.query
    || action.evidence?.top_trial_terms?.[0]?.query
    || action.title_terms?.[0]
    || action.subject_tags?.[0]
    || "暂无搜索词";
}

function actionSearchCount(action: SearchVisibilityAction) {
  return action.evidence?.totals?.impressions || action.evidence?.top_queries?.[0]?.metrics?.impressions || 0;
}

function actionOzonQueryCount(action: SearchVisibilityAction) {
  return Number(action.evidence?.totals?.query_count || action.evidence?.top_queries?.length || 0);
}

function actionYandexSearchCount(action: SearchVisibilityAction) {
  return action.evidence?.reference_totals?.yandex_wordstat_searches
    || action.evidence?.top_yandex_wordstat?.[0]?.count
    || action.evidence?.top_yandex_wordstat?.[0]?.metrics?.search_count
    || 0;
}

function actionTrialSearchCount(action: SearchVisibilityAction) {
  return action.evidence?.reference_totals?.trial_reference_searches
    || action.evidence?.top_trial_terms?.[0]?.count
    || action.evidence?.top_trial_terms?.[0]?.metrics?.search_count
    || 0;
}

function tagKey(value: unknown) {
  return String(value || "").trim().replace(/^#+/, "").replace(/[\s_#-]+/g, "").toLowerCase();
}

function splitTagText(value: unknown) {
  return String(value || "")
    .split(/[\s,;，；]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function actualProductTags(action: SearchVisibilityAction) {
  const check = action.last_upload_status_check;
  const existingTags = (action.existing_subject_tags || []).flatMap(splitTagText);
  const checkedTags = check?.subject_tag_values?.length
    ? check.subject_tag_values.flatMap(splitTagText)
    : check?.has_subject_tags ? splitTagText(check.subject_tag_sample) : [];
  return [
    ...existingTags,
    ...checkedTags,
  ];
}

function actionAddedToProductCard(action: SearchVisibilityAction, tag?: string) {
  const actualKeys = actualProductTags(action).map(tagKey).filter(Boolean);
  if (!tag) return false;
  const key = tagKey(tag);
  return Boolean(key && actualKeys.includes(key));
}

function actionSearchMetricLabel(action: SearchVisibilityAction) {
  const ozonCount = Number(actionSearchCount(action) || 0);
  if (ozonCount > 0) return `搜索人数 · ${metricNumber(ozonCount)}`;
  const ozonQueryCount = actionOzonQueryCount(action);
  if (ozonQueryCount > 0) return `搜索人数 -- · Ozon词 ${metricNumber(ozonQueryCount)} 个`;
  const yandexCount = Number(actionYandexSearchCount(action) || 0);
  if (yandexCount > 0) return `Yandex搜索量 · ${metricNumber(yandexCount)}`;
  const trialCount = Number(actionTrialSearchCount(action) || 0);
  if (trialCount > 0) return `竞品搜索人数 · ${metricNumber(trialCount)}`;
  const trialQueryCount = Number(action.evidence?.reference_totals?.trial_reference_query_count || action.evidence?.top_trial_terms?.length || 0);
  if (trialQueryCount > 0) return `试错词 · ${metricNumber(trialQueryCount)} 个`;
  return "数据来源 · 待补";
}

function actionBasis(action: SearchVisibilityAction, periodDays = 7) {
  const query = topQuery(action);
  const ozonCount = Number(actionSearchCount(action) || 0);
  if (ozonCount <= 0) {
    const ozonQueryCount = actionOzonQueryCount(action);
    if (ozonQueryCount > 0) {
      return `依据：Ozon后台返回 ${metricNumber(ozonQueryCount)} 个搜索词「${query}」，但没有搜索人数，先不自动上传。`;
    }
    const yandexCount = Number(actionYandexSearchCount(action) || 0);
    if (yandexCount > 0) {
      const yandexPeriod = action.evidence?.top_yandex_wordstat?.[0]?.period_days || 30;
      return `依据：Yandex 近${yandexPeriod}天有 ${metricNumber(yandexCount)} 次搜索「${query}」，只作为标签参考。`;
    }
    const trialCount = Number(actionTrialSearchCount(action) || 0);
    if (trialCount > 0) {
      return `依据：竞品商品查询有 ${metricNumber(trialCount)} 人搜「${query}」，只按人数高的词补标签。`;
    }
    const trialQueryCount = Number(action.evidence?.reference_totals?.trial_reference_query_count || action.evidence?.top_trial_terms?.length || 0);
    if (trialQueryCount > 0) {
      const source = action.evidence?.top_trial_terms?.[0]?.source_label || "Ozon下拉/竞品/类目词";
      return `依据：${source}提取 ${metricNumber(trialQueryCount)} 个词「${query}」，但没有搜索人数，先不自动上传。`;
    }
  }
  if (action.evidence?.data_source_status === "title_inference_only" || action.data_source_status === "title_inference_only") {
    return "依据：只有标题识别，暂无搜索数据来源；建议可看，不自动上传。";
  }
  const count = metricNumber(ozonCount);
  const missingTitleTerms = (action.title_terms || []).slice(0, 3);
  const covered = action.title_locked
    ? "标题已锁定，先补标签"
    : missingTitleTerms.length
      ? `建议补充「${missingTitleTerms.join("、")}」`
      : "标题已覆盖当前搜索词";
  return `依据：过去${periodDays}天有 ${count} 人搜「${query}」，${covered}。`;
}

function searchLayerLabel(layer?: string, action?: SearchVisibilityAction) {
  if (action?.data_source_status === "query_without_count" || action?.evidence?.data_source_status === "query_without_count") return "无人数候选";
  if (action?.data_source_status === "trial_source" || action?.evidence?.data_source_status === "trial_source") return "试错标签";
  if (layer === "stable_seller") return "稳定商品";
  if (layer === "title_optimization_candidate") return "可优化";
  if (layer === "tag_only_candidate") return "补标签";
  if (layer === "insufficient_data") return "待补来源";
  return layer || "待判断";
}

function searchBadgeVariant(layer?: string): "default" | "warning" | "danger" | "muted" {
  if (layer === "title_optimization_candidate") return "warning";
  if (layer === "stable_seller" || layer === "tag_only_candidate") return "default";
  return "muted";
}

export function PipelinePanel({
  products,
  shops,
  entryTab,
  selectedStoreId,
  selectedProductId,
  selectedOzonProductId,
  productsLoading,
  productsError,
  searchVisibilityPlan,
  syncingSearchVisibility,
  onSelectStore,
  onSelectProduct,
  onSelectOzonProduct,
  onLaunchProduct,
  onSyncSearchVisibility,
}: {
  products?: ProductCard[];
  shops?: ShopCard[];
  entryTab?: WorkspaceTab;
  selectedStoreId?: string;
  selectedProductId?: string;
  selectedOzonProductId?: string;
  productsLoading?: boolean;
  productsError?: string;
  searchVisibilityPlan?: SearchVisibilityPlan | null;
  syncingSearchVisibility?: boolean;
  onSelectStore?: (storeId: string) => void;
  onSelectProduct: (productId: string) => void;
  onSelectOzonProduct?: (productId: string) => void;
  onLaunchProduct?: (productId: string) => void;
  onSyncSearchVisibility?: () => Promise<unknown>;
}) {
  const [query, setQuery] = useState("");
  const [activeTab, setActiveTab] = useState<WorkspaceTab>(entryTab || "ozon");
  const localProducts = products || [];
  const inboxProducts = useMemo(() => localProducts.filter(isLocalQueueProduct), [localProducts]);
  const attentionProducts = useMemo(() => localProducts.filter(isAttentionProduct), [localProducts]);
  const searchActions = searchVisibilityPlan?.actions || [];
  const shopOptions = (shops?.length ? shops : searchVisibilityPlan?.shop_id ? [{
    id: searchVisibilityPlan.shop_id,
    display_name: searchVisibilityPlan.shop_id,
    connection_status: "connected",
  } as ShopCard] : []);
  const selectedShop = shopOptions.find((shop) => shop.id === selectedStoreId)
    || shopOptions.find((shop) => shop.id === searchVisibilityPlan?.shop_id)
    || shopOptions[0];
  const searchPlanMatchesStore = !selectedStoreId || !searchVisibilityPlan?.shop_id || searchVisibilityPlan.shop_id === selectedStoreId;
  const normalizedQuery = query.trim().toLowerCase();

  useEffect(() => {
    if (entryTab) setActiveTab(entryTab);
  }, [entryTab]);

  useEffect(() => {
    const selected = localProducts.find((product) => product.product_id === selectedProductId);
    if (selected && isLocalQueueProduct(selected) && activeTab === "ozon") {
      setActiveTab("inbox");
    }
  }, [activeTab, localProducts, selectedProductId]);

  const visibleLocalProducts = (activeTab === "attention" ? attentionProducts : inboxProducts).filter((product) => {
    if (!normalizedQuery) return true;
    return [
      product.product_id,
      product.title_cn,
      product.title_ru,
      product.current_step,
      product.raw_status,
      product.search_terms,
    ].some((value) => String(value || "").toLowerCase().includes(normalizedQuery));
  });
  const visibleOzonActions = searchPlanMatchesStore
    ? searchActions.filter((action) => {
      if (!normalizedQuery) return true;
      return [
        action.product_id,
        action.sku,
        action.current_title,
        action.category_name,
        action.brand,
        ...(action.offer_ids || []),
        ...(action.title_terms || []),
        ...(action.subject_tags || []),
        ...(action.existing_subject_tags || []),
        topQuery(action),
      ].some((value) => String(value || "").toLowerCase().includes(normalizedQuery));
    })
    : [];
  const sidebarOzonActions = useMemo(() => {
    const firstPage = visibleOzonActions.slice(0, 40);
    const active = visibleOzonActions.find((item) => item.product_id === selectedOzonProductId);
    if (!active || firstPage.some((item) => item.product_id === active.product_id)) return firstPage;
    return [active, ...firstPage.slice(0, 39)];
  }, [selectedOzonProductId, visibleOzonActions]);

  function renderLocalProduct(product: ProductCard) {
    return (
      <motion.article
        key={product.product_id}
        className={cn("workspace-product", selectedProductId === product.product_id && "active")}
        initial={{ opacity: 0, x: -8 }}
        animate={{ opacity: 1, x: 0 }}
      >
        <button type="button" className="workspace-product-main" onClick={() => onSelectProduct(product.product_id)}>
          {product.thumbnail_url ? <img src={assetUrl(product.thumbnail_url)} alt="" /> : <PackageCheck className="h-5 w-5" />}
          <span>
            <strong>{truncate(product.title_cn || product.title_ru || product.product_id, 30)}</strong>
            <small>{product.product_id} · {product.sku_count} SKU · {productStepLabel(product)}</small>
          </span>
        </button>
        <div className="workspace-product-meta">
          <Badge variant={productBadge(product)}>{productStatusLabel(product)}</Badge>
          <em>{product.progress || 0}%</em>
        </div>
        {isInboxProduct(product) && onLaunchProduct && (
          <Button size="sm" variant="secondary" onClick={() => onLaunchProduct(product.product_id)}>
            选择店铺
          </Button>
        )}
      </motion.article>
    );
  }

  function renderOzonAction(action: SearchVisibilityAction) {
    const active = selectedOzonProductId === action.product_id;
    const imageUrl = action.image_url
      || (action.images || []).map((item) => typeof item === "string" ? item : item?.url || item?.file_name || item?.src || "").find(Boolean)
      || "";
    const remoteTags = action.existing_subject_tags || [];
    const uploadedTags = action.last_upload?.applied_subject_tags || [];
    const currentTags = remoteTags.length ? remoteTags : uploadedTags;
    const suggestedTags = action.subject_tags || [];
    const visibleTags = suggestedTags.length ? suggestedTags : currentTags;
    const suggestedKeys = suggestedTags.map(tagKey).filter(Boolean);
    const actualKeys = actualProductTags(action).map(tagKey).filter(Boolean);
    const addedToProductCard = Boolean(suggestedKeys.length && suggestedKeys.every((key) => actualKeys.includes(key)));
    const submittedNotAdded = action.last_upload?.status === "submitted" && !addedToProductCard;
    return (
      <motion.article
        key={action.product_id}
        className={cn("workspace-product ozon", active && "active")}
        initial={{ opacity: 0, x: -8 }}
        animate={{ opacity: 1, x: 0 }}
      >
        <button type="button" className="workspace-product-main" onClick={() => onSelectOzonProduct?.(action.product_id)}>
          {imageUrl ? (
            <img src={assetUrl(imageUrl)} alt="" />
          ) : (
            <span className="workspace-product-placeholder"><ImageIcon className="h-4 w-4" /></span>
          )}
          <span>
            <strong>{truncate(action.current_title || action.offer_ids?.[0] || action.product_id, 32)}</strong>
            <small>{action.product_id} · {action.offer_ids?.[0] || action.sku || "SKU未读取"}</small>
          </span>
        </button>
        <div className="workspace-product-meta ozon-meta">
          <Badge variant={addedToProductCard ? "default" : submittedNotAdded ? "warning" : searchBadgeVariant(action.risk_layer)}>
            {addedToProductCard ? "已添加到商品卡" : submittedNotAdded ? "待确认/可重传" : searchLayerLabel(action.risk_layer, action)}
          </Badge>
          <em>{actionSearchMetricLabel(action)}</em>
        </div>
        <div className="workspace-product-tags">
          <span><b>{remoteTags.length ? "当前标签" : uploadedTags.length ? "最近上传" : "当前标签"}</b>{currentTags.length ? `${currentTags.length} 个` : "未读取"}</span>
          <span><b>建议标签</b>{suggestedTags.length ? `${suggestedTags.length} 个` : "暂无"}</span>
        </div>
        {!!visibleTags.length && (
          <button type="button" className="workspace-tag-strip" onClick={() => onSelectOzonProduct?.(action.product_id)}>
            {visibleTags.slice(0, 4).map((tag) => (
              <em
                key={`${action.product_id}-${tag}`}
                className={cn(actionAddedToProductCard(action, tag) && "added")}
              >
                {tag}
              </em>
            ))}
            {visibleTags.length > 4 && <span>+{visibleTags.length - 4}</span>}
          </button>
        )}
        <p>{actionBasis(action, searchVisibilityPlan?.period_days || 7)}</p>
      </motion.article>
    );
  }

  return (
    <Card className="pipeline-panel workspace-panel floating-layer">
      <CardHeader className="workspace-head">
        <div className="pipeline-brand-row">
          <img src={jlcLogo} alt="JLC GLOBAL 嘉联创" />
          <div>
            <div className="panel-kicker">工作台</div>
            <CardTitle>商品列表</CardTitle>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <label className="workspace-store">
          <span><Store className="h-3.5 w-3.5" /> 店铺</span>
          <select
            value={selectedStoreId || selectedShop?.id || ""}
            onChange={(event) => onSelectStore?.(event.target.value)}
            disabled={!shopOptions.length}
          >
            {shopOptions.map((shop) => (
              <option key={shop.id} value={shop.id}>{shop.display_name || shop.id}</option>
            ))}
            {!shopOptions.length && <option value="">暂无店铺</option>}
          </select>
          <small>{selectedShop?.connection_status === "connected" ? "已连接" : selectedShop ? "待检查" : "先添加店铺"}</small>
        </label>

        <div className="workspace-tabs" role="tablist" aria-label="商品入口">
          <button type="button" className={activeTab === "ozon" ? "active" : ""} onClick={() => setActiveTab("ozon")}>
            <UploadCloud className="h-4 w-4" />
            Ozon商品
            <b>{searchActions.length}</b>
          </button>
          <button type="button" className={activeTab === "inbox" ? "active" : ""} onClick={() => setActiveTab("inbox")}>
            <Box className="h-4 w-4" />
            采集/生产
            <b>{inboxProducts.length}</b>
          </button>
          <button type="button" className={activeTab === "attention" ? "active" : ""} onClick={() => setActiveTab("attention")}>
            <AlertTriangle className="h-4 w-4" />
            待处理
            <b>{attentionProducts.length}</b>
          </button>
        </div>

        <label className="queue-search workspace-search">
          <Search className="h-3.5 w-3.5" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={activeTab === "ozon" ? "搜 Ozon 商品、词、SKU" : "搜商品、SKU、步骤"}
          />
        </label>

        {activeTab === "ozon" && (
          <div className="workspace-search-tools">
            <span>{selectedShop?.display_name || selectedStoreId || "当前店铺"}</span>
            <Button size="sm" variant="secondary" onClick={() => onSyncSearchVisibility?.()} disabled={syncingSearchVisibility || !onSyncSearchVisibility}>
              <RefreshCw className={cn("h-3.5 w-3.5", syncingSearchVisibility && "animate-spin")} />
              更新商品信息
            </Button>
          </div>
        )}

        <ScrollArea className="workspace-scroll">
          <div className="workspace-products">
            {activeTab === "ozon" ? (
              !searchPlanMatchesStore ? (
                <div className="queue-empty">这个店铺还没更新搜索词</div>
              ) : visibleOzonActions.length ? (
                <>
                  {sidebarOzonActions.map(renderOzonAction)}
                  {visibleOzonActions.length > sidebarOzonActions.length && (
                    <div className="queue-empty">左侧仅显示前 40 个商品，请在中间商品列表搜索或翻页。</div>
                  )}
                </>
              ) : (
                <div className="queue-empty">
                  {searchVisibilityPlan?.available === false
                    ? "正在同步此店全部 Ozon 商品，完成后会自动显示。"
                    : "暂无 Ozon 商品搜索词，点“更新商品信息”读取。"}
                </div>
              )
            ) : productsLoading && !visibleLocalProducts.length ? (
              <div className="queue-empty">正在读取商品列表</div>
            ) : productsError && !visibleLocalProducts.length ? (
              <div className="queue-empty danger">{productsError}</div>
            ) : visibleLocalProducts.length ? (
              visibleLocalProducts.map(renderLocalProduct)
            ) : (
              <div className="queue-empty">
                {activeTab === "attention" ? "暂无待处理商品" : "采集箱暂无商品"}
              </div>
            )}
          </div>
        </ScrollArea>

        <div className="workspace-footnote">
          {activeTab === "ozon" ? (
            <>
              <CheckCircle2 className="h-3.5 w-3.5" />
              <span>只按所选店铺显示；下载商品资料和搜索词。</span>
            </>
          ) : (
            <>
              <CheckCircle2 className="h-3.5 w-3.5" />
              <span>采集和生产中的商品会在这里显示进度。</span>
            </>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
