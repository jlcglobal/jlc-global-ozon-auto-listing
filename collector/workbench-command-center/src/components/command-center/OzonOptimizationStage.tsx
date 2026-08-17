import { useMemo, useState } from "react";
import { ArrowLeft, ChevronLeft, ChevronRight, Clock3, ImageIcon, RefreshCw, Search, SearchCheck, ShoppingBag, Tag, UploadCloud } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { assetUrl } from "@/services/workbenchApi";
import { cn, truncate } from "@/lib/utils";
import type { CommandResult } from "@/lib/workbenchFormat";
import type { SearchVisibilityAction, SearchVisibilityPlan } from "@/types/workbench";

function metricNumber(value: unknown) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "0";
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(number);
}

function topQuery(action?: SearchVisibilityAction | null) {
  if (!action) return "暂无搜索词";
  return action.evidence?.top_queries?.[0]?.query
    || action.evidence?.top_seerfar_keyword_reverse?.[0]?.query
    || action.evidence?.top_seerfar_keyword_mining?.[0]?.query
    || action.evidence?.top_yandex_wordstat?.[0]?.query
    || action.evidence?.top_trial_terms?.[0]?.query
    || action.title_terms?.[0]
    || action.subject_tags?.[0]
    || "暂无搜索词";
}

function actionSearchCount(action?: SearchVisibilityAction | null) {
  return action?.evidence?.totals?.impressions
    || action?.evidence?.top_queries?.[0]?.metrics?.impressions
    || 0;
}

function actionOzonQueryCount(action?: SearchVisibilityAction | null) {
  return Number(action?.evidence?.totals?.query_count || action?.evidence?.top_queries?.length || 0);
}

function actionYandexSearchCount(action?: SearchVisibilityAction | null) {
  return action?.evidence?.reference_totals?.yandex_wordstat_searches
    || action?.evidence?.top_yandex_wordstat?.[0]?.count
    || action?.evidence?.top_yandex_wordstat?.[0]?.metrics?.search_count
    || 0;
}

function actionSeerfarSearchHeat(action?: SearchVisibilityAction | null) {
  return action?.evidence?.reference_totals?.seerfar_keyword_mining_search_heat
    || action?.evidence?.top_seerfar_keyword_mining?.[0]?.count
    || action?.evidence?.top_seerfar_keyword_mining?.[0]?.metrics?.monthly_search_heat
    || 0;
}

function actionSeerfarReverseSearchCount(action?: SearchVisibilityAction | null) {
  return action?.evidence?.reference_totals?.seerfar_keyword_reverse_searches
    || action?.evidence?.top_seerfar_keyword_reverse?.[0]?.count
    || action?.evidence?.top_seerfar_keyword_reverse?.[0]?.metrics?.search_count
    || 0;
}

