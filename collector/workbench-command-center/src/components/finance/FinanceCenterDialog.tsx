import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Boxes,
  ChartPie,
  Check,
  FileSpreadsheet,
  ListOrdered,
  RefreshCw,
  Search,
  TrendingUp,
  Upload,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  commitFinanceImport,
  loadFinanceOrders,
  loadFinanceOverview,
  loadFinanceProducts,
  loadFinanceReconciliation,
  loadFinanceSyncStatus,
  previewFinanceImport,
  saveFinanceSkuPurchaseCost,
  startFinanceSync,
} from "@/services/workbenchApi";
import type {
  FinanceOrder,
  FinanceOverview,
  FinanceProduct,
  FinanceReconciliationItem,
  FinanceSyncStatus,
} from "@/types/finance";

type FinanceTab = "overview" | "orders" | "products" | "reconciliation";

const tabs: Array<{ key: FinanceTab; label: string; icon: typeof TrendingUp }> = [
  { key: "overview", label: "概览", icon: TrendingUp },
  { key: "orders", label: "订单", icon: ListOrdered },
  { key: "products", label: "商品", icon: Boxes },
  { key: "reconciliation", label: "待核对", icon: AlertTriangle },
];

function localIso(day: Date) {
  const offset = day.getTimezoneOffset() * 60_000;
  return new Date(day.getTime() - offset).toISOString().slice(0, 10);
}

function defaultPeriod() {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - 89);
  return { dateFrom: localIso(start), dateTo: localIso(end) };
}

