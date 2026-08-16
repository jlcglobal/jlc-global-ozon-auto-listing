import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { AlertTriangle, Clock3, PackageCheck, Radar, RefreshCcw, Search, Store, UploadCloud, Zap } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Progress } from "@/components/ui/progress";
import { cn, truncate } from "@/lib/utils";
import {
  currentProductionState,
  isProductRunning,
  isSubmittedReadOnly,
  productStepLabel,
  readableStageName,
  selectedRegenerationSlot,
  shouldRecover,
  statusLabel,
  statusTone,
} from "@/lib/workbenchFormat";
import { assetUrl } from "@/services/workbenchApi";
import type { CommandResult } from "@/lib/workbenchFormat";
import type { ProductCard, ProductDetail } from "@/types/workbench";

function selectedStoreIds(detail: ProductDetail | null) {
  const publications = detail?.publications?.stores || {};
  return Object.entries(publications)
    .filter(([, record]) => record?.selected)
    .map(([storeId]) => storeId);
}

function availableStores(detail: ProductDetail | null) {
  return (detail?.stores || []).filter((store) => Boolean(store.enabled) && store.connection_status === "connected");
}

function runningDurationLabel(startedAt: string | undefined, nowMs: number) {
  const startedMs = startedAt ? Date.parse(startedAt) : Number.NaN;
  if (!Number.isFinite(startedMs)) return "后台进程在线";
  const seconds = Math.max(0, Math.floor((nowMs - startedMs) / 1000));
  if (seconds < 60) return `后台进程在线 · 已运行 ${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return `后台进程在线 · 已运行 ${minutes} 分 ${remainder} 秒`;
}

function CommandSearch({
  products,
  selectedProductId,
  onSelectProduct,
}: {
  products?: ProductCard[];
  selectedProductId?: string;
  onSelectProduct: (productId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="secondary" className="command-search">
          <Search className="h-4 w-4" />
          搜索商品、SKU、步骤
          <kbd>⌘K</kbd>
        </Button>
      </DialogTrigger>
      <DialogContent className="p-0">
        <Command>
          <CommandInput placeholder="搜索商品编号、标题、SKU 或当前步骤..." />
          <CommandList>
            <CommandEmpty>没有找到本地商品。换一个商品编号或标题关键词。</CommandEmpty>
            <CommandGroup heading={`最近生产任务 · ${products?.length || 0}`}>
              {(products || []).map((product) => (
                <CommandItem
                  key={product.product_id}
                  value={`${product.product_id} ${product.title_cn || ""} ${product.title_ru || ""} ${product.current_step || ""}`}
                  onSelect={() => {
                    onSelectProduct(product.product_id);
                    setOpen(false);
                  }}
                  className={cn("product-command-item", selectedProductId === product.product_id && "active")}
                >
                  <Radar className="h-4 w-4" />
                  <span>
                    <strong>{product.product_id}</strong>
                    <small>{truncate(product.title_cn || product.title_ru, 46)}</small>
                  </span>
                  <em>{productStepLabel(product)} · {product.progress}% · {product.sku_count} SKU</em>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  );
}

function productionIssue(detail: ProductDetail | null) {
  const uiState = detail?.ui_state;
  const uiTone = String(uiState?.tone || "").toLowerCase();
  if (uiState && (uiState.blocking || uiTone === "danger" || uiTone === "warning")) {
    return {
      tone: uiTone === "danger" ? "danger" : "warning",
      title: uiState.title || "需要处理",
      message: uiState.message || "当前商品需要处理后才能继续。",
    };
  }
  if (uiState && ["ok", "running", "idle"].includes(uiTone)) {
    return null;
  }
  const readiness = detail?.production_readiness;
  const readinessState = String(readiness?.state || "").toLowerCase();
  const readinessTitle =
    {
      ozon_reference_draft: "参考草稿",
      ozon_reference_generating_images: "正在生成参考图片",
      ozon_reference_images_partial: "参考图片部分完成",
      ozon_reference_needs_retry: "参考图片需要续跑",
      ozon_reference_images_generated: "参考图片已生成",
    }[readinessState] || readiness?.state || "生产提示";
  if (readinessState === "submitted_read_only" || readinessState === "legacy_submitted_read_only") {
    return null;
  }
  if (readiness?.blocking || readiness?.message || readiness?.errors?.length) {
    return {
      tone: readiness.blocking ? "danger" : "warning",
      title: readiness.blocking ? "生产被阻断" : readinessTitle,
      message: readiness.errors?.[0] || readiness.message || "当前商品需要处理后才能继续。",
    };
  }
  if (detail?.error?.message || detail?.error?.title) {
    return {
      tone: "danger",
      title: detail.error.title || "任务错误",
      message: detail.error.message || "当前商品存在任务错误。",
    };
  }
  const firstRisk = detail?.risk?.items?.[0];
  if (detail?.attention_required || firstRisk) {
    return {
      tone: String(firstRisk?.level || detail?.risk?.level || "").toLowerCase().includes("high") ? "danger" : "warning",
      title: firstRisk?.title || "需要处理",
      message: firstRisk?.message || "当前商品有需要处理的项目。",
    };
  }
  return null;
}

type StageImageItem = {
  key: string;
  url: string;
  label: string;
  state?: string;
};

function stageImageItems(detail: ProductDetail | null, card?: ProductCard): StageImageItem[] {
  const generated = (detail?.images || [])
    .filter((item) => item.url)
    .map((item, index) => ({
      key: item.slot || item.url || `image-${index}`,
      url: assetUrl(item.url),
      label: item.slot || item.role || item.type || `图 ${index + 1}`,
      state: item.state || item.status,
    }));
  if (generated.length) return generated;
  return card?.thumbnail_url ? [{
    key: "thumbnail",
    url: assetUrl(card.thumbnail_url),
    label: "商品缩略图",
    state: card.raw_status,
  }] : [];
}

export function ProductionStage({
  detail,
  card,
  products,
  error,
  loadingProducts,
  commandResult,
  actionBusy,
  onRunProduct,
  onRefreshOzonStatus,
  onRegenerateImage,
  onOpenDetail,
  onSelectProduct,
}: {
  detail: ProductDetail | null;
  card?: ProductCard;
  products?: ProductCard[];
  error?: string;
  loadingProducts?: boolean;
  commandResult?: CommandResult | null;
  actionBusy?: boolean;
  onRunProduct: () => void;
  onRefreshOzonStatus: () => void;
  onRegenerateImage: () => void;
  onOpenDetail: () => void;
  onSelectProduct: (productId: string) => void;
}) {
  const skuCount = detail?.skus?.length || card?.sku_count || 0;
  const status = detail?.status?.status || card?.raw_status || "UNKNOWN";
  const tone = statusTone(status);
  const stageImages = useMemo(() => stageImageItems(detail, card), [detail?.product_id, detail?.images, card?.product_id, card?.thumbnail_url]);
  const [selectedImageKey, setSelectedImageKey] = useState("");
  const selectedImage = stageImages.find((item) => item.key === selectedImageKey) || stageImages[0];
  const visibleStageImages = stageImages.length > 12 ? stageImages.slice(0, 11) : stageImages.slice(0, 12);
  const extraImageCount = Math.max(0, stageImages.length - visibleStageImages.length);
  const production = currentProductionState(detail, card);
  const regenSlot = selectedRegenerationSlot(detail);
  const running = isProductRunning(detail, card);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const uiState = detail?.ui_state;
  const uiPrimaryAction = uiState?.primary_action;
  const primaryActionId = String(uiPrimaryAction?.id || "");
  const primaryActionOpensDetail = primaryActionId === "view_details";
  const remoteWaiting = ["PENDING_REMOTE", "OZON_MODERATION", "HANDED_OFF_TO_OZON"].includes(String(status).toUpperCase());
  const primaryActionChecksOzon = primaryActionId === "read_only_status_query" && remoteWaiting;
  const actionLabel = uiPrimaryAction?.label || (shouldRecover(detail, card) ? "恢复任务" : "继续生产");
  const issue = productionIssue(detail);
  const submittedReadOnly = isSubmittedReadOnly(detail, card);
  const activeProductId = detail?.product_id || card?.product_id;
  const displayTone = String(uiState?.tone || tone);
  const displayStatusLabel = running ? "生产中" : uiState?.title || statusLabel(status);
  const displayStepLabel = running ? `${readableStageName(production.step)}进行中` : uiState?.progress_label || production.stepLabel;
  const activeStartedAt = detail?.status?.active_step?.started_at || detail?.status?.last_run_at;
  const runningDetail = running ? runningDurationLabel(activeStartedAt, nowMs) : "";
  const pipelineNote = detail?.pipeline_progress?.status_note || "";
  const activeAttempt = Number(detail?.pipeline_progress?.active_step_attempt || 0);
  const nextStep = (detail?.status?.pending_steps || []).find((step) => step !== production.step);
  const progressValue = Math.max(0, Math.min(100, Number(production.progress || 0)));
  const generatedImageCount = detail?.images?.filter((item) => Boolean(item.url)).length || 0;
  const expectedImageCount = detail?.image_contract?.expected_total_count || detail?.images?.length || card?.image_count || generatedImageCount;
  const activeImageCount = detail?.status?.active_image_slots?.length || 0;
  const activeImageServiceWaitCount = (detail?.status?.active_image_slots || []).reduce(
    (total, slot) => total + Number(detail?.status?.image_slot_service_wait_count_by_slot?.[slot] || 0),
    0,
  );
  const hasStepCounts = Boolean((detail?.status?.completed_steps?.length || 0) + (detail?.status?.pending_steps?.length || 0));
  const primaryActionText = uiPrimaryAction?.label || (submittedReadOnly ? (remoteWaiting ? "已提交Ozon" : "已上架") : running ? "生产中" : actionLabel);
  const stores = availableStores(detail);
  const savedStoreIds = selectedStoreIds(detail);
  const selectedStores = stores.filter((store) => savedStoreIds.includes(store.id));
  const runDisabledByStores = Boolean(activeProductId && stores.length && !savedStoreIds.length && !submittedReadOnly);
  const primaryActionDisabled =
    !activeProductId
    || actionBusy
    || uiPrimaryAction?.enabled === false
    || (!primaryActionOpensDetail && runDisabledByStores)
    || (submittedReadOnly && !primaryActionOpensDetail && !primaryActionChecksOzon)
    || (running && !primaryActionOpensDetail);

  useEffect(() => {
    if (!stageImages.length) {
      setSelectedImageKey("");
      return;
    }
    setSelectedImageKey((current) => stageImages.some((item) => item.key === current) ? current : stageImages[0].key);
  }, [stageImages]);

  useEffect(() => {
    if (!running) return undefined;
    setNowMs(Date.now());
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running, activeStartedAt]);

  function handlePrimaryAction() {
    if (primaryActionChecksOzon) {
      onRefreshOzonStatus();
      return;
    }
    if (primaryActionOpensDetail) {
      onOpenDetail();
      return;
    }
    onRunProduct();
  }

  return (
    <div className="center-stack">
      <div className="center-tools">
        <div>
          <span>JLC GLOBAL 嘉联创</span>
          <strong>商品生产控制中心</strong>
        </div>
        <CommandSearch products={products} selectedProductId={detail?.product_id || card?.product_id} onSelectProduct={onSelectProduct} />
      </div>
      {error ? (
        <Card className="error-card">
          <AlertTriangle className="h-8 w-8 text-red-300" />
          <strong>无法读取本地工作台</strong>
          <p>{error}</p>
        </Card>
      ) : loadingProducts && !detail && !card ? (
        <Card className="error-card">
          <Clock3 className="h-8 w-8 text-emerald-700" />
          <strong>正在读取商品列表</strong>
          <p>本地商品较多时需要几十秒；读取期间不会调用 Ozon 或库存接口。</p>
        </Card>
      ) : (
        <motion.section
          className="production-stage jlc-panel"
          initial={{ opacity: 0, y: 18, scale: 0.99 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ duration: 0.58, ease: [0.2, 0.8, 0.2, 1] }}
        >
          <div className="stage-scan" />
          <div className="stage-ambient-grid" />
          <CardHeader className="stage-header">
            <div>
              <div className="panel-kicker">AI 生产中心</div>
              <CardTitle>{truncate(detail?.source?.title_cn || card?.title_cn, 86)}</CardTitle>
              <div className="stage-status-line">
                <span className={cn("status-dot", displayTone === "danger" || displayTone === "warning" ? "warn" : "ok")} />
                <strong>当前：{displayStepLabel}</strong>
                <small>{running ? runningDetail : `${production.completed} 个已完成 / ${production.pending} 个待处理`}</small>
              </div>
            </div>
            <div className="stage-command-panel">
              <Badge variant={displayTone === "danger" ? "danger" : displayTone === "running" || displayTone === "warning" ? "warning" : "default"}>{displayStatusLabel}</Badge>
              <div className="stage-actions">
                {selectedStores.length > 0 && (
                  <div className="stage-store-picker" aria-label="已选目标店铺">
                    <Store className="h-3.5 w-3.5" />
                    <div className="stage-store-options">
                      {selectedStores.slice(0, 6).map((store) => (
                        <span key={store.id} className="selected" title={store.display_name || store.id}>
                          {truncate(store.display_name || store.id, 10)}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                <Button size="sm" onClick={handlePrimaryAction} disabled={primaryActionDisabled}>
                  {primaryActionChecksOzon ? <UploadCloud className="h-3.5 w-3.5" /> : <Zap className="h-3.5 w-3.5" />}
                  {primaryActionText}
                </Button>
                <Button size="sm" variant="secondary" onClick={onRegenerateImage} disabled={!activeProductId || submittedReadOnly || !regenSlot || actionBusy}>
                  <RefreshCcw className="h-3.5 w-3.5" />
                  重新生成图片
                </Button>
                <Button size="sm" variant="ghost" onClick={onOpenDetail} disabled={!activeProductId}>
                  查看详情
                </Button>
              </div>
              <small>
                {stores.length && !submittedReadOnly
                  ? savedStoreIds.length
                    ? `目标店铺：已选择 ${savedStoreIds.length} 家`
                    : "尚未选择目标店铺"
                  : regenSlot ? `图片槽位：${regenSlot}` : "等待可处理图片槽位"}
              </small>
            </div>
          </CardHeader>
          {issue && (
            <div className={cn("stage-issue-strip", issue.tone)}>
              <AlertTriangle className="h-4 w-4" />
              <div>
                <strong>{issue.title}</strong>
                <span>{issue.message}</span>
              </div>
              <Button size="sm" variant="secondary" onClick={onOpenDetail} disabled={!activeProductId}>
                查看处理方法
              </Button>
            </div>
          )}
          <CardContent className="stage-content">
            <motion.div
              className="hud-frame"
              initial={{ opacity: 0, scale: 0.985 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 0.5 }}
            >
              <div className="production-status-strip">
                <span>生产状态</span>
                <strong>{displayStepLabel}</strong>
                <Progress value={production.progress} className="h-1.5" />
              </div>
              <div className="ai-connection-line horizontal" />
              <div className="ai-connection-line vertical" />
              <div className="stage-core-label">
                <span>当前商品</span>
                <strong>{detail?.product_id || card?.product_id || "LOCAL"}</strong>
              </div>
              <div className="hud-corner tl" />
              <div className="hud-corner tr" />
              <div className="hud-corner bl" />
              <div className="hud-corner br" />
              <div className={cn("hud-image-workbench", !stageImages.length && "empty")}>
                <div className="hud-image-zone">
                  {selectedImage?.url
                    ? <img src={selectedImage.url} alt="当前商品图片" />
                    : <PackageCheck className="h-16 w-16 text-emerald-700/40" />}
                </div>
                <div className="stage-thumb-wall">
                  <div className="stage-thumb-head">
                    <span>图片墙</span>
                    <strong>{stageImages.length || 0}</strong>
                  </div>
                  <div className="stage-thumb-grid">
                    {visibleStageImages.map((item) => (
                      <button
                        key={item.key}
                        type="button"
                        className={cn("stage-thumb", selectedImage?.key === item.key && "active")}
                        onClick={() => setSelectedImageKey(item.key)}
                        title={item.label}
                      >
                        <img src={item.url} alt="" />
                        <span>{item.label}</span>
                      </button>
                    ))}
                    {extraImageCount > 0 && (
                      <button type="button" className="stage-thumb more" onClick={onOpenDetail}>
                        <strong>+{extraImageCount}</strong>
                        <span>查看全部</span>
                      </button>
                    )}
                    {!stageImages.length && <div className="stage-thumb-empty">暂无图片</div>}
                  </div>
                </div>
              </div>
              <div className="hud-metrics">
                <div className="hud-meta">
                  <span>SKU</span>
                  <strong>{skuCount}</strong>
                </div>
                <div className="hud-meta">
                  <span>进度</span>
                  <strong>{detail?.status?.progress ?? card?.progress ?? 0}%</strong>
                </div>
                <div className="hud-meta">
                  <span>图片</span>
                  <strong>{production.imageReady}/{detail?.image_contract?.expected_total_count || card?.image_count || production.imageReady}</strong>
                </div>
                <div className="hud-meta">
                  <span>批次</span>
                  <strong>{card?.batch_id || detail?.status?.ozon?.task_id || "LOCAL"}</strong>
                </div>
              </div>
            </motion.div>
            {running && (
              <motion.div
                className="production-live-monitor"
                aria-live="polite"
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
              >
                <div className="live-monitor-current">
                  <span>当前流程</span>
                  <strong>{displayStepLabel}</strong>
                  <small>
                    {runningDetail}
                    {activeAttempt > 1 ? ` · 第 ${activeAttempt} 次` : ""}
                  </small>
                </div>
                <div className="live-monitor-progress">
                  <div><strong>{progressValue}%</strong><span>总进度</span></div>
                  <Progress value={progressValue} className="h-2" />
                </div>
                <div className="live-monitor-stats">
                  <div>
                    <span>流程进度</span>
                    <strong>{hasStepCounts ? `${production.completed} 完成 / ${production.pending} 待处理` : "读取中"}</strong>
                  </div>
                  <div>
                    <span>图片进度</span>
                    <strong>{expectedImageCount ? `${generatedImageCount} / ${expectedImageCount}` : "准备中"}</strong>
                    {activeImageCount > 0 && (
                      <small>
                        当前并行 {activeImageCount} 张
                        {activeImageServiceWaitCount > 0 ? ` · 服务重连 ${activeImageServiceWaitCount} 次` : ""}
                      </small>
                    )}
                  </div>
                  <div>
                    <span>下一步</span>
                    <strong>{nextStep ? readableStageName(nextStep) : hasStepCounts ? "等待完成" : "读取中"}</strong>
                  </div>
                </div>
                {pipelineNote && (
                  <div className="live-monitor-note">
                    {pipelineNote}
                  </div>
                )}
              </motion.div>
            )}
            {commandResult && (
              <motion.div
                className={cn("command-result", commandResult.tone)}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
              >
                {commandResult.message}
              </motion.div>
            )}
          </CardContent>
        </motion.section>
      )}
    </div>
  );
}
