import { useEffect, useMemo, useState } from "react";
import { Check, Loader2, Rocket, Store, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { assetUrl } from "@/services/workbenchApi";
import type { CreateBatchResponse, ProductCard, ShopCard } from "@/types/workbench";

type StepKey = "products" | "shops" | "confirm";

const steps: Array<{ key: StepKey; label: string }> = [
  { key: "products", label: "选择商品" },
  { key: "shops", label: "选择店铺" },
  { key: "confirm", label: "确认启动" },
];

const storeGroupPresets = [
  {
    id: "1256",
    label: "组合 1256",
    storeIds: ["zhonglian1", "zhonglian2", "zhonglian5", "jlc-blobal-6"],
    description: "1、2、5、6 · 四个不同主体",
  },
  {
    id: "V346",
    label: "组合 V346",
    storeIds: ["volttech", "zhonglian3", "zhonglian4", "jlc-blobal-6"],
    description: "V、3、4、6 · 四个不同主体",
  },
] as const;

function isConnectedShop(shop: ShopCard) {
  return Boolean(shop.enabled) && shop.connection_status === "connected";
}

function resultMessage(result: CreateBatchResponse) {
  if (result.message) return result.message;
  if (result.status === "started") return `批次已启动：${result.batch_id}`;
  if (result.status === "queued") return `批次已排队：${result.batch_id}，位置 ${result.queue_position || 1}`;
  if (result.status === "already_queued") return `所选商品已在队列中：${(result.existing_batch_ids || []).join("、")}`;
  if (result.status === "empty") return "没有可启动的商品";
  return `工作台返回：${result.status}`;
}

export function BatchLauncherDrawer({
  open,
  onOpenChange,
  products,
  shops,
  initialProductId,
  loading,
  error,
  onRefresh,
  onCreateBatch,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  products?: ProductCard[];
  shops?: ShopCard[];
  initialProductId?: string;
  loading?: boolean;
  error?: string;
  onRefresh: () => Promise<unknown>;
  onCreateBatch: (productIds: string[], storeIds: string[]) => Promise<CreateBatchResponse>;
  onCreated: (result: CreateBatchResponse) => void;
}) {
  const [step, setStep] = useState<StepKey>("products");
  const [selectedProducts, setSelectedProducts] = useState<string[]>([]);
  const [selectedStores, setSelectedStores] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState("");
  const [lastResult, setLastResult] = useState<CreateBatchResponse | null>(null);
  const focusedLaunch = Boolean(initialProductId);

  const connectedShops = useMemo(() => (shops || []).filter(isConnectedShop), [shops]);
  const selectedProductCards = useMemo(
    () => (products || []).filter((product) => selectedProducts.includes(product.product_id)),
    [products, selectedProducts],
  );
  const selectedShopCards = useMemo(
    () => (shops || []).filter((shop) => selectedStores.includes(shop.id)),
    [shops, selectedStores],
  );
  const selectedPreset = useMemo(() => storeGroupPresets.find((preset) =>
    preset.storeIds.length === selectedStores.length && preset.storeIds.every((storeId) => selectedStores.includes(storeId)),
  ), [selectedStores]);
  const invalidStoreGroup = selectedStores.length > 1 && !selectedPreset;
  const storeSelectionMessage = selectedPreset
    ? `已选择 ${selectedPreset.label}：${selectedPreset.description}`
    : invalidStoreGroup
      ? "多店仅允许组合 1256 或 V346；其他情况请只选一家店。"
      : selectedStores.length === 1
        ? "单店上架：只生成这一家店的商品卡。"
        : "请选择一家店，或直接选择跨主体组合 1256 / V346。";

  useEffect(() => {
    if (!open) return;
    setStep(initialProductId ? "shops" : "products");
    setLocalError("");
    setLastResult(null);
    setSelectedProducts(initialProductId ? [initialProductId] : []);
    onRefresh().catch(() => null);
  }, [initialProductId, open]);

  function toggleProduct(productId: string) {
    setLastResult(null);
    setSelectedProducts((current) =>
      current.includes(productId) ? current.filter((id) => id !== productId) : [...current, productId],
    );
  }

  function toggleStore(storeId: string) {
    setLastResult(null);
    setSelectedStores((current) =>
      current.includes(storeId) ? current.filter((id) => id !== storeId) : [...current, storeId],
    );
  }

  function chooseStoreGroup(groupId: string) {
    const preset = storeGroupPresets.find((item) => item.id === groupId);
    if (!preset) return;
    const unavailable = preset.storeIds.filter((storeId) => !connectedShops.some((shop) => shop.id === storeId));
    if (unavailable.length) {
      setLocalError(`${preset.label} 有不可用店铺：${unavailable.join("、")}`);
      return;
    }
    setLastResult(null);
    setLocalError("");
    setSelectedStores([...preset.storeIds]);
  }

  function goNext() {
    setLocalError("");
    if (step === "products") {
      if (!selectedProducts.length) {
        setLocalError("至少选择一个商品。");
        return;
      }
      setStep("shops");
      return;
    }
    if (step === "shops") {
      if (!selectedStores.length) {
        setLocalError("至少选择一个已连接店铺。");
        return;
      }
      if (invalidStoreGroup) {
        setLocalError("多店仅允许组合 1256 或 V346；其他情况请只选一家店。");
        return;
      }
      setStep("confirm");
    }
  }

  async function submit() {
    if (!selectedProducts.length || !selectedStores.length) return;
    setBusy(true);
    setLocalError("");
    setLastResult(null);
    try {
      const result = await onCreateBatch(selectedProducts, selectedStores);
      setLastResult(result);
      onCreated(result);
      if (["started", "queued", "already_queued"].includes(result.status)) {
        window.setTimeout(() => onOpenChange(false), 900);
      }
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "创建批次失败");
    } finally {
      setBusy(false);
    }
  }

  const canContinue = step === "products" ? selectedProducts.length > 0 : selectedStores.length > 0 && !invalidStoreGroup;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="batch-launcher">
        <SheetHeader className="batch-launcher-head">
          <div className="panel-kicker">采集箱生产</div>
          <SheetTitle>{focusedLaunch ? "选择店铺并启动当前商品" : "启动采集箱商品"}</SheetTitle>
          <SheetDescription>
            {focusedLaunch ? "当前采集商品已锁定，只需要选择目标店铺即可启动生产。" : "选择采集箱商品和目标店铺后，交给现有生产引擎自动执行。"}
          </SheetDescription>
        </SheetHeader>

        {!focusedLaunch ? (
          <div className="launcher-steps">
            {steps.map((item, index) => (
              <button
                key={item.key}
                type="button"
                className={cn("launcher-step", step === item.key && "active")}
                onClick={() => setStep(item.key)}
              >
                <span>{index + 1}</span>
                {item.label}
              </button>
            ))}
          </div>
        ) : (
          <div className="launcher-direct-step">
            <span>当前商品</span>
            <strong>{selectedProducts[0] || "等待商品加载"}</strong>
          </div>
        )}

        {focusedLaunch && selectedProductCards.length > 0 && (
          <div className="launcher-selected-product">
            {selectedProductCards.map((product) => (
              <button
                key={product.product_id}
                type="button"
                className="launcher-product selected"
                disabled
              >
                <span className="launcher-thumb">
                  {product.thumbnail_url ? <img src={assetUrl(product.thumbnail_url)} alt="" /> : product.product_id.slice(-2)}
                </span>
                <span>
                  <strong>{product.title_cn || product.title_ru || product.product_id}</strong>
                  <small>{product.product_id} · SKU {product.sku_count || 0} · {product.current_step || product.state}</small>
                </span>
                <Badge variant="default">已选定</Badge>
              </button>
            ))}
          </div>
        )}

        {(error || localError) && (
          <div className="launcher-alert">
            <XCircle className="h-4 w-4" />
            <span>{localError || error}</span>
          </div>
        )}
        {lastResult && (
          <div className="launcher-result">
            <Check className="h-4 w-4" />
            <span>{resultMessage(lastResult)}</span>
          </div>
        )}

        <ScrollArea className="launcher-body">
          {loading ? (
            <div className="launcher-loading">
              <Loader2 className="h-5 w-5 animate-spin" />
              正在读取工作台数据
            </div>
          ) : step === "products" ? (
            <div className="launcher-grid">
              {(products || []).map((product) => (
                <button
                  key={product.product_id}
                  type="button"
                  className={cn("launcher-product", selectedProducts.includes(product.product_id) && "selected")}
                  onClick={() => toggleProduct(product.product_id)}
                >
                  <span className="launcher-thumb">
                    {product.thumbnail_url ? <img src={assetUrl(product.thumbnail_url)} alt="" /> : product.product_id.slice(-2)}
                  </span>
                  <span>
                    <strong>{product.title_cn || product.title_ru || product.product_id}</strong>
                    <small>{product.product_id} · SKU {product.sku_count || 0} · {product.current_step || product.state}</small>
                  </span>
                  <Badge variant={selectedProducts.includes(product.product_id) ? "default" : "muted"}>
                    {product.progress || 0}%
                  </Badge>
                </button>
              ))}
              {!(products || []).length && <div className="launcher-empty">当前没有商品可选</div>}
            </div>
              ) : step === "shops" ? (
                <div className="launcher-grid">
              <section className="launcher-store-groups" aria-label="跨主体店群组合">
                <div className="launcher-store-groups-head">
                  <div>
                    <span className="panel-kicker">跨主体组合</span>
                    <strong>一键选择合规店群</strong>
                  </div>
                  <Button size="sm" variant="ghost" onClick={() => { setSelectedStores([]); setLocalError(""); }} disabled={!selectedStores.length || busy}>
                    清空选择
                  </Button>
                </div>
                <div className="launcher-store-group-options">
                  {storeGroupPresets.map((preset) => {
                    const unavailable = preset.storeIds.filter((storeId) => !connectedShops.some((shop) => shop.id === storeId));
                    const selected = selectedPreset?.id === preset.id;
                    return (
                      <button
                        key={preset.id}
                        type="button"
                        className={cn("launcher-store-group", selected && "selected")}
                        disabled={Boolean(unavailable.length) || busy}
                        onClick={() => chooseStoreGroup(preset.id)}
                      >
                        <strong>{preset.label}</strong>
                        <small>{preset.description}{unavailable.length ? " · 有店铺不可用" : ""}</small>
                      </button>
                    );
                  })}
                </div>
                <p className={cn(invalidStoreGroup && "error")}>{storeSelectionMessage}</p>
              </section>
              {(shops || []).map((shop) => {
                const available = isConnectedShop(shop);
                return (
                  <button
                    key={shop.id}
                    type="button"
                    className={cn("launcher-shop", selectedStores.includes(shop.id) && "selected", !available && "disabled")}
                    disabled={!available}
                    onClick={() => toggleStore(shop.id)}
                  >
                    <Store className="h-5 w-5" />
                    <span>
                      <strong>{shop.display_name || shop.id}</strong>
                      <small>{shop.connection_status || "unknown"} · {shop.credentials_display || "credentials hidden"}</small>
                    </span>
                    <Badge variant={available ? "default" : "warning"}>{available ? "可用" : "不可用"}</Badge>
                  </button>
                );
              })}
              {!(shops || []).length && <div className="launcher-empty">没有店铺配置</div>}
            </div>
          ) : (
            <div className="launcher-confirm">
              <section>
                <span className="panel-kicker">商品</span>
                {selectedProductCards.map((product) => (
                  <div key={product.product_id} className="confirm-line">
                    <strong>{product.title_cn || product.product_id}</strong>
                    <span>{product.product_id} · SKU {product.sku_count || 0}</span>
                  </div>
                ))}
              </section>
              <section>
                <span className="panel-kicker">店铺</span>
                {selectedShopCards.map((shop) => (
                  <div key={shop.id} className="confirm-line">
                    <strong>{shop.display_name || shop.id}</strong>
                    <span>{shop.id}</span>
                  </div>
                ))}
              </section>
              <div className="confirm-note">
                当前后端批次创建接口是自动模式：提交后会进入启动或排队，不需要旧工作台的人工确认接口。
              </div>
            </div>
          )}
        </ScrollArea>

        <div className="launcher-actions">
          <Button variant="secondary" onClick={() => onOpenChange(false)} disabled={busy}>
            关闭
          </Button>
          {!focusedLaunch && step !== "products" && (
            <Button variant="ghost" onClick={() => setStep(step === "confirm" ? "shops" : "products")} disabled={busy}>
              上一步
            </Button>
          )}
          {focusedLaunch ? (
          <Button onClick={submit} disabled={!selectedProducts.length || !selectedStores.length || invalidStoreGroup || busy}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Rocket className="h-4 w-4" />}
              启动生产
            </Button>
          ) : step !== "confirm" ? (
            <Button onClick={goNext} disabled={!canContinue || busy}>
              下一步
            </Button>
          ) : (
            <Button onClick={submit} disabled={!selectedProducts.length || !selectedStores.length || invalidStoreGroup || busy}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Rocket className="h-4 w-4" />}
              启动生产
            </Button>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
