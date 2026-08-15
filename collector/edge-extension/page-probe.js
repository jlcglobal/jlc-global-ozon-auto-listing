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
    "detailData"
  ];
  const dynamic = Object.keys(window)
    .filter((key) => /sku|offer|detail|product|init|state|data/i.test(key))
    .slice(0, 80);
  const names = Array.from(new Set(explicit.concat(dynamic)));
  const seen = new WeakSet();

  function clone(value, depth) {
    if (value == null || depth > 6) return value == null ? value : "[depth-limit]";
    if (typeof value === "string") return value.length > 3000 ? value.slice(0, 3000) : value;
    if (typeof value === "number" || typeof value === "boolean") return value;
    if (typeof value !== "object") return undefined;
    if (seen.has(value)) return "[circular]";
    seen.add(value);
    if (Array.isArray(value)) return value.slice(0, 100).map((item) => clone(item, depth + 1));
    const out = {};
    Object.keys(value).slice(0, 180).forEach((key) => {
      if (depth <= 1 || /sku|spec|prop|offer|product|price|stock|image|pic|sale|detail|title|subject/i.test(key)) {
        const child = clone(value[key], depth + 1);
        if (child !== undefined) out[key] = child;
      }
    });
    return out;
  }

  const result = [];
  names.forEach((name) => {
    try {
      const value = window[name];
      if (value == null) return;
      const cloned = clone(value, 0);
      const sample = typeof value === "string" ? value : JSON.stringify(cloned).slice(0, 50000);
      if (/sku|规格|颜色|尺寸|型号|offer|product/i.test(sample)) {
        result.push({ name, data: cloned });
      }
    } catch {}
  });
  document.documentElement.setAttribute(attr, JSON.stringify(result).slice(0, 700000));
  window.dispatchEvent(new CustomEvent("CAF_PAGE_PRODUCT_DATA_READY"));
})();