function actionTrialSearchCount(action?: SearchVisibilityAction | null) {
  return action?.evidence?.reference_totals?.trial_reference_searches
    || action?.evidence?.top_trial_terms?.[0]?.count
    || action?.evidence?.top_trial_terms?.[0]?.metrics?.search_count
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

function actualProductTags(action?: SearchVisibilityAction | null) {
  const check = action?.last_upload_status_check;
  const existingTags = (action?.existing_subject_tags || []).flatMap(splitTagText);
  const checkedTags = check?.subject_tag_values?.length
    ? check.subject_tag_values.flatMap(splitTagText)
    : check?.has_subject_tags ? splitTagText(check.subject_tag_sample) : [];
  return [
    ...existingTags,
    ...checkedTags,
  ];
}

function actionAddedToProductCard(action?: SearchVisibilityAction | null, tag?: string) {
  if (!action) return false;
  const actualKeys = actualProductTags(action).map(tagKey).filter(Boolean);
  if (tag) {
    const key = tagKey(tag);
    return Boolean(key && actualKeys.includes(key));
  }
  const suggestedKeys = (action.subject_tags || []).map(tagKey).filter(Boolean);
  return Boolean(suggestedKeys.length && suggestedKeys.every((key) => actualKeys.includes(key)));
}

function actionViewCount(action?: SearchVisibilityAction | null) {
  return action?.evidence?.totals?.clicks
    || action?.evidence?.top_queries?.[0]?.metrics?.clicks
    || 0;
}

function actionQueryCount(action?: SearchVisibilityAction | null) {
  return Number(action?.evidence?.totals?.query_count || action?.evidence?.top_queries?.length || 0)
    + Number(action?.evidence?.reference_totals?.seerfar_keyword_reverse_query_count || action?.evidence?.top_seerfar_keyword_reverse?.length || 0)
    + Number(action?.evidence?.reference_totals?.seerfar_keyword_mining_query_count || action?.evidence?.top_seerfar_keyword_mining?.length || 0)
    + Number(action?.evidence?.reference_totals?.yandex_wordstat_query_count || action?.evidence?.top_yandex_wordstat?.length || 0)
    + Number(action?.evidence?.reference_totals?.trial_reference_query_count || action?.evidence?.top_trial_terms?.length || 0);
}

function hasSearchSource(action?: SearchVisibilityAction | null) {
  return Boolean(
    Number(actionSearchCount(action) || 0) > 0
    || Number(actionSeerfarReverseSearchCount(action) || 0) > 0
    || Number(actionSeerfarSearchHeat(action) || 0) > 0
    || Number(actionYandexSearchCount(action) || 0) > 0
    || Number(actionTrialSearchCount(action) || 0) > 0
    || action?.evidence?.data_source_status === "search_source"
    || action?.data_source_status === "search_source",
  );
}

function actionOrderCount(action?: SearchVisibilityAction | null) {
  const direct = Number(action?.order_count || 0);
  if (Number.isFinite(direct) && direct > 0) return direct;
  return action?.evidence?.totals?.orders
    || (action?.evidence?.top_queries || []).reduce((total, query) => total + Number(query.metrics?.orders || 0), 0)
    || 0;
}

function dateRank(value: unknown) {
  const text = String(value || "").trim();
  if (!text) return 0;
  const timestamp = Date.parse(text);
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function actionCreatedRank(action?: SearchVisibilityAction | null) {
  return dateRank(action?.created_at || action?.updated_at);
}

function formatDate(value: unknown) {
  const timestamp = dateRank(value);
  if (!timestamp) return "未读取";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "2-digit",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(timestamp));
}

function actionBasis(action?: SearchVisibilityAction | null, periodDays = 7) {
  if (!action) return "依据：先读取所选店铺的搜索词。";
  const query = topQuery(action);
  const ozonCount = Number(actionSearchCount(action) || 0);
  const missingTitleTerms = (action.title_terms || []).slice(0, 3);
  const titleText = action.title_locked
    ? "标题不动，先补主题标签和简介"
    : missingTitleTerms.length
      ? `建议补充「${missingTitleTerms.join("、")}」`
      : "标题已覆盖当前搜索词";
  if (ozonCount > 0) {
    return `依据：Ozon 过去${periodDays}天有 ${metricNumber(ozonCount)} 人搜「${query}」，${titleText}。`;
  }
  const ozonQueryCount = actionOzonQueryCount(action);
  if (ozonQueryCount > 0) {
    return `依据：Ozon后台返回 ${metricNumber(ozonQueryCount)} 个搜索词「${query}」，但没有搜索人数，先不作为自动上传依据。`;
  }
  const seerfarReverseCount = Number(actionSeerfarReverseSearchCount(action) || 0);
  if (seerfarReverseCount > 0) {
    return `依据：Seerfar 竞品反查有 ${metricNumber(seerfarReverseCount)} 次搜索「${query}」，作为标签和简介参考。`;
  }
  const seerfarHeat = Number(actionSeerfarSearchHeat(action) || 0);
  if (seerfarHeat > 0) {
    return `依据：Seerfar 月搜热度 ${metricNumber(seerfarHeat)}「${query}」，每周更新，作为标签和简介参考。`;
  }
  const yandexCount = Number(actionYandexSearchCount(action) || 0);
  if (yandexCount > 0) {
    const yandexPeriod = action.evidence?.top_yandex_wordstat?.[0]?.period_days || 30;
    return `依据：Yandex 近${yandexPeriod}天有 ${metricNumber(yandexCount)} 次搜索「${query}」，作为标签和简介参考。`;
  }
  const trialTerm = action.evidence?.top_trial_terms?.[0];
  if (trialTerm || action.data_source_status === "trial_source" || action.evidence?.data_source_status === "trial_source") {
    const source = trialTerm?.source_label || "竞品/类目试错词";
    const count = Number(actionTrialSearchCount(action) || 0);
    if (count > 0) {
      return `依据：${source}有 ${metricNumber(count)} 次参考搜索「${query}」，上传标签和简介试错。`;
    }
    return `依据：${source}提取「${query}」，但没有搜索人数，先不自动上传。`;
  }
  return "依据：只有标题识别，暂无搜索数据来源；建议可看，但不自动上传。";
}

function searchLayerLabel(layer?: string, action?: SearchVisibilityAction | null) {
  if (action?.data_source_status === "query_without_count" || action?.evidence?.data_source_status === "query_without_count") return "无人数候选";
  if (action?.data_source_status === "trial_source" || action?.evidence?.data_source_status === "trial_source") return "试错标签";
  if (layer === "stable_seller") return "稳定商品";
  if (layer === "title_optimization_candidate") return "可改标题";
  if (layer === "tag_only_candidate") return "补标签";
  if (layer === "insufficient_data") return "待补来源";
  return layer || "待判断";
}

function badgeVariant(layer?: string): "default" | "warning" | "danger" | "muted" {
  if (layer === "title_optimization_candidate") return "warning";
  if (layer === "stable_seller" || layer === "tag_only_candidate") return "default";
  return "muted";
}

function recommendedTitle(action?: SearchVisibilityAction | null) {
  if (!action) return "";
  const current = String(action.current_title || "").trim();
  const additions = (action.title_terms || [])
    .filter((term) => term && !current.toLowerCase().includes(String(term).toLowerCase()))
    .slice(0, 2);
  if (!current) return additions.join(" ");
  if (!additions.length) return current;
  return truncate(`${current} ${additions.join(" ")}`, 118);
}

function imageUrls(action?: SearchVisibilityAction | null) {
  const values = [
    action?.image_url,
    ...(action?.images || []).map((item) => {
      if (typeof item === "string") return item;
      return item?.url || item?.file_name || item?.src || "";
    }),
  ];
  const seen = new Set<string>();
  return values
    .map((value) => String(value || "").trim())
    .filter((value) => {
      if (!value || seen.has(value)) return false;
      seen.add(value);
      return true;
    });
}

function formatPrice(value: unknown, currency?: string) {
  if (value === undefined || value === null || value === "") return "未读取";
  const text = String(value).trim();
  const number = Number(text.replace(/\s/g, "").replace(",", "."));
  if (!Number.isFinite(number)) return text;
  const unit = String(currency || "").toUpperCase();
  if (unit.includes("RUB") || unit.includes("RUR")) return `${Math.round(number).toLocaleString("ru-RU")} ₽`;
  if (unit.includes("CNY") || unit.includes("RMB")) return `¥${number.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
  return number.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

function formatValue(value: unknown) {
  if (value === undefined || value === null || value === "") return "未读取";
  return String(value);
}

function formatMeasure(value: unknown, unit: string) {
  if (value === undefined || value === null || value === "") return "未读取";
  const number = Number(String(value).replace(/\s/g, "").replace(",", "."));
  if (!Number.isFinite(number) || number <= 0) return String(value);
  return `${Number.isInteger(number) ? number : number.toFixed(1)} ${unit}`;
}

function formatDimensions(action?: SearchVisibilityAction | null) {
  const measurements = action?.measurements || {};
  const length = measurements.length_mm;
  const width = measurements.width_mm;
  const height = measurements.height_mm;
  if ([length, width, height].some((value) => value === undefined || value === null || value === "")) return "未读取";
  const valueText = [length, width, height].map((value) => {
    const number = Number(String(value).replace(/\s/g, "").replace(",", "."));
    if (!Number.isFinite(number)) return String(value);
    return Number.isInteger(number) ? String(number) : number.toFixed(1);
  });
  return `${valueText.join("×")} mm`;
}

function tagAdviceLabel(action?: SearchVisibilityAction | null) {
  const count = action?.subject_tags?.length || 0;
  if (!count) return "暂无";
  if (action?.subject_tag_strategy === "replace_low_search") return `替换 ${count} 个`;
  return `${count} 个主题标签`;
}

function canUploadOptimization(action?: SearchVisibilityAction | null) {
  if (!action || !hasSearchSource(action)) return false;
  const tagsAlreadyOnCard = actionAddedToProductCard(action);
  const canUploadTags = (action.allowed_changes || []).includes("subject_tags")
    && Boolean(action.subject_tag_update_required)
    && Boolean(action.subject_tags?.length)
    && !tagsAlreadyOnCard;
  const canUploadIntro = (action.allowed_changes || []).includes("intro")
    && Boolean(action.intro_update_available);
  return canUploadTags || canUploadIntro;
}

function canStartBatchUpload(action?: SearchVisibilityAction | null) {
  return canUploadOptimization(action) && action?.last_upload?.status !== "submitted";
}

function isSubmittedPendingCheck(action?: SearchVisibilityAction | null) {
  return canUploadOptimization(action) && action?.last_upload?.status === "submitted";
}

function queryMetricCount(query: { count?: number; metrics?: { impressions?: number; search_count?: number } }) {
  return query.metrics?.impressions || query.metrics?.search_count || query.count || 0;
}

function querySourceText(
  query: { count?: number; metrics?: { impressions?: number; search_count?: number; monthly_search_heat?: number }; source_label?: string },
  source: "ozon" | "seerfar" | "seerfar_reverse" | "yandex" | "trial",
) {
  const count = Number(queryMetricCount(query) || 0);
  if (source === "ozon") return count > 0 ? `搜索人数 · ${metricNumber(count)}` : "搜索人数 · --";
  if (source === "seerfar") return `Seerfar月搜热度 · ${metricNumber(count)}`;
  if (source === "seerfar_reverse") return `Seerfar反查搜索 · ${metricNumber(count)}`;
  if (source === "yandex") return `Yandex搜索量 · ${metricNumber(count)}`;
  if (count > 0) return `${query.source_label || "竞品词"}参考量 · ${metricNumber(count)}`;
  return `${query.source_label || "竞品词"} · 搜索人数 --`;
}

type OzonSortMode = "created_desc" | "orders_desc";

function matchesAction(action: SearchVisibilityAction, normalizedQuery: string) {
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
    ...(action.evidence?.top_trial_terms || []).map((term) => term.query),
    topQuery(action),
  ].some((value) => String(value || "").toLowerCase().includes(normalizedQuery));
}

export function OzonOptimizationStage({
  action,
  actions,
  plan,
  shopName,
  selectedProductId,
  detailOpen,
  syncing,
  applying,
  importingYandex,
  queueingSeerfar,
  commandResult,
  onSelectAction,
  onBackToList,
  onRefreshSearch,
  onApplyOptimization,
  onApplyAllOptimizations,
  onCheckUploadStatus,
  onImportYandexWordstat,
  onQueueSeerfarKeywordMining,
}: {
  action?: SearchVisibilityAction | null;
  actions?: SearchVisibilityAction[];
  plan?: SearchVisibilityPlan | null;
  shopName?: string;
  selectedProductId?: string;
  detailOpen?: boolean;
  syncing?: boolean;
  applying?: boolean;
  importingYandex?: boolean;
  queueingSeerfar?: boolean;
  commandResult?: CommandResult | null;
  onSelectAction?: (productId: string) => void;
  onBackToList?: () => void;
  onRefreshSearch?: () => Promise<unknown>;
  onApplyOptimization?: () => Promise<unknown>;
  onApplyAllOptimizations?: () => Promise<unknown>;
  onCheckUploadStatus?: () => Promise<unknown>;
  onImportYandexWordstat?: (productId: string, text: string) => Promise<unknown>;
  onQueueSeerfarKeywordMining?: (productId: string) => Promise<unknown>;
}) {
  const pageSize = 40;
  const allActions = actions?.length ? actions : plan?.actions || [];
  const periodDays = plan?.period_days || 7;
  const orderPeriodDays = plan?.order_period_days || 90;
  const [listQuery, setListQuery] = useState("");
  const [sortMode, setSortMode] = useState<OzonSortMode>("created_desc");
  const [listPage, setListPage] = useState(1);
  const [yandexImportOpen, setYandexImportOpen] = useState(false);
  const [yandexText, setYandexText] = useState("");
  const normalizedListQuery = listQuery.trim().toLowerCase();
  const visibleActions = useMemo(
    () => allActions
      .filter((item) => matchesAction(item, normalizedListQuery))
      .sort((a, b) => {
        if (sortMode === "orders_desc") {
          return actionOrderCount(b) - actionOrderCount(a)
            || actionCreatedRank(b) - actionCreatedRank(a)
            || Number(b.product_id || 0) - Number(a.product_id || 0);
        }
        return actionCreatedRank(b) - actionCreatedRank(a)
          || Number(b.product_id || 0) - Number(a.product_id || 0);
      }),
    [allActions, normalizedListQuery, sortMode],
  );
  const totalPages = Math.max(1, Math.ceil(visibleActions.length / pageSize));
  const currentPage = Math.min(listPage, totalPages);
  const pagedActions = useMemo(() => {
    const start = (currentPage - 1) * pageSize;
    return visibleActions.slice(start, start + pageSize);
  }, [currentPage, visibleActions]);
  const totalSearchCount = allActions.reduce((total, item) => total + Number(actionSearchCount(item) || 0), 0);
  const totalSeerfarReverseSearchCount = allActions.reduce((total, item) => total + Number(actionSeerfarReverseSearchCount(item) || 0), 0);
  const totalSeerfarSearchHeat = allActions.reduce((total, item) => total + Number(actionSeerfarSearchHeat(item) || 0), 0);
  const totalQueryCount = allActions.reduce((total, item) => total + Number(actionQueryCount(item) || 0), 0);
  const totalOrderCount = allActions.reduce((total, item) => total + Number(actionOrderCount(item) || 0), 0);
  const totalSuggestedTags = allActions.reduce((total, item) => total + (item.subject_tags?.length || 0), 0);
  const uploadableCount = allActions.filter((item) => {
    return canStartBatchUpload(item);
  }).length;
  const pendingUploadCount = allActions.filter((item) => {
    return isSubmittedPendingCheck(item);
  }).length;
  const addedToCardCount = allActions.filter((item) => {
    return actionAddedToProductCard(item);
  }).length;

  async function handleYandexImport() {
    if (!action?.product_id || !yandexText.trim() || !onImportYandexWordstat) return;
    await onImportYandexWordstat(action.product_id, yandexText);
    setYandexText("");
    setYandexImportOpen(false);
  }

  function openExactSkuMatch() {
    const sku = listQuery.trim().toLowerCase();
    if (!sku) return;
    const match = visibleActions.find((item) => {
      const identifiers = [item.product_id, item.sku, ...(item.offer_ids || [])]
        .map((value) => String(value || "").trim().toLowerCase())
        .filter(Boolean);
      return identifiers.includes(sku);
    });
    if (match) onSelectAction?.(match.product_id);
  }

  if (!detailOpen || !action) {
    return (
      <div className="center-stack">
        <div className="center-tools">
          <div>
            <span>OZON 商品列表</span>
            <strong>{shopName || plan?.shop_id || "当前店铺"}</strong>
          </div>
          <div className="ozon-toolbar-actions">
            <Button variant="secondary" onClick={() => onRefreshSearch?.()} disabled={syncing || !onRefreshSearch}>
              <RefreshCw className={cn("h-4 w-4", syncing && "animate-spin")} />
              更新商品信息
            </Button>
            <Button
              onClick={() => onApplyAllOptimizations?.()}
              disabled={!uploadableCount || applying || !onApplyAllOptimizations}
            >
              <UploadCloud className="h-4 w-4" />
              {uploadableCount ? "批量上传标签+简介" : pendingUploadCount ? "已提交待确认" : "批量上传标签+简介"}
              {uploadableCount || pendingUploadCount ? <b>{uploadableCount || pendingUploadCount}</b> : null}
            </Button>
          </div>
        </div>
        <section className="ozon-optimization-stage ozon-product-list-stage glass-panel hero-layer">
          <CardHeader className="ozon-opt-header ozon-list-header">
            <div>
              <div className="panel-kicker">所有商品</div>
              <CardTitle>Ozon 商品列表</CardTitle>
              <p>{allActions.length ? `已下载 ${allActions.length} 个商品：图片、标题、主题标签、售价、重量、长宽高。` : plan?.notice || "先更新所选店铺的 Ozon 商品信息。"}</p>
            </div>
            <div className="ozon-list-header-actions">
              <Badge variant={allActions.length ? "default" : "muted"}>{allActions.length ? `${allActions.length} 个商品` : "待读取"}</Badge>
              {uploadableCount ? <Badge variant="warning">{uploadableCount} 可上传</Badge> : null}
              {pendingUploadCount ? <Badge variant="warning">{pendingUploadCount} 待确认</Badge> : null}
              {!uploadableCount && !pendingUploadCount && (
                <Badge variant={addedToCardCount ? "default" : "muted"}>
                  {addedToCardCount ? `${addedToCardCount} 已添加` : "无新标签"}
                </Badge>
              )}
            </div>
          </CardHeader>
          <CardContent className="ozon-list-content">
            <div className="ozon-list-toolbar">
              <label>
                <Search className="h-4 w-4" />
                <input
                  value={listQuery}
                  onChange={(event) => {
                    setListQuery(event.target.value);
                    setListPage(1);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") openExactSkuMatch();
                  }}
                  aria-label="SKU 快速查看"
                  placeholder="输入 SKU 直接查看，也可搜标题、搜索词、主题标签"
                />
              </label>
              <div className="ozon-sort-controls" role="group" aria-label="商品排序">
                <Button
                  variant={sortMode === "created_desc" ? "default" : "secondary"}
                  size="sm"
                  onClick={() => {
                    setSortMode("created_desc");
                    setListPage(1);
                  }}
                >
                  <Clock3 className="h-3.5 w-3.5" />
                  创建时间倒叙
                </Button>
                <Button
                  variant={sortMode === "orders_desc" ? "default" : "secondary"}
                  size="sm"
                  onClick={() => {
                    setSortMode("orders_desc");
                    setListPage(1);
                  }}
                >
                  <ShoppingBag className="h-3.5 w-3.5" />
                  出单量倒叙
                </Button>
              </div>
              <div className="ozon-list-summary">
                <span><b>{totalSearchCount > 0 ? metricNumber(totalSearchCount) : "--"}</b> Ozon搜索人数</span>
                <span><b>{totalSeerfarReverseSearchCount > 0 ? metricNumber(totalSeerfarReverseSearchCount) : "--"}</b> Seerfar反查搜索</span>
                <span><b>{totalSeerfarSearchHeat > 0 ? metricNumber(totalSeerfarSearchHeat) : "--"}</b> Seerfar月搜热度</span>
                <span><b>{metricNumber(totalOrderCount)}</b> 近{orderPeriodDays}天出单</span>
                <span><b>{metricNumber(totalQueryCount)}</b> 搜索词</span>
                <span><b>{metricNumber(totalSuggestedTags)}</b> 建议标签</span>
              </div>
            </div>

            <div className="ozon-product-list" role="list" aria-label="Ozon 商品列表">
              {pagedActions.map((item) => {
                const images = imageUrls(item);
                const remoteTags = item.existing_subject_tags || [];
                const uploadedTags = item.last_upload?.applied_subject_tags || [];
                const currentTags = remoteTags.length ? remoteTags : uploadedTags;
                const suggestedTags = item.subject_tags || [];
                const orderCount = actionOrderCount(item);
                const title = item.current_title || item.offer_ids?.[0] || item.product_id;
                const addedToProductCard = actionAddedToProductCard(item);
                const submittedNotAdded = item.last_upload?.status === "submitted" && !addedToProductCard;
                return (
                  <button
                    key={item.product_id}
                    type="button"
                    className={cn("ozon-product-row", selectedProductId === item.product_id && "active")}
                    onClick={() => onSelectAction?.(item.product_id)}
                  >
                    <span className={cn("ozon-product-row-media", !images[0] && "empty")}>
                      {images[0] ? <img src={assetUrl(images[0])} alt="" /> : <ImageIcon className="h-5 w-5" />}
                    </span>
                    <span className="ozon-product-row-main">
                      <strong>{title}</strong>
                      <small>{item.product_id} · {item.offer_ids?.[0] || item.sku || "SKU未读取"}</small>
                      <em>{actionBasis(item, periodDays)}</em>
                    </span>
                    <span className="ozon-product-row-facts">
                      <span><b>{item.created_at ? "创建" : "更新"}</b>{formatDate(item.created_at || item.updated_at)}</span>
                      <span><b>售价</b>{formatPrice(item.price, item.currency)}</span>
                      <span><b>重量</b>{formatMeasure(item.measurements?.weight_g, "g")}</span>
                      <span><b>长宽高</b>{formatDimensions(item)}</span>
                    </span>
                    <span className="ozon-product-row-tags">
                      <span><b>近{orderPeriodDays}天出单</b>{metricNumber(orderCount)}</span>
                      <span><b>{remoteTags.length ? "主题标签" : uploadedTags.length ? "最近上传" : "主题标签"}</b>{currentTags.length ? `${currentTags.length} 个` : "未读取"}</span>
                      <span><b>建议</b>{tagAdviceLabel(item)}</span>
                      <Badge
                        className="ozon-product-row-status"
                        variant={addedToProductCard ? "default" : submittedNotAdded ? "warning" : badgeVariant(item.risk_layer)}
                      >
                        {addedToProductCard ? "已添加到商品卡" : submittedNotAdded ? "待确认/可重传" : searchLayerLabel(item.risk_layer, item)}
                      </Badge>
                    </span>
                    <ChevronRight className="h-4 w-4" />
                  </button>
                );
              })}
              {!visibleActions.length && (
                <div className="ozon-product-list-empty">
                  <SearchCheck className="h-10 w-10" />
                  <strong>{allActions.length ? "没有匹配的商品" : plan?.notice || "还没有读取到 Ozon 商品"}</strong>
                  <Button variant="secondary" onClick={() => onRefreshSearch?.()} disabled={syncing || !onRefreshSearch}>
                    <RefreshCw className={cn("h-4 w-4", syncing && "animate-spin")} />
                    更新商品信息
                  </Button>
                </div>
              )}
            </div>
            {visibleActions.length > pageSize && (
              <div className="ozon-list-pagination" aria-label="商品列表分页">
                <span>第 {currentPage} / {totalPages} 页，共 {visibleActions.length} 个商品</span>
                <Button
                  variant="secondary"
                  size="sm"
                  title="上一页"
                  aria-label="上一页"
                  disabled={currentPage === 1}
                  onClick={() => setListPage((page) => Math.max(1, page - 1))}
                >
                  <ChevronLeft className="h-4 w-4" />
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  title="下一页"
                  aria-label="下一页"
                  disabled={currentPage === totalPages}
                  onClick={() => setListPage((page) => Math.min(totalPages, page + 1))}
                >
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            )}
            {commandResult && (
              <div className={cn("command-result inline", commandResult.tone)}>
                {commandResult.message}
              </div>
            )}
          </CardContent>
        </section>
      </div>
    );
  }

  const ozonQueries = action?.evidence?.top_queries || [];
  const seerfarReverseQueries = action?.evidence?.top_seerfar_keyword_reverse || [];
  const seerfarQueries = action?.evidence?.top_seerfar_keyword_mining || [];
  const yandexQueries = action?.evidence?.top_yandex_wordstat || [];
  const trialQueries = action?.evidence?.top_trial_terms || [];
  const queries = ozonQueries.length ? ozonQueries : seerfarReverseQueries.length ? seerfarReverseQueries : seerfarQueries.length ? seerfarQueries : yandexQueries.length ? yandexQueries : trialQueries;
  const querySource = ozonQueries.length ? "ozon" : seerfarReverseQueries.length ? "seerfar_reverse" : seerfarQueries.length ? "seerfar" : yandexQueries.length ? "yandex" : "trial";
  const queryPeriodLabel = ozonQueries.length
    ? `${periodDays} 天`
    : seerfarReverseQueries.length ? "Seerfar 竞品反查" : seerfarQueries.length ? "Seerfar 月搜热度（周更新）" : yandexQueries.length ? `Yandex ${yandexQueries[0]?.period_days || 30}天` : trialQueries.length ? "竞品/类目试错词" : `${periodDays} 天`;
  const title = recommendedTitle(action);
  const tags = action?.subject_tags || [];
  const uploaded = actionAddedToProductCard(action);
  const submitted = action?.last_upload?.status === "submitted";
  const sourceReady = hasSearchSource(action);
  const canApply = Boolean(
    action
    && canUploadOptimization(action)
    && onApplyOptimization
  );
  const images = imageUrls(action);
  const primaryImage = images[0] || "";
  const rawTitle = action?.current_title || action?.offer_ids?.[0] || action?.product_id || "未选择商品";
  const titleLength = (title || "").length;
  const remoteTags = action?.existing_subject_tags || [];
  const uploadedTags = action?.last_upload?.applied_subject_tags || [];
  const currentTags = remoteTags.length ? remoteTags : uploadedTags;
  const productAttributes = action?.product_attributes || [];
  const uploadStatus = action?.last_upload_status_check;

  return (
    <div className="center-stack">
      <div className="center-tools">
        <div>
          <span>OZON 商品优化</span>
          <strong>{shopName || plan?.shop_id || "当前店铺"}</strong>
        </div>
        <Button variant="secondary" onClick={() => onRefreshSearch?.()} disabled={syncing || !onRefreshSearch}>
          <RefreshCw className={cn("h-4 w-4", syncing && "animate-spin")} />
          更新商品信息
        </Button>
      </div>
      <section className="ozon-optimization-stage glass-panel hero-layer">
        <CardHeader className="ozon-opt-header">
          <div>
            <div className="panel-kicker">商品优化台</div>
            <CardTitle>{action ? truncate(rawTitle, 96) : "先选择一个 Ozon 商品"}</CardTitle>
            <p>{actionBasis(action, periodDays)}</p>
          </div>
          <div className="ozon-detail-actions">
            <Button variant="secondary" size="sm" onClick={() => onBackToList?.()}>
              <ArrowLeft className="h-4 w-4" />
              返回列表
            </Button>
            <Button
              size="sm"
              onClick={() => onApplyAllOptimizations?.()}
              disabled={!uploadableCount || applying || !onApplyAllOptimizations}
            >
              <UploadCloud className="h-4 w-4" />
              批量上传标签
            </Button>
            <Badge variant={uploaded ? "default" : submitted ? "warning" : badgeVariant(action?.risk_layer)}>
              {uploaded ? "已添加到商品卡" : submitted ? "待确认/可重传" : searchLayerLabel(action?.risk_layer, action)}
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="ozon-opt-content">
          {action ? (
            <>
              <div className="ozon-product-overview">
                <article className="ozon-gallery-card">
                  <div className={cn("ozon-gallery-main", !primaryImage && "empty")}>
                    {primaryImage ? <img src={assetUrl(primaryImage)} alt="" /> : <ImageIcon className="h-12 w-12" />}
                  </div>
                  <div className="ozon-gallery-thumbs">
                    {images.slice(0, 4).map((url, index) => (
                      <span key={`${url}-${index}`} className={index === 0 ? "active" : ""}>
                        <img src={assetUrl(url)} alt="" />
                      </span>
                    ))}
                    {!images.length && <span className="empty"><ImageIcon className="h-4 w-4" /></span>}
                  </div>
                </article>
                <article className="ozon-product-meta-card">
                  <label>
                    <span>原始标题（当前线上标题）</span>
                    <strong>{rawTitle}</strong>
                    <em>{rawTitle.length} / 255</em>
                  </label>
                  <label>
                    <span>建议标题</span>
                    <strong>{title || "本轮只更新主题标签"}</strong>
                    <em>{titleLength || rawTitle.length} / 255</em>
                  </label>
                  <div className="ozon-product-facts">
                    <div><span>类目</span><strong>{formatValue(action.category_name)}</strong></div>
                    <div><span>品牌</span><strong>{formatValue(action.brand)}</strong></div>
                    <div><span>SKU</span><strong>{formatValue(action.sku || action.offer_ids?.[0])}</strong></div>
                    <div><span>价格</span><strong>{formatPrice(action.price, action.currency)}</strong></div>
                    <div><span>重量</span><strong>{formatMeasure(action.measurements?.weight_g, "g")}</strong></div>
                    <div><span>长宽高</span><strong>{formatDimensions(action)}</strong></div>
                  </div>
                  <div className="ozon-current-tags">
                    <span>{remoteTags.length ? "当前主题标签" : uploadedTags.length ? "最近上传标签" : "当前主题标签"}</span>
                    <div>
                      {currentTags.slice(0, 12).map((tag) => <em key={tag}>{tag}</em>)}
                      {!currentTags.length && <small>{action.existing_subject_tags == null ? "Ozon未返回主题标签字段" : "商品卡暂无主题标签"}</small>}
                    </div>
                  </div>
                </article>
              </div>

              <div className="ozon-opt-panels">
                <section>
                  <div className="ozon-opt-section-head">
                    <strong>搜索词</strong>
                    <div className="ozon-section-actions">
                      <small>{queryPeriodLabel}</small>
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => setYandexImportOpen((value) => !value)}
                        disabled={!onImportYandexWordstat || importingYandex}
                      >
                        <UploadCloud className="h-3.5 w-3.5" />
                        导入Yandex
                      </Button>
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => action?.product_id && onQueueSeerfarKeywordMining?.(action.product_id)}
                        disabled={!action?.product_id || !onQueueSeerfarKeywordMining || queueingSeerfar}
                      >
                        <RefreshCw className={cn("h-3.5 w-3.5", queueingSeerfar && "animate-spin")} />
                        {queueingSeerfar ? "读取中" : "更新Seerfar"}
                      </Button>
                    </div>
                  </div>
                  {yandexImportOpen && (
                    <div className="ozon-yandex-import">
                      <textarea
                        value={yandexText}
                        onChange={(event) => setYandexText(event.target.value)}
                        placeholder={"粘贴 Wordstat 词表，例如：\nбанка для ферментации 4435\nемкость для засолки 2135"}
                      />
                      <div>
                        <small>只作为主题标签参考</small>
                        <Button
                          size="sm"
                          onClick={() => handleYandexImport()}
                          disabled={!yandexText.trim() || importingYandex}
                        >
                          <UploadCloud className={cn("h-3.5 w-3.5", importingYandex && "animate-pulse")} />
                          {importingYandex ? "保存中" : "保存参考词"}
                        </Button>
                      </div>
                    </div>
                  )}
                  <div className="ozon-query-list">
                    {queries.slice(0, 8).map((query, index) => (
                      <div key={`${query.query}-${index}`}>
                        <span>{query.query || "未知词"}</span>
                        <em>{querySourceText(query, querySource)}</em>
                      </div>
                    ))}
                    {!queries.length && <div><span>暂无搜索数据来源</span><em>只有标题识别，非真实搜索量</em></div>}
                  </div>
                </section>
                <section>
                  <div className="ozon-opt-section-head">
                    <strong>优化建议</strong>
                    <small>手动上传标签+简介</small>
                  </div>
                  <div className="ozon-advice-list">
                    <article>
                      <Tag className="h-4 w-4" />
                      <div>
                        <strong>{tags.length ? `${sourceReady ? (action.subject_tag_strategy === "replace_low_search" ? "替换" : "补充") : "标题识别"} ${Math.min(tags.length, 30)} 个主题标签` : "本轮不补标签"}</strong>
                        <p>{actionBasis(action, periodDays)}</p>
                      </div>
                    </article>
                    {action.recommended_intro && (
                      <article>
                        <SearchCheck className="h-4 w-4" />
                        <div>
                          <strong>{action.current_intro ? "原简介后追加搜索词" : "完整搜索词放简介"}</strong>
                          <p>{action.intro_supplement || action.recommended_intro}</p>
                        </div>
                      </article>
                    )}
                    <article>
                      <SearchCheck className="h-4 w-4" />
                      <div>
                        <strong>
                          {Number(actionSearchCount(action) || 0) > 0
                            ? `Ozon 有多少人搜 · ${metricNumber(actionSearchCount(action))}`
                            : actionOzonQueryCount(action) > 0
                              ? `搜索人数 -- · Ozon词 ${metricNumber(actionOzonQueryCount(action))} 个`
                              : Number(actionYandexSearchCount(action) || 0) > 0
                                ? `Yandex 参考量 · ${metricNumber(actionYandexSearchCount(action))}`
                                : Number(actionTrialSearchCount(action) || 0) > 0
                                  ? `竞品参考量 · ${metricNumber(actionTrialSearchCount(action))}`
                                  : sourceReady ? "试错来源" : "待补来源"}
                        </strong>
                        <p>浏览人数：{metricNumber(actionViewCount(action))}；订单：{metricNumber(action.evidence?.totals?.orders)}。</p>
                      </div>
                    </article>
                  </div>
                  <div className="ozon-tag-strip">
                    {tags.slice(0, 16).map((tag) => (
                      <em key={tag} className={cn(actionAddedToProductCard(action, tag) && "added")}>{tag}</em>
                    ))}
                    {!tags.length && <span>没有建议标签</span>}
                  </div>
                  <div className="ozon-attribute-note">
                    已下载字段：图片、标题、主题标签、售价、重量、长宽高{productAttributes.length ? `，另有 ${productAttributes.length} 个属性` : ""}
                  </div>
                </section>
              </div>

              <div className="ozon-opt-actions">
                <span>
                  {uploaded
                    ? `商品卡已包含建议标签${action.last_upload?.task_id ? ` · task ${action.last_upload.task_id}` : ""}。`
                    : submitted
                      ? `已提交过${action.last_upload?.task_id ? ` · task ${action.last_upload.task_id}` : ""}，但商品卡未确认包含这些标签，可重新上传。`
                      : "点击后同时上传主题标签和简介；不改标题、价格、库存。"}
                </span>
                {submitted && (
                  <Button variant="secondary" onClick={() => onCheckUploadStatus?.()} disabled={applying || !onCheckUploadStatus}>
                    <SearchCheck className="h-4 w-4" />
                    {applying ? "检查中" : "检查Ozon结果"}
                  </Button>
                )}
                <Button onClick={() => onApplyOptimization?.()} disabled={!canApply || applying}>
                  <UploadCloud className="h-4 w-4" />
                  {applying ? "正在上传" : uploaded ? "商品卡已包含" : submitted ? "重新上传标签+简介" : "运行优化并上传"}
                </Button>
              </div>
              {uploadStatus && (
                <div className={cn("command-result inline", uploaded ? "ok" : "idle")}>
                  {uploaded
                    ? `Ozon 已读到本轮建议标签；简介 ${uploadStatus.has_intro ? "已读到" : "未读到"}。`
                    : `Ozon 状态：${uploadStatus.import_status || uploadStatus.status}，但未读到本轮建议标签。`}
                  {uploadStatus.warnings?.length ? ` 警告 ${uploadStatus.warnings.length} 条。` : ""}
                </div>
              )}
            </>
          ) : (
            <div className="ozon-opt-empty">
              <SearchCheck className="h-10 w-10" />
              <strong>{plan?.notice || "左侧选择 Ozon 商品，或先更新搜索词。"}</strong>
              <Button variant="secondary" onClick={() => onRefreshSearch?.()} disabled={syncing || !onRefreshSearch}>
                <RefreshCw className={cn("h-4 w-4", syncing && "animate-spin")} />
                更新商品信息
              </Button>
            </div>
          )}
          {commandResult && (
            <div className={cn("command-result inline", commandResult.tone)}>
              {commandResult.message}
            </div>
          )}
        </CardContent>
      </section>
    </div>
  );
}
