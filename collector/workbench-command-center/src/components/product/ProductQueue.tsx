import { useMemo, useState } from "react";
import { PackageCheck, Search } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { assetUrl } from "@/services/workbenchApi";
import { cn, truncate } from "@/lib/utils";
import { productStepLabel } from "@/lib/workbenchFormat";
import type { ProductCard } from "@/types/workbench";

function isInboxProduct(product: ProductCard) {
  const status = String(product.raw_status || "").toUpperCase();
  const bucket = String(product.workflow_bucket || "");
  const step = String(product.current_step || "");
  return status === "COLLECTED" || bucket.includes("采集箱") || step === "collect_source";
}

export function ProductQueue({
  products,
  selectedProductId,
  loading,
  error,
  onSelectProduct,
  onLaunchProduct,
}: {
  products?: ProductCard[];
  selectedProductId?: string;
  loading?: boolean;
  error?: string;
  onSelectProduct: (productId: string) => void;
  onLaunchProduct?: (productId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [viewMode, setViewMode] = useState<"grouped" | "all">("grouped");
  const filteredProducts = useMemo(() => {
    const items = products || [];
    const term = query.trim().toLowerCase();
    if (!term) return items;
    return items.filter((product) => [
      product.product_id,
      product.title_cn,
      product.title_ru,
      product.current_step,
      product.raw_status,
      product.state,
    ].some((value) => String(value || "").toLowerCase().includes(term)));
  }, [products, query]);
  const selectedProduct = filteredProducts.find((product) => product.product_id === selectedProductId);
  const visibleProducts = selectedProductId
    ? filteredProducts.filter((product) => product.product_id !== selectedProductId)
    : filteredProducts;
  const allInboxProducts = filteredProducts.filter(isInboxProduct);
  const allProductionProducts = filteredProducts.filter((product) => !isInboxProduct(product));
  const inboxProducts = visibleProducts.filter(isInboxProduct);
  const productionProducts = visibleProducts.filter((product) => !isInboxProduct(product));
  const allVisibleProducts = visibleProducts;

  function renderProduct(product: ProductCard, mode: "inbox" | "production") {
    return (
      <article
        key={product.product_id}
        className={cn("queue-item", selectedProductId === product.product_id && "active", mode)}
      >
        <button type="button" className="queue-main" onClick={() => onSelectProduct(product.product_id)}>
          {product.thumbnail_url ? <img src={assetUrl(product.thumbnail_url)} alt="" /> : <PackageCheck className="h-5 w-5" />}
          <span>
            <strong>{truncate(product.title_cn || product.title_ru || product.product_id, 24)}</strong>
            <small>{product.product_id} · {product.sku_count} SKU · {productStepLabel(product)}</small>
          </span>
          <em>{product.progress}%</em>
        </button>
        {mode === "inbox" && onLaunchProduct && (
          <button type="button" className="queue-launch" onClick={() => onLaunchProduct(product.product_id)}>
            选择店铺
          </button>
        )}
      </article>
    );
  }

  return (
    <div className="queue-list">
      <div className="queue-title">
        <span>商品工作区</span>
        <strong>{loading ? "..." : products?.length || 0}</strong>
      </div>
      <div className="queue-summary">
        <span><b>{filteredProducts.length}</b> 全部商品</span>
        <span><b>{allInboxProducts.length}</b> 新采集未生产</span>
        <span><b>{allProductionProducts.length}</b> 已生产/上传中</span>
      </div>
      <div className="queue-view-switch" role="tablist" aria-label="商品列表视图">
        <button
          type="button"
          className={viewMode === "grouped" ? "active" : ""}
          onClick={() => setViewMode("grouped")}
        >
          按状态分组
        </button>
        <button
          type="button"
          className={viewMode === "all" ? "active" : ""}
          onClick={() => setViewMode("all")}
        >
          全部商品
        </button>
      </div>
      <label className="queue-search">
        <Search className="h-3.5 w-3.5" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索商品编号、标题、步骤"
        />
      </label>
      <ScrollArea className="h-[250px]">
        <div className="queue-items">
          {loading && !(products || []).length ? (
            <div className="queue-empty">正在读取商品列表</div>
          ) : error && !(products || []).length ? (
            <div className="queue-empty danger">{error}</div>
          ) : (
            <>
              {selectedProduct && (
                <section className="queue-section current">
                  <div className="queue-section-title">
                    <span>当前查看</span>
                    <strong>{productStepLabel(selectedProduct)}</strong>
                  </div>
                  {renderProduct(selectedProduct, isInboxProduct(selectedProduct) ? "inbox" : "production")}
                </section>
              )}
              {viewMode === "all" ? (
                <section className="queue-section">
                  <div className="queue-section-title">
                    <span>全部商品</span>
                    <strong>{filteredProducts.length}</strong>
                  </div>
                  {allVisibleProducts.length ? allVisibleProducts.map((product) => (
                    renderProduct(product, isInboxProduct(product) ? "inbox" : "production")
                  )) : (
                    <div className="queue-empty compact">没有可显示商品</div>
                  )}
                </section>
              ) : (
                <>
                  <section className="queue-section">
                    <div className="queue-section-title">
                      <span>新采集，未开始生产</span>
                      <strong>{allInboxProducts.length}</strong>
                    </div>
                    {inboxProducts.length ? inboxProducts.map((product) => renderProduct(product, "inbox")) : (
                      <div className="queue-empty compact">暂无新采集未生产商品</div>
                    )}
                  </section>
                  <section className="queue-section">
                    <div className="queue-section-title">
                      <span>已生产 / 上传中 / 需要处理</span>
                      <strong>{allProductionProducts.length}</strong>
                    </div>
                    {productionProducts.length ? productionProducts.map((product) => renderProduct(product, "production")) : (
                      <div className="queue-empty compact">暂无已生产或上传中的商品</div>
                    )}
                  </section>
                </>
              )}
            </>
          )}
          {!loading && !error && query && !filteredProducts.length && <div className="queue-empty">没有匹配商品</div>}
          {!loading && !error && !(products || []).length && <div className="queue-empty">暂无商品</div>}
        </div>
      </ScrollArea>
    </div>
  );
}
