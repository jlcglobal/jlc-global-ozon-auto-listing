(() => {
  const attr = "data-caf-window-product-data";
  const explicit = [
    "__INIT_DATA__",
    "__INITIAL_STATE__",
    "__GLOBAL_DATA__",
    "__STORE__",
    "iDetailData",
    "offerDetailData",
    "globalData",
    "detailData",
    "context"
  ];
  const dynamic = Object.keys(window)
    .filter((key) => /sku|offer|detail|product|init|state|data|context/i.test(key))
    .slice(0, 80);
  const names = Array.from(new Set(explicit.concat(dynamic)));
  const SKU_CONTAINER_RE = /^(skuProps|skuInfoMap|skuMap|sku_props|sku_map|skuInfos|skuInfo|specProps|saleProps|propList)$/i;

  function clone(value, depth, preserveAll, seen = new WeakSet()) {
    const maxDepth = 12;
    if (value == null || depth > maxDepth) return value == null ? value : "[depth-limit]";
    if (typeof value === "string") return value.length > 3000 ? value.slice(0, 3000) : value;
    if (typeof value === "number" || typeof value === "boolean") return value;
    if (typeof value !== "object") return undefined;
    if (seen.has(value)) return "[circular]";
    seen.add(value);
    if (Array.isArray(value)) return value.slice(0, 300).map((item) => clone(item, depth + 1, preserveAll, seen));
    const out = {};
    Object.keys(value).slice(0, 180).forEach((key) => {
      const childValue = value[key];
      const skuContainer = SKU_CONTAINER_RE.test(key);
      const isContainer = childValue && typeof childValue === "object";
      // 容器键（值仍是对象/数组）必须保留以穿透深层结构，否则 SKU 数据若埋在
      // result.data.Root.fields.dataJson.skuModel 里，会在 "Root" 这类非关键词键处被丢弃。
      // 叶子字段按关键词过滤；SKU 容器整棵子树完整保留。
      const keep = preserveAll || depth <= 1 || skuContainer || isContainer
        || /sku|spec|prop|offer|product|price|stock|image|pic|sale|detail|title|subject/i.test(key);
      if (keep) {
        const child = clone(childValue, depth + 1, preserveAll || skuContainer, seen);
        if (child !== undefined) out[key] = child;
      }
    });
    return out;
  }

  // 新版 1688 把 SKU 放在 window.context 的深层 tradeModel 中。该对象
  // 同时被多个路径引用，通用快照会先遇到它并在另一条路径写成 [circular]。
  // 单独采集 skuProps/skuMap，不能让循环引用吞掉真实 SKU。
  function collectSkuModels(value, out, seen = new WeakSet(), depth = 0) {
    if (!value || typeof value !== "object" || depth > 16 || seen.has(value) || out.length >= 20) return;
    seen.add(value);
    const props = value.skuProps || value.sku_props || value.skuProp || value.specProps || value.saleProps;
    const map = value.skuMap || value.skuInfoMap || value.sku_map || value.skuInfos || value.skuInfo;
    if (Array.isArray(props) && props.length && (Array.isArray(map) || (map && typeof map === "object"))) {
      out.push({
        skuProps: clone(props, 0, true),
        skuMap: clone(map, 0, true),
        price: value.price || value.offerPrice || value.offerPriceModel || null,
        beginAmount: value.beginAmount || value.startAmount || null,
      });
    }
    Object.keys(value).slice(0, 240).forEach((key) => {
      const child = value[key];
      if (child && typeof child === "object") collectSkuModels(child, out, seen, depth + 1);
    });
  }

  const skuModels = [];
  [window.context, window.globalData, window.detailData, window.offerDetailData].forEach((value) => {
    try { collectSkuModels(value, skuModels); } catch {}
  });
  // Put the compact, authoritative SKU snapshot first.  New 1688 pages can
  // expose several very large window objects; those optional generic objects
  // must never crowd the SKU map out of the transport payload.
  const result = [];
  const modelKeys = new Set();
  skuModels.forEach((model) => {
    const key = JSON.stringify(model.skuMap || []).slice(0, 500);
    if (modelKeys.has(key)) return;
    modelKeys.add(key);
    result.push({ name: "caf_direct_sku_model", data: model });
  });
  names.forEach((name) => {
    try {
      const value = window[name];
      if (value == null) return;
      const cloned = clone(value, 0, false);
      const sample = typeof value === "string" ? value : JSON.stringify(cloned).slice(0, 50000);
      if (/sku|规格|颜色|尺寸|型号|offer|product/i.test(sample)) {
        result.push({ name, data: cloned });
      }
    } catch {}
  });
  // Do not use string.slice here: it produces invalid JSON and makes the
  // content script silently fall back to its previous/empty snapshot.  Keep
  // complete entries only, with the direct SKU model already at the front.
  const bounded = [];
  let serialized = "[]";
  result.forEach((entry) => {
    try {
      const candidate = JSON.stringify([...bounded, entry]);
      if (candidate.length > 650000) return;
      bounded.push(entry);
      serialized = candidate;
    } catch {}
  });
  document.documentElement.setAttribute(attr, serialized);
  window.dispatchEvent(new CustomEvent("CAF_PAGE_PRODUCT_DATA_READY"));
})();