function cny(value?: string | number) {
  const number = Number(value || 0);
  return `¥${number.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function percent(value?: number) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

function costSourceLabel(source?: string) {
  if (source === "actual_finance" || source === "actual_order") return "实际";
  if (source === "period_sales_allocation" || source === "actual_and_period_allocation") return "分摊估算";
  if (source === "missing") return "未返回";
  return "历史估算";
}

function shortTime(value?: string | null) {
  if (!value) return "尚未更新";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", { hour12: false });
}

function fileBase64(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || "").split(",").pop() || "");
    reader.onerror = () => reject(new Error("采购表读取失败"));
    reader.readAsDataURL(file);
  });
}

export function FinanceCenterDialog({
  open,
  onOpenChange,
  onResult,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onResult?: (message: string, tone: "ok" | "danger" | "idle") => void;
}) {
  const initial = useMemo(defaultPeriod, []);
  const fileInput = useRef<HTMLInputElement>(null);
  const [tab, setTab] = useState<FinanceTab>("overview");
  const [storeId, setStoreId] = useState("all");
  const [dateFrom, setDateFrom] = useState(initial.dateFrom);
  const [dateTo, setDateTo] = useState(initial.dateTo);
  const [query, setQuery] = useState("");
  const [overview, setOverview] = useState<FinanceOverview | null>(null);
  const [orders, setOrders] = useState<FinanceOrder[]>([]);
  const [products, setProducts] = useState<FinanceProduct[]>([]);
  const [reconciliation, setReconciliation] = useState<FinanceReconciliationItem[]>([]);
  const [reconciliationCounts, setReconciliationCounts] = useState<Record<string, number>>({});
  const [syncStatus, setSyncStatus] = useState<FinanceSyncStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [costEditor, setCostEditor] = useState<{ orderId: string; sku: string } | null>(null);
  const [costValue, setCostValue] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (!open) return;
    setError("");
    const filters = { storeId, dateFrom, dateTo, limit: 200, q: query.trim() };
    try {
      const [nextOverview, nextSync] = await Promise.all([
        loadFinanceOverview(filters),
        loadFinanceSyncStatus(),
      ]);
      setOverview(nextOverview);
      setSyncStatus(nextSync);
      if (tab === "orders") {
        const nextOrders = await loadFinanceOrders(filters);
        setOrders(nextOrders.items || []);
      } else if (tab === "products") {
        const nextProducts = await loadFinanceProducts(filters);
        setProducts(nextProducts.items || []);
      } else if (tab === "reconciliation") {
        const nextReconciliation = await loadFinanceReconciliation(storeId, 200);
        setReconciliation(nextReconciliation.items || []);
        setReconciliationCounts(nextReconciliation.counts || {});
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "财务利润读取失败");
    }
  }, [dateFrom, dateTo, open, query, storeId, tab]);

  useEffect(() => {
    if (!open) return undefined;
    refresh();
    const timer = window.setInterval(() => {
      if (syncStatus?.runs?.some((item) => item.status === "running")) refresh();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [open, refresh, syncStatus?.runs]);

  async function handleSync() {
    setBusy(true);
    setError("");
    try {
      const result = await startFinanceSync();
      setNotice(result.message);
      onResult?.(result.message, "idle");
      window.setTimeout(() => refresh(), 1200);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Ozon 财务读取启动失败";
      setError(message);
      onResult?.(message, "danger");
    } finally {
      setBusy(false);
    }
  }

  async function handlePurchaseFile(file?: File) {
    if (!file) return;
    setBusy(true);
    setError("");
    setNotice("正在核对订单号和 SKU...");
    try {
      const content = await fileBase64(file);
      const preview = await previewFinanceImport({ file_name: file.name, content_base64: content, file_kind: "purchase_cost" });
      const choose = (target: string, preferred: string) => {
        if (preview.headers.includes(preferred)) return preferred;
        return preview.mapping_candidates
          .filter((item) => item.target_field === target)
          .sort((a, b) => b.confidence - a.confidence)[0]?.source_header || "";
      };
      const mapping = {
        [choose("store_id", "店铺")]: "store_id",
        [choose("product_name", "名称")]: "product_name",
        [choose("order_number", "订单号")]: "order_number",
        [choose("purchase_cost_cny", "采购成本")]: "purchase_cost_cny",
      };
      if (Object.keys(mapping).some((key) => !key)) throw new Error("这份采购表缺少名称、店铺、订单号或采购成本");
      const result = await commitFinanceImport({
        file_name: file.name,
        content_base64: content,
        file_kind: "purchase_cost",
        mapping,
      });
      const message = `采购价已同步 ${result.matched_count} 行，${result.unmatched_count} 行进入待核对`;
      setNotice(message);
      onResult?.(message, "ok");
      await refresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : "采购表同步失败";
      setError(message);
      onResult?.(message, "danger");
    } finally {
      if (fileInput.current) fileInput.current.value = "";
      setBusy(false);
    }
  }

  async function handleSkuPurchaseCost() {
    if (!costEditor) return;
    const amount = Number(costValue);
    if (!Number.isFinite(amount) || amount <= 0) {
      setError("请输入正确的采购价");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await saveFinanceSkuPurchaseCost({ sku: costEditor.sku, purchase_cost_cny: amount });
      const message = `采购价已同步到 ${result.affected_store_count} 个店铺、${result.affected_order_count} 个相同 SKU 订单`;
      setNotice(message);
      onResult?.(message, "ok");
      setCostEditor(null);
      setCostValue("");
      await refresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : "采购价同步失败";
      setError(message);
      onResult?.(message, "danger");
    } finally {
      setBusy(false);
    }
  }

  const syncRunning = syncStatus?.runs?.some((item) => item.status === "running");
  const storeNameById = new Map((overview?.stores || []).map((store) => [store.id, store.name]));
  const profitMargin = Math.max(0, Math.min(1, Number(overview?.summary.expected_margin || 0)));
  const salesAmount = Number(overview?.summary.sales || 0);
  const profitAmount = Number(overview?.summary.expected_profit || 0);
  const costAmount = Math.max(0, salesAmount - profitAmount);
  const metrics = overview ? [
    { label: "销售额", value: cny(overview.summary.sales) },
    { label: "Ozon费用", value: cny(overview.summary.ozon_fees) },
    { label: "运费", value: cny(overview.summary.logistics) },
    { label: "广告费用", value: cny(overview.summary.ad_spend) },
    { label: "预计利润", value: cny(overview.summary.expected_profit) },
  ] : [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="finance-dialog">
        <DialogHeader className="finance-dialog-header">
          <div>
            <DialogTitle><span className="finance-circle-icon finance-circle-icon-lg"><ChartPie className="h-4 w-4" /></span>财务利润</DialogTitle>
            <DialogDescription>按 Ozon 订单核算，采购成本来自你导入的采购表。</DialogDescription>
          </div>
          <div className="finance-header-actions">
            <input ref={fileInput} hidden type="file" accept=".xlsx,.csv,.tsv" onChange={(event) => handlePurchaseFile(event.target.files?.[0])} />
            <Button variant="secondary" size="sm" onClick={() => fileInput.current?.click()} disabled={busy}>
              <Upload className="h-4 w-4" />导入采购表
            </Button>
            <Button size="sm" onClick={handleSync} disabled={busy || syncRunning}>
              <RefreshCw className={syncRunning ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
              {syncRunning ? "正在更新" : "更新 Ozon 数据"}
            </Button>
          </div>
        </DialogHeader>

        <div className="finance-toolbar">
          <div className="finance-tabs">
            {tabs.map((item) => {
              const Icon = item.icon;
              const count = item.key === "reconciliation"
                ? Object.values(reconciliationCounts).reduce((sum, value) => sum + Number(value || 0), 0)
                : 0;
              return (
                <button key={item.key} className={tab === item.key ? "active" : ""} onClick={() => setTab(item.key)}>
                  <Icon className="h-4 w-4" />{item.label}{count > 0 ? <span>{count}</span> : null}
                </button>
              );
            })}
          </div>
          <div className="finance-filters">
            <select value={storeId} onChange={(event) => setStoreId(event.target.value)}>
              <option value="all">全部店铺</option>
              {(overview?.stores || []).map((store) => <option key={store.id} value={store.id}>{store.name}</option>)}
            </select>
            <input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
            <span>至</span>
            <input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
            {(tab === "orders" || tab === "products") ? (
              <label className="finance-search"><Search className="h-4 w-4" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="订单号或 SKU" /></label>
            ) : null}
            <Button variant="secondary" size="icon" aria-label="刷新财务数据" onClick={refresh}><RefreshCw className="h-4 w-4" /></Button>
          </div>
        </div>

        <div className="finance-status-line">
          <span className={syncRunning ? "running" : "ok"}>{syncRunning ? "Ozon 数据读取中" : `最近更新 ${shortTime(syncStatus?.last_successful_sync_at)}`}</span>
          <span>采购覆盖 {percent(overview?.coverage.purchase)}</span>
          <span>Ozon 写入 0</span>
        </div>
        {notice ? <div className="finance-notice ok">{notice}</div> : null}
        {error ? <div className="finance-notice danger">{error}</div> : null}

        <div className="finance-body">
          {tab === "overview" && overview ? (
            <div className="finance-overview">
              <div className="finance-metrics">
                {metrics.map((metric) => <article key={metric.label}><span>{metric.label}</span><strong>{metric.value}</strong></article>)}
              </div>
              <section className="finance-profit-chart">
                <header><h3>利润结构</h3><small>当前所选日期与店铺</small></header>
                <div className="finance-profit-chart-body">
                  <div
                    className="finance-profit-ring"
                    style={{ background: `conic-gradient(#34d399 0deg ${profitMargin * 360}deg, rgba(148,163,184,.18) ${profitMargin * 360}deg 360deg)` }}
                    aria-label={`预计利润率 ${percent(overview.summary.expected_margin)}`}
                  >
                    <div><strong>{percent(overview.summary.expected_margin)}</strong><span>预计利润率</span></div>
                  </div>
                  <div className="finance-profit-legend">
                    <div><i className="profit" /><span>预计利润</span><strong>{cny(profitAmount)}</strong></div>
                    <div><i className="cost" /><span>成本费用</span><strong>{cny(costAmount)}</strong></div>
                    <div><i className="ads" /><span>其中广告</span><strong>{cny(overview.summary.ad_spend)}</strong></div>
                  </div>
                </div>
                <p>{overview.warnings?.[0] || "缺失费用会按已覆盖订单估算，不冒充最终利润。"}</p>
              </section>
              <section className="finance-coverage">
                <header><h3>数据完整度</h3><small>数据越完整，利润越接近真实结果</small></header>
                {Object.entries({ 采购成本: overview.coverage.purchase, Ozon费用: overview.coverage.finance, 物流费用: overview.coverage.logistics, 广告费用: overview.coverage.ads }).map(([label, value]) => (
                  <div key={label}><span>{label}</span><div><i style={{ width: percent(value) }} /></div><strong>{percent(value)}</strong></div>
                ))}
              </section>
              <section className="finance-warning-band">
                <FileSpreadsheet className="h-5 w-5" />
                <div><strong>费用计算说明</strong><p>{overview.warnings?.[1]}</p></div>
              </section>
            </div>
          ) : null}

          {tab === "orders" ? (
            <div className="finance-table-wrap"><table><thead><tr><th>订单 / 商品</th><th>店铺</th><th>销售额</th><th>采购成本</th><th>Ozon费用</th><th>运费</th><th>广告</th><th>预计利润</th><th>完整度</th></tr></thead><tbody>
              {orders.map((item) => {
                const sku = item.offer_id || item.sku || "";
                const editing = costEditor?.orderId === item.id;
                return (
                  <tr key={item.id}>
                    <td><div className="finance-product-cell">{item.image_url ? <img src={item.image_url} alt="" /> : <span className="image-empty" />}<div><strong>{item.product_name || sku}</strong><small>订单号：{item.posting_number || item.order_number || "未读取"} · SKU：{sku || "未读取"}</small></div></div></td>
                    <td className="finance-store-name">{storeNameById.get(item.store_id) || item.store_id}</td>
                    <td>{cny(item.buyer_paid_cny)}</td>
                    <td>{editing ? (
                      <div className="finance-cost-editor">
                        <span>¥</span>
                        <input autoFocus type="number" min="0.01" step="0.01" value={costValue} onChange={(event) => setCostValue(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") handleSkuPurchaseCost(); }} />
                        <button title="保存并同步相同 SKU" onClick={handleSkuPurchaseCost} disabled={busy}><Check className="h-3.5 w-3.5" /></button>
                        <button title="取消" onClick={() => { setCostEditor(null); setCostValue(""); }} disabled={busy}><X className="h-3.5 w-3.5" /></button>
                      </div>
                    ) : cny(item.purchase_cost_cny)}</td>
                    <td className="finance-money-source">{cny(item.finance_fee_cny)}<small>{costSourceLabel(item.cost_sources.finance)}</small></td>
                    <td className="finance-money-source">{cny(item.logistics_cny)}<small>{costSourceLabel(item.cost_sources.logistics)}</small></td>
                    <td className="finance-money-source">{cny(item.ad_spend_cny)}<small>{costSourceLabel(item.cost_sources.ads)}</small></td>
                    <td className={Number(item.profit_cny) >= 0 ? "profit-positive" : "profit-negative"}>{cny(item.profit_cny)}<small>{percent(item.profit_margin)}{item.has_estimates ? " · 含估算" : ""}</small></td>
                    <td>{item.coverage.purchase ? <span className="coverage-ok">有采购价</span> : (
                      <button className="finance-fill-cost" onClick={() => { setCostEditor({ orderId: item.id, sku }); setCostValue(""); }} disabled={!sku || busy}>填写采购价</button>
                    )}</td>
                  </tr>
                );
              })}
            </tbody></table></div>
          ) : null}

          {tab === "products" ? (
            <div className="finance-table-wrap"><table><thead><tr><th>商品</th><th>店铺</th><th>订单行</th><th>销售额</th><th>预计利润</th><th>利润率</th><th>缺采购价</th></tr></thead><tbody>
              {products.map((item) => <tr key={`${item.store_id}-${item.sku}`}><td><div className="finance-product-cell">{item.image_url ? <img src={item.image_url} alt="" /> : <span className="image-empty" />}<div><strong>{item.product_name || item.offer_id || item.sku}</strong><small>SKU：{item.offer_id || item.sku}</small></div></div></td><td className="finance-store-name">{storeNameById.get(item.store_id) || item.store_id}</td><td>{item.order_lines}</td><td>{cny(item.sales_cny)}</td><td className={Number(item.profit_cny) >= 0 ? "profit-positive" : "profit-negative"}>{cny(item.profit_cny)}</td><td>{percent(item.profit_margin)}</td><td>{item.missing_purchase_lines || "-"}</td></tr>)}
            </tbody></table></div>
          ) : null}

          {tab === "reconciliation" ? (
            <div className="finance-table-wrap"><table><thead><tr><th>类型</th><th>订单号</th><th>SKU</th><th>金额</th><th>店铺</th><th>原因</th></tr></thead><tbody>
              {reconciliation.map((item) => <tr key={item.id}><td>{item.file_type === "purchase_cost" ? "采购价" : item.file_type}</td><td>{item.posting_number || item.order_number || "-"}</td><td>{item.sku || "-"}</td><td>{cny(item.amount_cny)}</td><td>{item.store_id}</td><td className="reason-cell">{item.reason}</td></tr>)}
            </tbody></table></div>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
