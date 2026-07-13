const PLUGIN_VERSION = "0.4.7";
const MAX_SELECTED_SKUS = 10;
const DEFAULT_FACTORY_URL = "http://127.0.0.1:8765";
let latestDrawerCapture = null;
let localCategoryTreeCachePromise = null;
let localCategoryRulesCachePromise = null;
let pageWindowProductData = [];
let pageProbeInjected = false;
const PAGE_PROBE_ATTR = "data-caf-window-product-data";

async function loadFactoryConnection() {
  const stored = await chrome.storage.local.get(["factoryBaseUrl", "factoryAccessCode"]);
  return {
    baseUrl: String(stored.factoryBaseUrl || DEFAULT_FACTORY_URL).replace(/\/$/, ""),
    accessCode: String(stored.factoryAccessCode || "").trim()
  };
}

window.addEventListener("CAF_PAGE_PRODUCT_DATA_READY", () => {
  const text = document.documentElement.getAttribute(PAGE_PROBE_ATTR);
  const parsed = parseJsonCandidate(text);
  if (Array.isArray(parsed)) pageWindowProductData = parsed;
});

function injectPageProbe() {
  if (pageProbeInjected || typeof chrome === "undefined" || !chrome.runtime?.getURL) return;
  pageProbeInjected = true;
  try {
    const script = document.createElement("script");
    script.src = chrome.runtime.getURL("page-probe.js");
    script.onload = () => script.remove();
    script.onerror = () => script.remove();
    (document.head || document.documentElement).appendChild(script);
  } catch {
    pageProbeInjected = false;
  }
}

injectPageProbe();

function textOf(node) {
  return node ? (node.textContent || "").replace(/\s+/g, " ").trim() : "";
}

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

function decodeHtmlEntities(value) {
  const text = String(value ?? "");
  if (!/[&<>]/.test(text)) return text;
  const textarea = document.createElement("textarea");
  textarea.innerHTML = text;
  return textarea.value
    .replace(/&gt;?/g, ">")
    .replace(/&lt;?/g, "<")
    .replace(/&amp;?/g, "&")
    .replace(/&quot;?/g, "\"")
    .replace(/&#39;?/g, "'");
}

function cleanText(value) {
  return decodeHtmlEntities(value).replace(/\s+/g, " ").trim();
}

function comparableText(value) {
  return cleanText(value).toLowerCase().replace(/\s+/g, "").replace(/[;；:：,，/|+]/g, "");
}

function normalizeImageUrl(url) {
  if (!url) return null;
  let text = decodeHtmlEntities(url).trim();
  if (!text || text === "unknown" || text.startsWith("data:")) return null;
  if (text.startsWith("//")) text = `https:${text}`;
  try {
    return new URL(text, location.href).href;
  } catch {
    return null;
  }
}

function isBlockedImageUrl(url) {
  const lowered = String(url || "").toLowerCase();
  return (
    /\.(svg)(?:$|[?#])/.test(lowered) ||
    /(icon|logo|avatar|sprite|pay|payment|wangwang|qrcode|qr|loading|blank|grey)/.test(lowered) ||
    /(?:^|[-_/])tps-\d{1,3}-\d{1,3}(?:[-_.]|$)/.test(lowered)
  );
}

function imageUrlFromNode(node) {
  const img = node?.querySelector?.("img") || (node?.tagName === "IMG" ? node : null);
  if (img) return normalizeImageUrl(
    img.currentSrc || img.src || img.getAttribute("data-src") ||
    img.getAttribute("data-lazyload-src") || img.getAttribute("data-img") || img.getAttribute("data-original")
  );
  const background = node?.ownerDocument?.defaultView?.getComputedStyle?.(node)?.backgroundImage || "";
  const match = background.match(/url\(["']?(.+?)["']?\)/i);
  return match ? normalizeImageUrl(match[1]) : null;
}

function isLikelyProductImage(img, url) {
  if (isBlockedImageUrl(url)) return false;
  const width = img.naturalWidth || img.width || 0;
  const height = img.naturalHeight || img.height || 0;
  if (width && height && (width < 120 || height < 120)) return false;
  return true;
}

function diagnostic(field, strategy, hit, failureReason, candidateCount) {
  return {
    field,
    strategy,
    hit,
    failure_reason: failureReason || "unknown",
    candidate_count: candidateCount
  };
}

function extractBalanced(text, startIndex, openChar, closeChar) {
  let depth = 0;
  let quote = null;
  let escaped = false;
  for (let i = startIndex; i < text.length; i += 1) {
    const ch = text[i];
    if (quote) {
      if (escaped) {
        escaped = false;
      } else if (ch === "\\") {
        escaped = true;
      } else if (ch === quote) {
        quote = null;
      }
      continue;
    }
    if (ch === "\"" || ch === "'") {
      quote = ch;
      continue;
    }
    if (ch === openChar) depth += 1;
    if (ch === closeChar) depth -= 1;
    if (depth === 0) return text.slice(startIndex, i + 1);
  }
  return null;
}

function parseJsonCandidate(candidate) {
  if (!candidate) return null;
  try {
    return JSON.parse(candidate);
  } catch {
    return null;
  }
}

function isGeneratedSkuId(value) {
  return /^(script-sku|dom-sku|dom-combo|combo-sku)-/i.test(String(value || "").trim());
}

function isRealSkuId(value) {
  const text = String(value || "").trim();
  if (!text || text === "unknown" || isGeneratedSkuId(text)) return false;
  return /^\d{8,}$/.test(text) || /^[A-Za-z0-9_-]{8,}$/.test(text);
}

function extractRealSkuId(value, mapKey = null) {
  const directKeys = [
    "skuId",
    "skuID",
    "sku_id",
    "sku_id_str",
    "skuIdStr",
    "offerSkuId",
    "offerSkuID",
    "offer_sku_id",
    "offerSkuIdStr",
    "sku",
    "id"
  ];
  if (value && typeof value === "object") {
    for (const key of directKeys) {
      if (isRealSkuId(value[key])) return String(value[key]).trim();
    }
    for (const key of ["skuInfo", "skuDTO", "skuDto", "skuModel", "offerSku", "skuData"]) {
      const nested = extractRealSkuId(value[key]);
      if (nested) return nested;
    }
  }
  if (isRealSkuId(mapKey)) return String(mapKey).trim();
  return null;
}

function fallbackSkuKey(prefix, index, parts = []) {
  const body = parts
    .map((part) => String(part || "").trim())
    .filter(Boolean)
    .join("-");
  const clean = body.replace(/[^\w\u4e00-\u9fa5-]+/g, "_").slice(0, 90);
  return `${prefix}-${clean || index + 1}`;
}

function skuRuntimeKey(sku) {
  if (isRealSkuId(sku?.sku_id)) return String(sku.sku_id);
  return sku?.source_data?.fallback_key || sku?.sku_name || JSON.stringify(sku?.option_values || []);
}

function parseLooseSkuInfoMap(text) {
  if (!text || typeof text !== "string") return null;
  const strict = parseJsonCandidate(text);
  if (strict && typeof strict === "object") return strict;
  const map = {};
  const entryPattern = /(?:"([^"]{1,160})"|'([^']{1,160})'|([A-Za-z0-9_$:;._-]{1,160}))\s*:\s*\{/g;
  let match;
  while ((match = entryPattern.exec(text))) {
    const key = match[1] || match[2] || match[3];
    if (!key || /^(skuId|skuID|id|price|stock|image|picUrl)$/.test(key)) continue;
    const openIndex = entryPattern.lastIndex - 1;
    const objectText = extractBalanced(text, openIndex, "{", "}");
    if (!objectText) continue;
    const skuIdMatch = objectText.match(/(?:"skuId"|'skuId'|skuId|"skuID"|'skuID'|skuID|"sku_id"|'sku_id'|sku_id|"offerSkuId"|'offerSkuId'|offerSkuId)\s*:\s*["']?(\d{8,})["']?/);
    const skuId = skuIdMatch ? skuIdMatch[1] : null;
    if (!isRealSkuId(skuId)) continue;
    const imageMatch = objectText.match(/(?:"(?:imageUrl|image|picUrl|skuImageUrl)"|'(?:imageUrl|image|picUrl|skuImageUrl)'|imageUrl|image|picUrl|skuImageUrl)\s*:\s*["']([^"']+)["']/);
    const priceMatch = objectText.match(/(?:"(?:price|salePrice|priceText)"|'(?:price|salePrice|priceText)'|price|salePrice|priceText)\s*:\s*["']?([^"',}]+)["']?/);
    const stockMatch = objectText.match(/(?:"(?:stock|stockNum|canBookCount|quantity|amountOnSale)"|'(?:stock|stockNum|canBookCount|quantity|amountOnSale)'|stock|stockNum|canBookCount|quantity|amountOnSale)\s*:\s*["']?([^"',}]+)["']?/);
    map[key] = {
      skuId,
      imageUrl: imageMatch ? imageMatch[1] : undefined,
      price: priceMatch ? priceMatch[1] : undefined,
      stock: stockMatch ? stockMatch[1] : undefined,
      raw_text_sample: objectText.slice(0, 600)
    };
  }
  return Object.keys(map).length ? map : null;
}

function appendParsedJsonStrings(value, out = [], seen = new Set(), depth = 0) {
  if (out.length >= 30 || depth > 6 || value == null) return out;
  if (typeof value === "string") {
    const text = value.trim();
    if (text.length < 20 || !/sku|offer|product|规格|颜色|尺寸|型号/i.test(text)) return out;
    if (!/^[\[{]/.test(text) || seen.has(text)) return out;
    seen.add(text);
    const parsed = parseJsonCandidate(text);
    if (parsed) {
      out.push({ name: "embedded_json_string", data: parsed });
      appendParsedJsonStrings(parsed, out, seen, depth + 1);
    }
    return out;
  }
  if (Array.isArray(value)) {
    value.slice(0, 120).forEach((item) => appendParsedJsonStrings(item, out, seen, depth + 1));
    return out;
  }
  if (typeof value === "object") {
    Object.values(value).slice(0, 120).forEach((child) => appendParsedJsonStrings(child, out, seen, depth + 1));
  }
  return out;
}

function readPageWindowProductData() {
  injectPageProbe();
  const text = document.documentElement.getAttribute(PAGE_PROBE_ATTR);
  const parsed = parseJsonCandidate(text);
  if (Array.isArray(parsed)) pageWindowProductData = parsed;
  return pageWindowProductData;
}

function parseJsonScripts() {
  const results = [];
  readPageWindowProductData().forEach((item, index) => {
    results.push({ index: -1000 - index, source: "window_variable", data: item });
    appendParsedJsonStrings(item.data).forEach((child, childIndex) => {
      results.push({ index: -2000 - childIndex, source: "window_variable_embedded_json", data: child });
    });
  });
  document.querySelectorAll("script").forEach((script, index) => {
    const text = script.textContent || "";
    if (!text.trim()) return;
    if (script.type === "application/ld+json" || script.type === "application/json") {
      try {
        results.push({ index, source: "ld_json", data: JSON.parse(text) });
      } catch {
        results.push({ index, source: "ld_json_parse_failed", data: null });
      }
      return;
    }
    const markers = ["offer", "sku", "product", "detail", "globalData", "__INIT_DATA__", "__INITIAL_STATE__"];
    if (!markers.some((marker) => text.includes(marker))) return;
    const snippets = [];
    const assignmentPattern = /(?:window\.)?([A-Za-z0-9_$]*(?:INIT|STATE|DATA|offer|sku|product|SKU|Offer)[A-Za-z0-9_$]*)\s*=\s*[\{\[]/g;
    let match;
    while ((match = assignmentPattern.exec(text)) && snippets.length < 5) {
      const openIndex = assignmentPattern.lastIndex - 1;
      const openChar = text[openIndex];
      const balanced = extractBalanced(text, openIndex, openChar, openChar === "{" ? "}" : "]");
      snippets.push({ name: match[1], data: parseJsonCandidate(balanced) });
    }
    ["__INIT_DATA__", "__INITIAL_STATE__", "skuProps", "skuInfoMap", "skuMap"].forEach((token) => {
      const tokenIndex = text.indexOf(token);
      if (tokenIndex < 0 || snippets.length >= 8) return;
      const braceIndex = text.indexOf("{", tokenIndex);
      const bracketIndex = text.indexOf("[", tokenIndex);
      const candidates = [braceIndex, bracketIndex].filter((pos) => pos >= 0).sort((a, b) => a - b);
      if (!candidates.length) return;
      const openIndex = candidates[0];
      const openChar = text[openIndex];
      const balanced = extractBalanced(text, openIndex, openChar, openChar === "{" ? "}" : "]");
      const data = parseJsonCandidate(balanced);
      if (data) snippets.push({ name: token, data });
      else if (/skuInfoMap|skuMap/i.test(token)) {
        const looseMap = parseLooseSkuInfoMap(balanced);
        if (looseMap) snippets.push({ name: `${token}_loose`, data: { [token]: looseMap } });
      }
    });
    snippets.slice().forEach((snippet) => appendParsedJsonStrings(snippet.data).forEach((child) => snippets.push(child)));
    if (!snippets.length && /sku|规格|颜色|尺寸|型号|skuInfoMap|skuProps/i.test(text)) {
      snippets.push({ name: "script_text", data: null, text_sample: text.slice(0, 2000) });
    }
    results.push({ index, source: "script_init_data", data: snippets, text_sample: text.slice(0, 600) });
  });
  return results;
}

function deepFindStrings(value, keyPattern, limit = 20, out = []) {
  if (out.length >= limit || value == null) return out;
  if (Array.isArray(value)) {
    value.forEach((item) => deepFindStrings(item, keyPattern, limit, out));
  } else if (typeof value === "object") {
    Object.entries(value).forEach(([key, child]) => {
      if (keyPattern.test(key) && (typeof child === "string" || typeof child === "number")) out.push(String(child));
      deepFindStrings(child, keyPattern, limit, out);
    });
  }
  return out;
}

function cleanTitleCandidate(value) {
  const text = cleanText(value)
    .replace(/[-_ ]*阿里巴巴.*$/, "")
    .replace(/^\s*1688\s*/, "")
    .trim();
  if (text.length < 6) return null;
  if (/^(skuProps|sku|SKU列表|规格|商品|采集预览|当前商品)$/i.test(text)) return null;
  if (/^(首页|我的阿里|下载插件|我的订单|找本店|搜索)/.test(text)) return null;
  return text;
}

function extractTitle(structured) {
  const candidates = [];
  candidates.push(document.title);
  const selectors = [
    "h1",
    "[data-title]",
    ".title-text",
    ".mod-detail-title h1",
    ".d-title",
    "meta[property='og:title']"
  ];
  selectors.forEach((selector) => {
    document.querySelectorAll(selector).forEach((node) => {
      candidates.push(node.content || textOf(node));
    });
  });
  structured.forEach((item) => {
    deepFindStrings(item.data, /^(offerTitle|productTitle|subject|title|name)$/i, 5, candidates);
  });
  const clean = unique(candidates.map(cleanTitleCandidate).filter(Boolean));
  return { value: clean[0] || "unknown", candidates: clean, selectors };
}

function extractSupplier(structured) {
  const candidates = [];
  structured.forEach((item) => {
    deepFindStrings(item.data, /(company|supplier|seller|shop).*name|companyName|sellerName/i, 10, candidates);
  });
  const selectors = [".company-name", ".supplier-name", "[data-company-name]", ".shop-name", ".seller-name"];
  selectors.forEach((selector) => {
    document.querySelectorAll(selector).forEach((node) => candidates.push(textOf(node)));
  });
  const clean = unique(candidates.filter((item) => item.length >= 2));
  return { value: clean[0] || "unknown", candidates, selectors };
}

function extractAttributes() {
  const attrs = [];
  const selectors = [
    "[data-name][data-value]",
    ".detail-attribute li",
    ".obj-leading-table tr",
    ".mod-detail-attributes li",
    ".offer-attr li",
    "dl"
  ];
  selectors.forEach((selector) => {
    document.querySelectorAll(selector).forEach((node) => {
      let name = node.getAttribute("data-name");
      let value = node.getAttribute("data-value");
      if (!name || !value) {
        const cells = [...node.querySelectorAll("th,td,dt,dd,span")].map(textOf).filter(Boolean);
        if (cells.length >= 2) {
          name = cells[0].replace(/[：:]\s*$/, "");
          value = cells.slice(1).join(" ");
        } else {
          const text = textOf(node);
          const match = text.match(/^([^：:]{1,20})[：:]\s*(.+)$/);
          if (match) {
            name = match[1];
            value = match[2];
          }
        }
      }
      if (name && value && name.length <= 30 && value.length <= 200) {
        attrs.push({
          name_cn: name,
          value_cn: value,
          source: "candidate_selector",
          source_text: textOf(node).slice(0, 300)
        });
      }
    });
  });
  const seen = new Set();
  return { values: attrs.filter((item) => {
    const key = `${item.name_cn}:${item.value_cn}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 80), selectors };
}

function parsePrice(text) {
  const match = String(text || "").replace(/,/g, "").match(/(?:¥|￥)?\s*([0-9]+(?:\.[0-9]{1,2})?)/);
  return match ? Number(match[1]) : null;
}

function normalizePriceValue(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    if (value > 100000 && Number.isInteger(value)) return Number((value / 100).toFixed(2));
    return value;
  }
  if (typeof value === "string") return parsePrice(value);
  if (value && typeof value === "object") {
    const nested = firstDefined(
      value.price,
      value.value,
      value.amount,
      value.amountText,
      value.priceText,
      value.cent,
      value.priceCent,
      value.fen
    );
    return normalizePriceValue(nested);
  }
  return null;
}

function collectNestedValuesByKey(value, keyPattern, limit = 20, depth = 0, out = []) {
  if (out.length >= limit || !value || typeof value !== "object" || depth > 6) return out;
  if (Array.isArray(value)) {
    value.slice(0, 120).forEach((item) => collectNestedValuesByKey(item, keyPattern, limit, depth + 1, out));
    return out;
  }
  for (const [key, child] of Object.entries(value)) {
    if (keyPattern.test(key) && child !== null && child !== undefined && child !== "") out.push(child);
  }
  for (const [key, child] of Object.entries(value)) {
    if (!/price|sku|sale|offer|data|info|map|list|item|prop|spec|image|img|pic|url|thumb/i.test(key)) continue;
    collectNestedValuesByKey(child, keyPattern, limit, depth + 1, out);
  }
  return out;
}

function findNestedValueByKey(value, keyPattern, depth = 0) {
  if (!value || typeof value !== "object" || depth > 5) return null;
  if (Array.isArray(value)) {
    for (const item of value.slice(0, 80)) {
      const found = findNestedValueByKey(item, keyPattern, depth + 1);
      if (found !== null && found !== undefined && found !== "") return found;
    }
    return null;
  }
  for (const [key, child] of Object.entries(value)) {
    if (keyPattern.test(key) && child !== null && child !== undefined && child !== "") return child;
  }
  for (const [key, child] of Object.entries(value)) {
    if (!/price|sku|sale|offer|data|info|map|list|item|prop|spec|image|pic/i.test(key)) continue;
    const found = findNestedValueByKey(child, keyPattern, depth + 1);
    if (found !== null && found !== undefined && found !== "") return found;
  }
  return null;
}

function resolveSkuPrice(source, hasProductPriceRange = false) {
  const direct = source && typeof source === "object" ? firstDefined(
    source.price,
    source.salePrice,
    source.discountPrice,
    source.promotionPrice,
    source.offerPrice,
    source.skuPrice,
    source.priceText,
    source.priceCent && Number(source.priceCent) / 100,
    source.priceFen && Number(source.priceFen) / 100
  ) : null;
  const directPrice = normalizePriceValue(direct);
  if (directPrice !== null) return { value: directPrice, source: "sku_specific_price" };
  const nestedCent = findNestedValueByKey(source, /^(priceCent|priceFen|cent|fen)$/i);
  if (nestedCent !== null && nestedCent !== undefined && nestedCent !== "") {
    const centPrice = normalizePriceValue(Number(nestedCent) / 100);
    if (centPrice !== null) return { value: centPrice, source: "sku_specific_price" };
  }
  const nested = findNestedValueByKey(source, /^(skuPrice|salePrice|discountPrice|promotionPrice|offerPrice|priceText|priceCent|priceFen|price)$/i);
  const nestedPrice = normalizePriceValue(nested);
  if (nestedPrice !== null) return { value: nestedPrice, source: "sku_specific_price" };
  return { value: null, source: hasProductPriceRange ? "price_range" : "unknown" };
}

function imageFromStructuredValue(value) {
  const directValues = value && typeof value === "object" ? [
    value.skuImage,
    value.skuImg,
    value.skuImgUrl,
    value.skuImageUrl,
    value.skuPic,
    value.skuPicUrl,
    value.valueImage,
    value.valueImageUrl,
    value.imageUrl,
    value.image,
    value.images,
    value.picUrl,
    value.pic,
    value.img,
    value.imgUrl,
    value.thumbUrl,
    value.url
  ] : [];
  const candidates = [];
  directValues.forEach((candidate) => {
    if (Array.isArray(candidate)) candidates.push(...candidate);
    else candidates.push(candidate);
  });
  collectNestedValuesByKey(
    value,
    /^(skuImage|skuImg|skuImgUrl|skuImageUrl|skuPic|skuPicUrl|valueImage|valueImageUrl|imageUrl|images|picUrl|pic|img|imgUrl|thumbUrl|url)$/i,
    30
  ).forEach((candidate) => {
    if (Array.isArray(candidate)) candidates.push(...candidate);
    else candidates.push(candidate);
  });
  for (const candidate of candidates) {
    if (candidate && typeof candidate === "object") {
      const nested = imageFromStructuredValue(candidate);
      if (nested) return nested;
      continue;
    }
    const url = normalizeImageUrl(candidate);
    if (url) return url;
  }
  return null;
}

function extractPriceInfo(structured) {
  const texts = [];
  structured.forEach((item) => deepFindStrings(item.data, /(price|priceRange|salePrice|consignPrice)/i, 20, texts));
  const selectors = [".price", ".price-now", "[class*='price']", "[data-price]"];
  selectors.forEach((selector) => {
    document.querySelectorAll(selector).forEach((node) => texts.push(node.getAttribute("data-price") || textOf(node)));
  });
  const ranges = unique(texts).map((raw) => ({
    min_quantity: null,
    price_cny: parsePrice(raw),
    raw_text: String(raw).slice(0, 120)
  })).filter((item) => item.price_cny !== null).slice(0, 10);
  return {
    value: {
      currency: ranges.length ? "CNY" : "unknown",
      price_ranges: ranges,
      raw_text: ranges.length ? ranges.map((item) => item.raw_text).join(" | ") : "unknown"
    },
    selectors,
    candidateCount: texts.length
  };
}

function extractMinimumOrder() {
  const bodyText = document.body ? document.body.innerText : "";
  const match = bodyText.match(/(\d+)\s*(件|个|只|套|箱|条|包)\s*(?:起批|起订|起购)/);
  return {
    value: {
      value: match ? Number(match[1]) : null,
      unit: match ? match[2] : "unknown",
      raw_text: match ? match[0] : "unknown"
    },
    candidateCount: match ? 1 : 0
  };
}

function productPriceForQuantity(priceInfo, quantity) {
  if (!priceInfo || !Number.isFinite(Number(quantity)) || Number(quantity) <= 0) return null;
  const text = `${priceInfo.raw_text || ""} ${document.body?.innerText || ""}`.replace(/,/g, "");
  const tiers = [];
  const addTier = (priceText, minText, maxText = null) => {
    const price = Number(priceText);
    const min = Number(minText);
    const max = maxText === null ? null : Number(maxText);
    if (Number.isFinite(price) && price > 0 && Number.isFinite(min) && min > 0) {
      tiers.push({ price, min, max });
    }
  };
  for (const match of text.matchAll(/(?:¥|￥)\s*([0-9]+(?:\.[0-9]{1,2})?)\s*(\d+)\s*(?:件|个|只|套|箱|条|包)\s*起批/g)) {
    addTier(match[1], match[2]);
  }
  for (const match of text.matchAll(/(?:¥|￥)\s*([0-9]+(?:\.[0-9]{1,2})?)\s*(\d+)\s*[-–—]\s*(\d+)\s*(?:件|个|只|套|箱|条|包)/g)) {
    addTier(match[1], match[2], match[3]);
  }
  for (const match of text.matchAll(/(?:¥|￥)\s*([0-9]+(?:\.[0-9]{1,2})?)\s*[≥>=]\s*(\d+)\s*(?:件|个|只|套|箱|条|包)/g)) {
    addTier(match[1], match[2]);
  }
  const applicable = tiers
    .filter((tier) => Number(quantity) >= tier.min && (tier.max === null || Number(quantity) <= tier.max))
    .sort((left, right) => right.min - left.min);
  return applicable.length ? applicable[0].price : null;
}

function applyProductRangePrice(skus, fallbackPrice) {
  if (!Number.isFinite(fallbackPrice) || fallbackPrice <= 0 || !skus.length) return skus;
  if (skus.some((sku) => typeof sku.purchase_price === "number")) return skus;
  return skus.map((sku) => ({
    ...sku,
    purchase_price: fallbackPrice,
    price_source: "price_range",
    source_data: {
      ...(sku.source_data || {}),
      inherited_price_range: {
        price_cny: fallbackPrice,
        reason: "All final SKUs share the product price tier matching the captured minimum order quantity."
      }
    }
  }));
}

function imageFromNodes(nodes, source) {
  const urls = [];
  nodes.forEach((img, index) => {
    const url = normalizeImageUrl(img.currentSrc || img.src || img.getAttribute("data-src") || img.getAttribute("data-lazyload-src"));
    if (url && isLikelyProductImage(img, url)) urls.push({ url, source, source_order: index });
  });
  const seen = new Set();
  return urls.filter((item) => {
    if (seen.has(item.url)) return false;
    seen.add(item.url);
    return true;
  });
}

function extractMainImages() {
  const selectors = [
    ".detail-gallery img",
    ".mod-detail-gallery img",
    "[class*='gallery'] img",
    "[class*='thumb'] img",
    "meta[property='og:image']"
  ];
  const urls = [];
  selectors.forEach((selector) => {
    document.querySelectorAll(selector).forEach((node, index) => {
      const url = normalizeImageUrl(node.content || node.currentSrc || node.src || node.getAttribute("data-src"));
      if (!url || isBlockedImageUrl(url)) return;
      if (node.tagName === "IMG" && !isLikelyProductImage(node, url)) return;
      urls.push({ url, source: "main_gallery", source_order: index });
    });
  });
  const seen = new Set();
  return { values: urls.filter((item) => {
    if (seen.has(item.url) || isBlockedImageUrl(item.url)) return false;
    seen.add(item.url);
    return true;
  }).slice(0, 30), selectors };
}

function extractDetailImages() {
  const selectors = [
    "#desc-lazyload-container img",
    "#detailContent img",
    ".detail-description img",
    ".desc-lazyload-container img",
    "[class*='detail'][class*='content'] img",
    "[class*='description'] img"
  ];
  let images = [];
  selectors.forEach((selector) => {
    const found = imageFromNodes([...document.querySelectorAll(selector)], "detail_area");
    if (found.length) images = images.concat(found);
  });
  const seen = new Set();
  return { values: images.filter((item) => {
    if (seen.has(item.url)) return false;
    seen.add(item.url);
    return true;
  }).slice(0, 80), selectors };
}

function firstDefined(...values) {
  return values.find((value) => value !== undefined && value !== null && value !== "");
}

function normalizeSkuOptionValue(raw, dimensionName, source) {
  const imageUrl = imageFromStructuredValue(raw);
  const valueId = firstDefined(raw.valueId, raw.vid, raw.specId, raw.id, raw.value_id, raw.value);
  const valueText = firstDefined(raw.name, raw.valueName, raw.value, raw.text, raw.title, raw.label, raw.specName, valueId, "unknown");
  return {
    id: cleanText(valueId || valueText),
    name: cleanText(valueText),
    image_url: imageUrl || "unknown",
    raw,
    option: {
      name_cn: cleanText(dimensionName || firstDefined(raw.prop, raw.propName, raw.name_cn, "规格")),
      value_cn: cleanText(valueText),
      source,
      source_text: `${dimensionName || "规格"}: ${valueText}`
    }
  };
}

function normalizeSkuProp(raw, source) {
  const dimensionName = cleanText(firstDefined(raw.prop, raw.propName, raw.name, raw.title, raw.label, raw.name_cn, "规格"));
  const rawValues = firstDefined(raw.value, raw.values, raw.items, raw.children, raw.list, []);
  if (!Array.isArray(rawValues)) return null;
  const values = rawValues
    .map((value) => typeof value === "object" ? normalizeSkuOptionValue(value, dimensionName, source) : normalizeSkuOptionValue({ value }, dimensionName, source))
    .filter((value) => value.name && value.name !== "unknown");
  if (!values.length) return null;
  return { name: String(dimensionName), values, raw };
}

function cartesian(groups) {
  return groups.reduce((acc, group) => {
    const next = [];
    acc.forEach((combo) => group.values.forEach((value) => next.push([...combo, value])));
    return next;
  }, [[]]);
}

function findSkuInfoForCombo(combo, skuMap) {
  if (!skuMap || typeof skuMap !== "object") return null;
  const ids = combo.map((item) => String(item.id));
  const entries = Object.entries(skuMap);
  let found = entries.find(([key]) => ids.every((id) => String(key).includes(id)));
  if (!found) {
    found = entries.find(([, value]) => {
      const text = JSON.stringify(value || {});
      return ids.every((id) => text.includes(id));
    });
  }
  if (!found) found = entries.find(([key]) => ids.some((id) => String(key).includes(id)));
  return found ? { key: found[0], value: found[1] } : null;
}

function skuFromCombo(combo, skuInfo, index, sourceData) {
  const info = skuInfo?.value && typeof skuInfo.value === "object" ? skuInfo.value : {};
  const optionValues = combo.map((item) => item.option);
  const name = optionValues.map((item) => item.value_cn).join(" / ");
  const imageUrl = imageFromStructuredValue(info) || combo.map((item) => item.image_url).find((url) => url && url !== "unknown") || "unknown";
  const stock = firstDefined(info.stock, info.stockNum, info.canBookCount, info.quantity, info.amountOnSale);
  const realSkuId = extractRealSkuId(info, skuInfo?.key);
  const fallbackKey = fallbackSkuKey("combo-key", index, [skuInfo?.key, name]);
  const price = resolveSkuPrice(info);
  return {
    sku_id: realSkuId || "unknown",
    sku_name: name || "unknown",
    option_values: optionValues,
    purchase_price: price.value,
    price_source: price.source,
    image_url: imageUrl,
    sku_image_missing: !(imageUrl && imageUrl !== "unknown"),
    availability: info.canBook === false || stock === 0 || stock === "0" ? "out_of_stock" : "unknown",
    source_data: {
      sku_info_key: skuInfo?.key || null,
      sku_info: info,
      combo: combo.map((item) => item.raw),
      source_data: sourceData,
      has_real_sku_id: Boolean(realSkuId),
      fallback_key: fallbackKey,
      sku_image_source: imageFromStructuredValue(info) ? "sku_specific_image" : (imageUrl && imageUrl !== "unknown" ? "sku_prop_value_image" : "missing")
    }
  };
}

function looksLikeSkuMap(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const entries = Object.entries(value).slice(0, 20);
  if (!entries.length) return false;
  return entries.some(([key, child]) => extractRealSkuId(child, key));
}

function findStructuredSkuMaps(value, out = []) {
  if (!value || typeof value !== "object") return out;
  if (Array.isArray(value)) {
    value.slice(0, 120).forEach((item) => findStructuredSkuMaps(item, out));
    return out;
  }
  Object.entries(value).forEach(([key, child]) => {
    if (/sku.*(map|info|data)|skuInfoMap|skuMap|skuPriceMap/i.test(key) && looksLikeSkuMap(child)) {
      out.push({ map: child, key, source: value });
    }
    if (/sku|spec|prop|sale|offer|product|data/i.test(key)) findStructuredSkuMaps(child, out);
  });
  return out;
}

function normalizeSkuListItem(item, index, sourceName) {
  if (!item || typeof item !== "object" || Array.isArray(item)) return null;
  const realSkuId = extractRealSkuId(item);
  if (!realSkuId) return null;
  const optionValues = [];
  const optionSources = firstDefined(item.optionValues, item.option_values, item.skuAttributes, item.attributes, item.props, item.specAttrs, item.specs, item.saleProps);
  if (Array.isArray(optionSources)) {
    optionSources.forEach((option) => {
      if (!option || typeof option !== "object") return;
      const name = firstDefined(option.name_cn, option.name, option.propName, option.propertyName, option.key, option.attributeName, "规格");
      const value = firstDefined(option.value_cn, option.value, option.valueName, option.name, option.text, option.label);
      if (value) {
        const valueId = firstDefined(option.valueId, option.vid, option.specId, option.id, option.value_id, option.valueID);
        optionValues.push({
          name_cn: cleanText(name),
          value_cn: cleanText(value),
          source: "script_init_data",
          source_text: `${name}: ${value}`,
          value_id: valueId ? cleanText(valueId) : undefined
        });
      }
    });
  }
  const stringSpec = firstDefined(item.skuName, item.name, item.specName, item.title, item.specAttrs, item.spec);
  if (!optionValues.length && typeof stringSpec === "string") {
    cleanText(stringSpec).split(/[;；,，/|+]/).map((part) => part.trim()).filter(Boolean).forEach((part, partIndex) => {
      const pieces = part.split(/[:：]/);
      optionValues.push({
        name_cn: pieces.length > 1 ? pieces[0].trim() : `规格${partIndex + 1}`,
        value_cn: (pieces.length > 1 ? pieces.slice(1).join(":") : part).trim(),
        source: "script_init_data",
        source_text: part
      });
    });
  }
  const imageUrl = imageFromStructuredValue(item);
  const price = resolveSkuPrice(item);
  return {
    sku_id: realSkuId,
    sku_name: cleanText(stringSpec || optionValues.map((option) => option.value_cn).join(" / ") || realSkuId),
    option_values: optionValues,
    purchase_price: price.value,
    price_source: price.source,
    image_url: imageUrl || "unknown",
    sku_image_missing: !imageUrl,
    availability: item.canBook === false || item.stock === 0 || item.stock === "0" || item.status === "disabled" ? "out_of_stock" : "unknown",
    source_data: {
      ...item,
      source: sourceName,
      has_real_sku_id: true,
      sku_image_source: imageUrl ? "sku_specific_image" : "missing"
    }
  };
}

function findStructuredSkuListItems(value, out = [], sourceName = "script_init_data") {
  if (!value || typeof value !== "object" || out.length > 500) return out;
  if (Array.isArray(value)) {
    value.slice(0, 500).forEach((item, index) => {
      const normalized = normalizeSkuListItem(item, index, sourceName);
      if (normalized) out.push(normalized);
      findStructuredSkuListItems(item, out, sourceName);
    });
    return out;
  }
  Object.entries(value).forEach(([key, child]) => {
    if (/sku|spec|prop|sale|offer|product|data|list|map/i.test(key)) {
      const normalized = normalizeSkuListItem(child, out.length, sourceName);
      if (normalized) out.push(normalized);
      findStructuredSkuListItems(child, out, sourceName);
    }
  });
  return out;
}

function buildSkuPropImageLookup(models) {
  const byValue = new Map();
  models.forEach((model) => {
    (model.props || []).forEach((prop) => {
      const group = normalizeSkuProp(prop, "script_init_data");
      if (!group) return;
      group.values.forEach((value) => {
        if (!value.image_url || value.image_url === "unknown") return;
        byValue.set(comparableText(value.name), {
          image_url: value.image_url,
          source: "sku_prop_value_image",
          prop_name: group.name,
          value_name: value.name,
          value_id: value.id
        });
        if (value.id) {
          byValue.set(comparableText(value.id), {
            image_url: value.image_url,
            source: "sku_prop_value_image",
            prop_name: group.name,
            value_name: value.name,
            value_id: value.id
          });
        }
      });
    });
  });
  return byValue;
}

function applySkuPropImage(sku, propImageLookup) {
  if (sku.image_url && sku.image_url !== "unknown") return sku;
  for (const option of sku.option_values || []) {
    const lookupKeys = [
      option.value_id,
      option.valueId,
      option.vid,
      option.specId,
      option.id,
      option.value_cn,
      option.value
    ].map(comparableText).filter(Boolean);
    const hit = lookupKeys.map((key) => propImageLookup.get(key)).find(Boolean);
    if (hit?.image_url) {
      return {
        ...sku,
        image_url: hit.image_url,
        sku_image_missing: false,
        source_data: {
          ...(sku.source_data || {}),
          sku_image_source: hit.source,
          sku_image_prop_name: hit.prop_name,
          sku_image_prop_value: hit.value_name,
          sku_image_prop_value_id: hit.value_id
        }
      };
    }
  }
  return {
    ...sku,
    image_url: "unknown",
    sku_image_missing: true,
    source_data: {
      ...(sku.source_data || {}),
      sku_image_source: "missing"
    }
  };
}

function buildDomPropertyImageData(groups) {
  const lookup = new Map();
  const propertyGroups = groups.map((group, groupIndex) => {
    const propId = `dom-prop-${groupIndex + 1}`;
    const values = group.values.map((value, valueIndex) => {
      const item = {
        prop_id: propId,
        prop_name: cleanText(group.name),
        value_id: cleanText(value.id || `dom-value-${valueIndex + 1}`),
        value_name: cleanText(value.name),
        image_url: value.image_url || "unknown",
        image_source: value.image_url && value.image_url !== "unknown" ? "sku_property_value" : "missing",
        source_data: value.raw || {}
      };
      if (item.image_url !== "unknown") lookup.set(comparableText(item.value_name), item);
      return item;
    });
    return { prop_id: propId, prop_name: cleanText(group.name), values };
  });
  return { lookup, propertyGroups };
}

function applyDomPropertyImage(sku, lookup) {
  if (sku.image_url && sku.image_url !== "unknown") return sku;
  const rawSpec = cleanText(sku.source_data?.specAttrs || sku.sku_name || "");
  const match = [...lookup.values()].find((value) => {
    const name = cleanText(value.value_name);
    return name && (rawSpec === name || rawSpec.startsWith(`${name}>`) || rawSpec.startsWith(`${name}#`) || rawSpec.startsWith(`${name}/`));
  });
  if (!match) return sku;
  return {
    ...sku,
    image_url: match.image_url,
    sku_image_missing: false,
    source_data: {
      ...(sku.source_data || {}),
      sku_image_source: "sku_property_value",
      sku_image_prop_id: match.prop_id,
      sku_image_prop_name: match.prop_name,
      sku_image_prop_value_id: match.value_id,
      sku_image_prop_value: match.value_name
    }
  };
}

function finalizeSkuImageAndPrice(sku, propImageLookup, hasProductPriceRange) {
  const directImage = imageFromStructuredValue(sku.source_data);
  const imageFinalized = directImage && (!sku.image_url || sku.image_url === "unknown")
    ? {
        ...sku,
        image_url: directImage,
        sku_image_missing: false,
        source_data: {
          ...(sku.source_data || {}),
          sku_image_source: "sku_specific_image"
        }
      }
    : applySkuPropImage({
        ...sku,
        sku_image_missing: !(sku.image_url && sku.image_url !== "unknown"),
        source_data: {
          ...(sku.source_data || {}),
          sku_image_source: sku.image_url && sku.image_url !== "unknown" ? (sku.source_data?.sku_image_source || "sku_specific_image") : "missing"
        }
      }, propImageLookup);
  const price = resolveSkuPrice(imageFinalized.source_data, hasProductPriceRange);
  return {
    ...imageFinalized,
    purchase_price: imageFinalized.purchase_price ?? price.value,
    price_source: imageFinalized.purchase_price !== null && imageFinalized.purchase_price !== undefined ? (imageFinalized.price_source || "sku_specific_price") : price.source
  };
}

function findStructuredSkuModels(value, out = []) {
  if (!value || typeof value !== "object") return out;
  if (Array.isArray(value)) {
    value.forEach((item) => findStructuredSkuModels(item, out));
    return out;
  }
  const keys = Object.keys(value);
  const props = firstDefined(value.skuProps, value.sku_props, value.skuProp, value.specProps, value.propList, value.saleProps);
  const map = firstDefined(value.skuInfoMap, value.skuMap, value.sku_map, value.skuPriceMap, value.skuInfos, value.skuInfo);
  if (Array.isArray(props) && props.length) out.push({ props, map, source: value });
  if (keys.some((key) => /sku|spec|prop|sale/i.test(key))) {
    Object.values(value).forEach((child) => findStructuredSkuModels(child, out));
  } else {
    Object.values(value).slice(0, 20).forEach((child) => findStructuredSkuModels(child, out));
  }
  return out;
}

function extractStructuredSkus(structured, hasProductPriceRange = false) {
  const skus = [];
  const models = [];
  const maps = [];
  const listItems = [];
  structured.forEach((item) => {
    findStructuredSkuModels(item.data, models);
    findStructuredSkuMaps(item.data, maps);
    findStructuredSkuListItems(item.data, listItems, item.source || "script_init_data");
    if (Array.isArray(item.data)) item.data.forEach((child) => findStructuredSkuModels(child?.data || child, models));
  });
  const propImageLookup = buildSkuPropImageLookup(models);
  const domPropertyData = buildDomPropertyImageData(extractDomSkuGroups());
  domPropertyData.lookup.forEach((value, key) => propImageLookup.set(key, {
    image_url: value.image_url,
    source: "sku_property_value",
    prop_name: value.prop_name,
    value_name: value.value_name,
    value_id: value.value_id
  }));
  listItems.forEach((sku) => skus.push(sku));
  models.slice(0, 8).forEach((model) => {
    const groups = model.props.map((prop) => normalizeSkuProp(prop, "script_init_data")).filter(Boolean);
    if (!groups.length) return;
    const combos = cartesian(groups).slice(0, 300);
    combos.forEach((combo, index) => {
      const skuInfo = findSkuInfoForCombo(combo, model.map) || findSkuInfoForCombo(combo, maps.find((candidate) => findSkuInfoForCombo(combo, candidate.map))?.map);
      skus.push(skuFromCombo(combo, skuInfo, skus.length + index, model.source));
    });
  });
  const finalized = skus.map((sku) => applyDomPropertyImage(
    finalizeSkuImageAndPrice(sku, propImageLookup, hasProductPriceRange), domPropertyData.lookup
  ));
  return { skus: finalized, modelCount: models.length, mapCount: maps.length, propertyGroups: domPropertyData.propertyGroups, realSkuIdCount: finalized.filter((sku) => isRealSkuId(sku.sku_id)).length };
}

function findSkuContainers() {
  const selectors = [
    "[class*='sku']",
    "[class*='Sku']",
    "[class*='spec']",
    "[class*='Spec']",
    "[class*='sale-prop']",
    "[class*='offer-prop']",
    "[class*='prop-module']",
    "[data-sku]",
    "[data-sku-prop]",
    "dl"
  ];
  const roots = [];
  selectors.forEach((selector) => {
    document.querySelectorAll(selector).forEach((node) => {
      const text = textOf(node);
      if (text.length > 5000) return;
      if (/颜色|尺寸|规格|型号|款式|套餐|容量|数量|口味|尺码|sku/i.test(text) || node.querySelector("img")) {
        roots.push(node);
      }
    });
  });
  return [...new Set(roots)];
}

function extractDomSkuGroups() {
  const dimensionWords = /(颜色|尺寸|规格|型号|款式|套餐|容量|数量|口味|尺码|类别|样式|花色|高度|长度|宽度)/;
  const groups = [];
  findSkuContainers().forEach((root) => {
    const rootText = textOf(root);
    if (!dimensionWords.test(rootText) && !/sku/i.test(root.className || "")) return;
    const dimensionMatch = rootText.match(dimensionWords);
    const dimension = dimensionMatch ? dimensionMatch[1] : "规格";
    const itemSelectors = [
      "[role='button']",
      "button",
      "li",
      "a",
      "[class*='item']",
      "[class*='value']",
      "[class*='option']",
      "[data-value]",
      "[data-name]"
    ];
    const values = [];
    itemSelectors.forEach((selector) => {
      root.querySelectorAll(selector).forEach((node) => {
        if (node.closest("#caf-sku-drawer-root")) return;
        const text = textOf(node).replace(/^(颜色|尺寸|规格|型号|款式|套餐|容量|数量|口味|尺码)\s*[:：]?/, "").trim();
        const title = node.getAttribute("title") || node.getAttribute("aria-label") || node.getAttribute("data-value") || node.getAttribute("data-name") || "";
        const valueText = (title || text).trim();
        if (!valueText || valueText.length > 80 || dimensionWords.test(valueText)) return;
        if (/加入购物车|立即订购|起批|价格|客服|收藏|分享|地址|物流|支付|查看|联系/.test(valueText)) return;
        const imageUrl = imageUrlFromNode(node) || imageUrlFromNode(node.closest("li,a,button,div"));
        const disabled = node.matches("[disabled],[aria-disabled='true']") || /disabled|disable|sold|empty|no-stock|不可|无货|售罄/.test(node.className || "");
        values.push({
          id: node.getAttribute("data-value-id") || node.getAttribute("data-id") || node.getAttribute("data-sku-id") || valueText,
          name: valueText,
          image_url: imageUrl || "unknown",
          disabled,
          raw: {
            text: valueText,
            class: node.className || "",
            dataset: { ...node.dataset }
          },
          option: {
            name_cn: dimension,
            value_cn: valueText,
            source: "dom_semantic",
            source_text: textOf(node).slice(0, 200) || valueText
          }
        });
      });
    });
    const seen = new Set();
    const cleanValues = values.filter((value) => {
      const key = `${dimension}:${value.name}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }).slice(0, 80);
    if (cleanValues.length >= 1) groups.push({ name: dimension, values: cleanValues, raw: { text: rootText.slice(0, 500), class: root.className || "" } });
  });
  const uniqueGroups = [];
  const seenGroup = new Set();
  groups.forEach((group) => {
    const key = `${group.name}:${group.values.map((value) => value.name).join("|")}`;
    if (!seenGroup.has(key)) {
      seenGroup.add(key);
      uniqueGroups.push(group);
    }
  });
  return uniqueGroups.slice(0, 4);
}

function extractDomComboSkus(fallbackPrice) {
  const groups = extractDomSkuGroups();
  if (!groups.length) return { skus: [], groupCount: 0 };
  const combos = cartesian(groups).slice(0, 300);
  const skus = combos.map((combo, index) => {
    const imageUrl = combo.map((item) => item.image_url).find((url) => url && url !== "unknown") || "unknown";
    const unavailable = combo.some((item) => item.disabled);
    const optionValues = combo.map((item) => item.option);
    const name = optionValues.map((item) => item.value_cn).join(" / ");
    const fallbackKey = fallbackSkuKey("dom-combo-key", index, combo.map((item) => item.id || item.name));
    return {
      sku_id: "unknown",
      sku_name: name || "unknown",
      option_values: optionValues,
      purchase_price: fallbackPrice,
      price_source: fallbackPrice !== null ? "price_range" : "unknown",
      image_url: imageUrl,
      sku_image_missing: !(imageUrl && imageUrl !== "unknown"),
      availability: unavailable ? "out_of_stock" : "unknown",
      source_data: {
        combo: combo.map((item) => item.raw),
        source: "dom_sku_groups",
        is_final_sku_combo: true,
        dimension_count: optionValues.length,
        has_real_sku_id: false,
        fallback_key: fallbackKey
      }
    };
  });
  return { skus, groupCount: groups.length };
}

function extractSkus(structured, hasProductPriceRange = false, productRangePrice = null) {
  const structuredResult = extractStructuredSkus(structured, hasProductPriceRange);
  if (structuredResult.skus.length) {
    const seenStructured = new Set();
    return {
      values: applyProductRangePrice(structuredResult.skus.filter((sku) => {
        const key = `${skuRuntimeKey(sku)}:${sku.sku_name}:${sku.image_url}`;
        if (seenStructured.has(key)) return false;
        seenStructured.add(key);
        return true;
      }).slice(0, 300), productRangePrice),
      candidateCount: structuredResult.skus.length,
      source: "script_init_data",
      propertyGroups: structuredResult.propertyGroups || []
    };
  }
  const rawCandidates = [];
  structured.forEach((item) => {
    const collect = (value) => {
      if (!value || typeof value !== "object") return;
      if (Array.isArray(value)) {
        if (value.length && value.some((child) => child && typeof child === "object" && /(sku|spec|price|stock|image)/i.test(Object.keys(child).join(" ")))) {
          rawCandidates.push(value);
        }
        value.forEach(collect);
      } else {
        Object.values(value).forEach(collect);
      }
    };
    collect(item.data);
  });
  const skus = [];
  rawCandidates.flat().slice(0, 100).forEach((item, index) => {
    if (!item || typeof item !== "object") return;
    const realSkuId = extractRealSkuId(item);
    const imageUrl = imageFromStructuredValue(item);
    const skuName = cleanText(item.name || item.skuName || item.specName || "unknown");
    const price = resolveSkuPrice(item, hasProductPriceRange);
    skus.push({
      sku_id: realSkuId || "unknown",
      sku_name: skuName,
      option_values: Object.entries(item).filter(([key, value]) => /color|size|spec|name|value|颜色|尺寸|规格/i.test(key) && typeof value !== "object").map(([key, value]) => ({
        name_cn: key,
        value_cn: String(value),
        source: "script_init_data",
        source_text: `${key}: ${value}`
          })),
      purchase_price: price.value,
      price_source: price.source,
      image_url: imageUrl || "unknown",
      sku_image_missing: !imageUrl,
      availability: item.canBook === false || item.stock === 0 ? "out_of_stock" : "unknown",
      source_data: {
        ...item,
        has_real_sku_id: Boolean(realSkuId),
        fallback_key: fallbackSkuKey("script-key", index, [skuName, imageUrl]),
        sku_image_source: imageUrl ? "sku_specific_image" : "missing"
      }
    });
  });
  if (!skus.length) {
    const fallbackPrice = parsePrice(document.querySelector("[class*='price'], [data-price]")?.getAttribute("data-price") || textOf(document.querySelector("[class*='price']")));
    const domCombos = extractDomComboSkus(fallbackPrice);
    if (domCombos.skus.length) {
      skus.push(...domCombos.skus);
    }
  }
  if (!skus.length) {
    findSkuContainers().forEach((container) => {
      container.querySelectorAll("img").forEach((img, index) => {
      const url = normalizeImageUrl(img.currentSrc || img.src || img.getAttribute("data-src"));
      if (!url || !isLikelyProductImage(img, url)) return;
      const optionText = img.alt || img.title || textOf(img.closest("li,a,button,div")) || "unknown";
      skus.push({
        sku_id: "unknown",
        sku_name: optionText,
        option_values: [{
          name_cn: "规格",
          value_cn: optionText,
          source: "dom_semantic",
          source_text: optionText
        }],
        purchase_price: null,
        price_source: "unknown",
        image_url: url,
        sku_image_missing: false,
        availability: "unknown",
        source_data: {
          alt: img.alt || null,
          title: img.title || null,
          has_real_sku_id: false,
          fallback_key: fallbackSkuKey("dom-image-key", skus.length, [optionText, url])
        }
      });
    });
    });
  }
  const seen = new Set();
  return { values: skus.filter((sku) => {
    const key = `${skuRuntimeKey(sku)}:${sku.image_url}:${sku.sku_name}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 300), candidateCount: Math.max(rawCandidates.length, skus.length), source: skus.length ? "dom_semantic" : "unknown" };
}

function skuDisplayName(sku) {
  const optionText = (sku.option_values || [])
    .map((item) => item.value_cn || item.value || "")
    .filter(Boolean)
    .join(" / ");
  return optionText || sku.sku_name || sku.sku_id || "unknown";
}

function skuDimensions(sku) {
  return (sku.option_values || [])
    .map((item) => `${item.name_cn || item.name || "规格"}: ${item.value_cn || item.value || "unknown"}`)
    .join("；");
}

function isSkuSelectable(sku) {
  return !["out_of_stock"].includes(sku.availability);
}

function getFilterValues(skus) {
  const values = new Map();
  skus.forEach((sku) => {
    (sku.option_values || []).forEach((option) => {
      const key = option.name_cn || option.name || "规格";
      const value = option.value_cn || option.value || "unknown";
      if (!values.has(key)) values.set(key, new Set());
      values.get(key).add(value);
    });
  });
  return [...values.entries()].map(([name, set]) => ({ name, values: [...set] }));
}

function compactSkuDebugItem(sku, reason) {
  return {
    sku_id: sku.sku_id || "unknown",
    sku_name: sku.sku_name || "unknown",
    option_values: sku.option_values || [],
    image_url: sku.image_url || "unknown",
    purchase_price: typeof sku.purchase_price === "number" ? sku.purchase_price : null,
    price_source: sku.price_source || "unknown",
    sku_image_missing: sku.sku_image_missing === true,
    reason: reason || "unknown",
    source_data_keys: sku.source_data && typeof sku.source_data === "object" ? Object.keys(sku.source_data).slice(0, 80) : []
  };
}

function countBy(values, getter) {
  const counts = {};
  values.forEach((item) => {
    const key = getter(item) || "unknown";
    counts[key] = (counts[key] || 0) + 1;
  });
  return counts;
}

function buildSkuDebug(skus, context = {}) {
  const values = Array.isArray(skus) ? skus : [];
  const missingImages = values
    .filter((sku) => !(sku.image_url && sku.image_url !== "unknown") || sku.sku_image_missing === true)
    .map((sku) => compactSkuDebugItem(sku, sku.source_data?.sku_image_source === "missing" ? "1688未提供SKU专属图片字段" : "SKU图片为空或不可用"))
    .slice(0, 300);
  const missingPrices = values
    .filter((sku) => typeof sku.purchase_price !== "number")
    .map((sku) => compactSkuDebugItem(sku, sku.price_source === "price_range" ? "仅发现商品价格区间，未发现SKU专属价" : "未发现SKU价格字段"))
    .slice(0, 300);
  const imageSourceCounts = countBy(values, (sku) => sku.source_data?.sku_image_source || (sku.sku_image_missing ? "missing" : "unknown"));
  const priceSourceCounts = countBy(values, (sku) => sku.price_source || "unknown");
  const skuSourceCounts = countBy(values, (sku) => sku.source_data?.source || "unknown");
  const dataSources = [
    {
      name: context.sku_source || "unknown",
      type: "sku_extraction",
      count: values.length
    },
    {
      name: "structured_candidates",
      type: "page_data",
      count: context.structured_count ?? "unknown"
    },
    {
      name: "window_variables",
      type: "page_data",
      count: context.window_variable_count ?? pageWindowProductData.length
    },
    {
      name: "sku_image_sources",
      type: "image_mapping",
      counts: imageSourceCounts
    },
    {
      name: "sku_price_sources",
      type: "price_mapping",
      counts: priceSourceCounts
    },
    {
      name: "sku_object_sources",
      type: "raw_sku_object",
      counts: skuSourceCounts
    }
  ];
  return {
    total_skus: values.length,
    real_sku_ids: values.filter((sku) => isRealSkuId(sku.sku_id)).length,
    sku_with_real_id: values.filter((sku) => isRealSkuId(sku.sku_id)).length,
    sku_with_images: values.filter((sku) => sku.image_url && sku.image_url !== "unknown" && sku.sku_image_missing !== true).length,
    sku_with_image: values.filter((sku) => sku.image_url && sku.image_url !== "unknown" && sku.sku_image_missing !== true).length,
    sku_with_prices: values.filter((sku) => typeof sku.purchase_price === "number").length,
    sku_with_price: values.filter((sku) => typeof sku.purchase_price === "number").length,
    missing_images: missingImages,
    missing_image_skus: values
      .filter((sku) => !(sku.image_url && sku.image_url !== "unknown") || sku.sku_image_missing === true)
      .map((sku) => sku.sku_id || skuRuntimeKey(sku))
      .slice(0, 100),
    missing_prices: missingPrices,
    missing_price_skus: values
      .filter((sku) => typeof sku.purchase_price !== "number")
      .map((sku) => sku.sku_id || skuRuntimeKey(sku))
      .slice(0, 100),
    data_sources: dataSources
  };
}

function buildSkuPropertyImageDebug(propertyGroups, skus) {
  const groups = propertyGroups || [];
  const values = groups.flatMap((group) => group.values || []);
  const mapped = (skus || []).filter((sku) => sku.source_data?.sku_image_source === "sku_property_value");
  return {
    property_groups: groups,
    color_property_detected: groups.some((group) => /颜色|色/.test(group.prop_name || "")),
    color_value_count: values.length,
    color_values_with_image: values.filter((value) => value.image_url && value.image_url !== "unknown").length,
    final_sku_count: (skus || []).length,
    final_skus_mapped_by_property_image: mapped.length,
    final_skus_still_missing_image: (skus || []).filter((sku) => sku.sku_image_missing === true).length,
    unmapped_values: values.filter((value) => !value.image_url || value.image_url === "unknown").map((value) => value.value_name),
    data_sources: ["sku_property_dom", "sku_property_value"]
  };
}

function buildSkuDebugExport(capture) {
  const skus = capture?.skus || [];
  const skuDebug = capture?.raw_snapshot?.sku_debug || buildSkuDebug(skus, {
    sku_source: capture?.raw_snapshot?.sku_source,
    structured_count: capture?.raw_snapshot?.structured_data_summary?.structured_candidates
  });
  return {
    generated_at: new Date().toISOString(),
    plugin_version: PLUGIN_VERSION,
    source_url: capture?.source_url || location.href,
    page_title: capture?.page_title || document.title || "unknown",
    title_cn: capture?.title_cn || "unknown",
    total_skus: skuDebug.total_skus,
    real_sku_ids: skuDebug.real_sku_ids ?? skuDebug.sku_with_real_id,
    sku_with_images: skuDebug.sku_with_images ?? skuDebug.sku_with_image,
    sku_with_prices: skuDebug.sku_with_prices ?? skuDebug.sku_with_price,
    missing_images: skuDebug.missing_images || [],
    missing_prices: skuDebug.missing_prices || [],
    data_sources: skuDebug.data_sources || [],
    skus: skus.map((sku) => ({
      sku_id: sku.sku_id || "unknown",
      sku_name: sku.sku_name || "unknown",
      option_values: sku.option_values || [],
      image_url: sku.image_url || "unknown",
      sku_image_missing: sku.sku_image_missing === true,
      sku_image_source: sku.source_data?.sku_image_source || "unknown",
      price: typeof sku.purchase_price === "number" ? sku.purchase_price : null,
      purchase_price: typeof sku.purchase_price === "number" ? sku.purchase_price : null,
      price_source: sku.price_source || "unknown",
      availability: sku.availability || "unknown",
      source_data_keys: sku.source_data && typeof sku.source_data === "object" ? Object.keys(sku.source_data).slice(0, 120) : []
    }))
  };
}

function downloadJson(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2) + "\n"], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.documentElement.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function removeSkuDrawer() {
  const old = document.getElementById("caf-sku-drawer-root");
  if (old) old.remove();
}

function showToast(text, isError = false) {
  let toast = document.getElementById("caf-collector-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "caf-collector-toast";
    toast.style.cssText = "position:fixed;right:18px;bottom:18px;z-index:2147483647;max-width:420px;padding:12px 14px;border-radius:8px;box-shadow:0 8px 24px rgba(15,23,42,.2);font:13px/1.4 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;white-space:pre-wrap;";
    document.documentElement.appendChild(toast);
  }
  toast.style.background = isError ? "#fef2f2" : "#ecfdf3";
  toast.style.color = isError ? "#b42318" : "#067647";
  toast.style.border = isError ? "1px solid #fecdca" : "1px solid #abefc6";
  toast.textContent = text;
  window.setTimeout(() => toast.remove(), 9000);
}

async function postSelectedCapture(capture) {
  const connection = await loadFactoryConnection();
  const headers = { "Content-Type": "application/json" };
  if (connection.accessCode) headers["X-Factory-Access-Code"] = connection.accessCode;
  const response = await fetch(`${connection.baseUrl}/api/collector/products`, {
    method: "POST",
    headers,
    body: JSON.stringify(capture)
  });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body.detail ? JSON.stringify(body.detail) : `HTTP ${response.status}`);
  }
  return body;
}

async function collectorApi(path, options = {}) {
  const connection = await loadFactoryConnection();
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  if (connection.accessCode) headers["X-Factory-Access-Code"] = connection.accessCode;
  const response = await fetch(`${connection.baseUrl}${path}`, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail?.message || body.detail || `HTTP ${response.status}`);
  return body;
}

async function loadLocalCategoryTreeCache() {
  if (!localCategoryTreeCachePromise) {
    localCategoryTreeCachePromise = fetch(chrome.runtime.getURL("category-tree.zh-CN.json"))
      .then((response) => {
        if (!response.ok) throw new Error(`本地中文类目缓存读取失败：HTTP ${response.status}`);
        return response.json();
      })
      .then((cache) => {
        if (cache.locale !== "zh-CN" || !cache.children_by_parent || !cache.search_items) {
          throw new Error("本地中文类目缓存格式无效");
        }
        return cache;
      });
  }
  return localCategoryTreeCachePromise;
}

async function loadLocalCategoryRulesCache() {
  if (!localCategoryRulesCachePromise) {
    localCategoryRulesCachePromise = fetch(chrome.runtime.getURL("category-rules-cache.json"))
      .then((response) => {
        if (!response.ok) throw new Error(`本地类目规则缓存读取失败：HTTP ${response.status}`);
        return response.json();
      })
      .then((cache) => {
        if (!cache.rules_by_key || !cache.category_count) throw new Error("本地类目规则缓存格式无效");
        return cache;
      });
  }
  return localCategoryRulesCachePromise;
}

function categoryRuleKey(item) {
  return `${item.category_id}:${item.type_id}`;
}

function validCategoryRules(item, rules) {
  return Boolean(
    rules
    && Number(rules.category_id) === Number(item.category_id)
    && Number(rules.type_id) === Number(item.type_id)
    && Array.isArray(rules.attributes)
    && rules.attributes.length
    && rules.rules_snapshot_hash
  );
}

async function rememberCategoryRules(item, rules) {
  if (!validCategoryRules(item, rules)) return;
  try {
    const stored = await chrome.storage.local.get(["categoryRulesCache"]);
    const cache = stored.categoryRulesCache && typeof stored.categoryRulesCache === "object"
      ? stored.categoryRulesCache
      : {};
    cache[categoryRuleKey(item)] = { saved_at: new Date().toISOString(), rules };
    const ordered = Object.entries(cache).sort((left, right) => String(right[1].saved_at).localeCompare(String(left[1].saved_at)));
    await chrome.storage.local.set({ categoryRulesCache: Object.fromEntries(ordered.slice(0, 12)) });
  } catch (_) {
    // The bundled cache remains available when browser storage is full.
  }
}

async function cachedCategoryRules(item) {
  try {
    const stored = await chrome.storage.local.get(["categoryRulesCache"]);
    const remembered = stored.categoryRulesCache?.[categoryRuleKey(item)]?.rules;
    if (validCategoryRules(item, remembered)) return remembered;
    const bundled = await loadLocalCategoryRulesCache();
    const rules = bundled.rules_by_key[categoryRuleKey(item)];
    if (!validCategoryRules(item, rules)) return null;
    return {
      ...rules,
      category_name_zh: item.name_zh || rules.category_name_zh,
      category_path_zh: item.path_zh || rules.category_path_zh,
    };
  } catch (_) {
    return null;
  }
}

function searchLocalCategoryCache(cache, query, limit = 20) {
  const normalized = String(query || "").trim().toLocaleLowerCase();
  if (!normalized) return [];
  const numeric = normalized.replace(/\D/g, "");
  const searchTerms = [normalized];
  Object.entries(cache.search_aliases || {}).forEach(([chinese, russian]) => {
    if (normalized.includes(chinese.toLocaleLowerCase())) {
      searchTerms.push(chinese.toLocaleLowerCase(), String(russian).toLocaleLowerCase());
      searchTerms.push(...String(russian).toLocaleLowerCase().split(/\s+/).filter((term) => term.length > 2));
    }
  });
  const ranked = [];
  (cache.search_items || []).forEach((item) => {
    const chinese = [item.name_zh, ...(item.path_zh || [])].join(" ").toLocaleLowerCase();
    const russian = [item.name_ru, ...(item.path || [])].join(" ").toLocaleLowerCase();
    const matchedTerms = [...new Set(searchTerms)].filter((term) => term && (chinese.includes(term) || russian.includes(term)));
    const textMatch = matchedTerms.length > 0;
    const idMatch = numeric && [String(item.category_id), String(item.type_id)].includes(numeric);
    if (!textMatch && !idMatch) return;
    const exactChinese = [item.name_zh, ...(item.path_zh || [])].some((value) => String(value || "").toLocaleLowerCase() === normalized);
    ranked.push({ score: (idMatch ? 100 : 0) + (exactChinese ? 50 : 0) + matchedTerms.length * 10, item });
  });
  ranked.sort((left, right) => right.score - left.score || left.item.name_zh.localeCompare(right.item.name_zh, "zh-CN"));
  return ranked.slice(0, limit).map((row) => row.item);
}

function showSkuDrawer(capture, options = {}) {
  removeSkuDrawer();
  latestDrawerCapture = capture;
  const selected = new Set();
  const skus = capture.skus || [];
  const previouslySelected = new Set(options.previous_selected_sku_ids || []);
  if (skus.length === 1 && isSkuSelectable(skus[0])) selected.add(skuRuntimeKey(skus[0]));
  skus.forEach((sku) => {
    if (previouslySelected.has(sku.sku_id) && isSkuSelectable(sku) && selected.size < MAX_SELECTED_SKUS) selected.add(skuRuntimeKey(sku));
  });
  const stalePrevious = [...previouslySelected].filter((id) => !skus.some((sku) => sku.sku_id === id));
  const activeFilters = new Map();
  let selectedCategory = null;
  let categoryRules = null;
  let categorySearchTimer = null;
  let currentCategoryResults = [];

  const root = document.createElement("div");
  root.id = "caf-sku-drawer-root";
  root.innerHTML = `
    <style>
      #caf-sku-drawer-root * { box-sizing: border-box; }
      .caf-mask { position: fixed; inset: 0; background: rgba(15,23,42,.25); z-index: 2147483646; }
      .caf-drawer { position: fixed; top: 0; right: 0; width: min(560px, 96vw); height: 100vh; overflow: hidden; background: #fff; z-index: 2147483647; box-shadow: -8px 0 24px rgba(15,23,42,.24); font: 13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color: #111827; display: flex; flex-direction: column; }
      .caf-head { padding: 14px 16px 10px; border-bottom: 1px solid #e5e7eb; }
      .caf-title-row { display: grid; grid-template-columns: 72px 1fr; gap: 10px; align-items: center; }
      .caf-title-row img { width: 72px; height: 72px; object-fit: cover; border: 1px solid #e5e7eb; border-radius: 6px; }
      .caf-title { font-weight: 700; font-size: 15px; max-height: 44px; overflow: hidden; }
      .caf-stats { margin-top: 8px; color: #4b5563; display: flex; flex-wrap: wrap; gap: 10px; }
      .caf-tools { padding: 10px 16px; border-bottom: 1px solid #e5e7eb; flex: 0 0 auto; max-height: 170px; overflow: auto; }
      .caf-search { width: 100%; padding: 8px; border: 1px solid #cbd5e1; border-radius: 6px; }
      .caf-filter-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 7px; color: #475569; font-size: 12px; }
      .caf-clear-filters { border: 0; background: transparent; color: #0969da; cursor: pointer; padding: 2px 0; }
      .caf-filters { display: flex; gap: 6px; flex-wrap: wrap; max-height: 86px; overflow: auto; margin-top: 5px; }
      .caf-filter { border: 1px solid #cbd5e1; background: #f8fafc; border-radius: 999px; padding: 4px 8px; cursor: pointer; }
      .caf-filter.active { border-color: #0969da; background: #eef6ff; color: #0969da; }
      .caf-list { overflow: auto; padding: 0 16px 8px; flex: 3 1 0; min-height: 180px; background: #fff; }
      .caf-sku-list-title { position: sticky; top: 0; z-index: 2; margin: 0 -16px; padding: 8px 16px; border-bottom: 1px solid #e5e7eb; background: #fff; color: #111827; font-weight: 700; }
      .caf-sku { display: grid; grid-template-columns: 24px 56px 1fr; gap: 10px; padding: 10px 0; border-bottom: 1px solid #edf2f7; align-items: start; }
      .caf-sku img { width: 56px; height: 56px; object-fit: cover; border: 1px solid #e5e7eb; border-radius: 4px; background: #f8fafc; }
      .caf-name { font-weight: 650; }
      .caf-meta { color: #4b5563; margin-top: 3px; }
      .caf-id { color: #6b7280; font-size: 12px; margin-top: 2px; word-break: break-all; }
      .caf-disabled { opacity: .55; }
      .caf-category { border-top: 1px solid #e5e7eb; padding: 10px 16px; background: #f8fafc; overflow: auto; flex: 2 1 0; min-height: 250px; }
      .caf-category-title { font-weight: 700; margin-bottom: 6px; }
      .caf-category-cache-status { margin: -2px 0 6px; color: #047857; font-size: 12px; }
      .caf-category-search-row { display: grid; grid-template-columns: minmax(0, 1fr) 72px; gap: 6px; }
      .caf-category-search { width: 100%; padding: 8px; border: 1px solid #cbd5e1; border-radius: 6px; }
      .caf-category-search-button { border: 1px solid #0969da; background: #0969da; color: #fff; border-radius: 6px; cursor: pointer; }
      .caf-category-section-title { margin-top: 7px; color: #475569; font-size: 12px; font-weight: 650; }
      .caf-category-results { display: grid; gap: 5px; max-height: 104px; overflow: auto; margin-top: 5px; }
      .caf-category-item { text-align: left; border: 1px solid #d8dee4; background: #fff; border-radius: 6px; padding: 7px; cursor: pointer; }
      .caf-category-item.active { border-color: #0969da; background: #eef6ff; }
      .caf-category-path { display: block; color: #4b5563; font-size: 12px; }
      .caf-category-selected { min-height: 18px; margin-top: 6px; color: #0969da; }
      .caf-category-shortcuts { display: flex; gap: 6px; margin-top: 6px; }
      .caf-category-shortcuts button { border: 1px solid #cbd5e1; background: #fff; border-radius: 5px; padding: 4px 7px; cursor: pointer; }
      .caf-category-tree { max-height: 156px; overflow: auto; margin-top: 5px; border: 1px solid #d8dee4; border-radius: 6px; background: #fff; padding: 4px; }
      .caf-tree-row { min-width: 0; }
      .caf-tree-node { width: 100%; display: flex; align-items: center; gap: 5px; border: 0; background: transparent; border-radius: 4px; padding: 5px 6px; text-align: left; cursor: pointer; color: #111827; }
      .caf-tree-node:hover { background: #f1f5f9; }
      .caf-tree-node.active { background: #eef6ff; color: #0969da; font-weight: 650; }
      .caf-tree-toggle { display: inline-block; width: 14px; flex: 0 0 14px; color: #64748b; }
      .caf-tree-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .caf-tree-children { margin-left: 15px; border-left: 1px solid #e2e8f0; padding-left: 3px; }
      .caf-tree-empty { padding: 6px; color: #64748b; }
      .caf-actions { padding: 12px 16px; border-top: 1px solid #e5e7eb; display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; flex: 0 0 auto; }
      .caf-actions button { border: 1px solid #cbd5e1; background: #f8fafc; border-radius: 6px; padding: 9px; cursor: pointer; }
      .caf-actions .primary { background: #0969da; border-color: #0969da; color: #fff; }
      .caf-message { color: #b42318; min-height: 18px; margin-top: 6px; }
      .caf-stale { color: #b45309; margin-top: 6px; }
      @media (max-height: 800px) {
        .caf-head { padding-top: 8px; }
        .caf-title-row { grid-template-columns: 56px 1fr; }
        .caf-title-row img { width: 56px; height: 56px; }
        .caf-tools { max-height: 125px; padding-top: 7px; padding-bottom: 7px; }
        .caf-list { min-height: 140px; }
        .caf-category { min-height: 210px; }
        .caf-actions { padding-top: 8px; padding-bottom: 8px; }
      }
    </style>
    <div class="caf-mask"></div>
    <aside class="caf-drawer" role="dialog" aria-label="SKU选择">
      <div class="caf-head">
        <div class="caf-title-row">
          <img class="caf-main-img" alt="">
          <div>
            <div class="caf-title"></div>
            <div class="caf-stats"></div>
            <div class="caf-stale"></div>
          </div>
        </div>
      </div>
      <div class="caf-tools">
        <input class="caf-search" placeholder="搜索SKU、颜色、尺寸、型号">
        <div class="caf-filter-heading"><span>规格筛选（蓝色仅表示筛选，不代表已选SKU）</span><button type="button" class="caf-clear-filters">清除筛选</button></div>
        <div class="caf-filters"></div>
        <div class="caf-message"></div>
      </div>
      <div class="caf-list"></div>
      <div class="caf-category">
        <div class="caf-category-title">最终 Ozon 类目（必选）</div>
        <div class="caf-category-cache-status">本地中文类目缓存：正在读取…</div>
        <div class="caf-category-search-row">
          <input class="caf-category-search" type="search" placeholder="输入中文、俄文、关键词或编号">
          <button type="button" class="caf-category-search-button">搜索</button>
        </div>
        <div class="caf-category-shortcuts">
          <button type="button" class="caf-category-recent">最近类目</button>
          <button type="button" class="caf-category-favorites">收藏类目</button>
          <button type="button" class="caf-category-favorite-current">收藏当前</button>
        </div>
        <div class="caf-category-selected">尚未选择，不能完成采集</div>
        <div class="caf-category-section-title">搜索结果（输入中文、俄文、关键词或编号）</div>
        <div class="caf-category-results"></div>
        <div class="caf-category-section-title">本地中文类目树（点击逐级展开，叶子类目才可选择）</div>
        <div class="caf-category-tree" role="tree" aria-label="Ozon类目树"></div>
      </div>
      <div class="caf-actions">
        <button class="caf-select-all">全选</button>
        <button class="caf-clear">全部取消</button>
        <button class="caf-debug">导出诊断</button>
        <button class="caf-cancel">取消返回</button>
        <button class="caf-confirm primary">确认采集</button>
      </div>
    </aside>
  `;
  document.documentElement.appendChild(root);

  const title = root.querySelector(".caf-title");
  const mainImg = root.querySelector(".caf-main-img");
  const stats = root.querySelector(".caf-stats");
  const stale = root.querySelector(".caf-stale");
  const search = root.querySelector(".caf-search");
  const filters = root.querySelector(".caf-filters");
  const message = root.querySelector(".caf-message");
  const list = root.querySelector(".caf-list");
  const categorySearch = root.querySelector(".caf-category-search");
  const categorySearchButton = root.querySelector(".caf-category-search-button");
  const categoryCacheStatus = root.querySelector(".caf-category-cache-status");
  const categoryResults = root.querySelector(".caf-category-results");
  const categoryTree = root.querySelector(".caf-category-tree");
  const categorySelected = root.querySelector(".caf-category-selected");
  title.textContent = capture.title_cn || "unknown";
  mainImg.src = capture.main_images?.[0]?.url || "";
  stale.textContent = stalePrevious.length ? `原商品中已失效SKU：${stalePrevious.join("、")}` : "";

  function setMessage(text) {
    message.textContent = text || "";
  }

  function refreshCategoryActiveState() {
    root.querySelectorAll("[data-category-id][data-type-id]").forEach((node) => {
      const active = selectedCategory
        && Number(node.dataset.categoryId) === selectedCategory.category_id
        && Number(node.dataset.typeId) === selectedCategory.type_id;
      node.classList.toggle("active", Boolean(active));
    });
  }

  async function selectCategory(item) {
    selectedCategory = item;
    categoryRules = null;
    categorySelected.textContent = `正在加载“${item.name_zh}”的官方必填属性、字典值和 is_aspect…`;
    refreshCategoryActiveState();
    try {
      categoryRules = await collectorApi("/api/collector/categories/rules", {
        method: "POST",
        body: JSON.stringify({ category_id: item.category_id, type_id: item.type_id, allow_readonly_fetch: false })
      });
      await rememberCategoryRules(item, categoryRules);
      const sourceLabel = categoryRules.offline_fallback ? "本地离线规则" : "完整本地规则";
      categorySelected.textContent = `已选：${item.name_zh} · 必填 ${categoryRules.required_attribute_ids.length} · SKU维度 ${categoryRules.aspect_attribute_ids.length} · ${sourceLabel}`;
      setMessage("");
    } catch (error) {
      categoryRules = await cachedCategoryRules(item);
      if (categoryRules) {
        categorySelected.textContent = `已选：${item.name_zh} · 必填 ${categoryRules.required_attribute_ids.length} · SKU维度 ${categoryRules.aspect_attribute_ids.length} · 插件离线缓存`;
        setMessage("主电脑规则接口暂不可用，已自动使用插件本地缓存，不阻断采集。缺失字典值保持unknown。");
      } else {
        categoryRules = null;
        categorySelected.textContent = "规则缓存读取失败，不能完成采集";
        setMessage(error.message);
      }
    }
  }

  function renderCategoryResults(items) {
    currentCategoryResults = items || [];
    categoryResults.innerHTML = "";
    currentCategoryResults.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `caf-category-item${selectedCategory?.category_id === item.category_id && selectedCategory?.type_id === item.type_id ? " active" : ""}`;
      button.dataset.categoryId = String(item.category_id);
      button.dataset.typeId = String(item.type_id);
      const name = document.createElement("strong");
      name.textContent = item.name_zh;
      const path = document.createElement("span");
      path.className = "caf-category-path";
      path.textContent = `${(item.path_zh || []).join(" / ")} · category_id ${item.category_id} · type_id ${item.type_id}`;
      button.append(name, path);
      button.addEventListener("click", () => selectCategory(item));
      categoryResults.appendChild(button);
    });
    if (!currentCategoryResults.length) categoryResults.textContent = "没有找到匹配类目，可继续从下方类目树逐级浏览";
  }

  async function loadCategoryTree(parentId = "root", container = categoryTree) {
    container.innerHTML = '<div class="caf-tree-empty">正在加载类目树…</div>';
    try {
      const cache = await loadLocalCategoryTreeCache();
      categoryCacheStatus.textContent = `本地中文类目缓存：已加载 ${cache.item_count} 个最终类目 · 版本 ${cache.cache_version}`;
      const items = cache.children_by_parent[parentId];
      if (!items) throw new Error("本地类目树节点不存在");
      container.innerHTML = "";
      items.forEach((item) => {
        const row = document.createElement("div");
        row.className = "caf-tree-row";
        const button = document.createElement("button");
        button.type = "button";
        button.className = "caf-tree-node";
        button.setAttribute("role", "treeitem");
        button.title = (item.path_zh || []).join(" / ");
        if (item.kind === "leaf") {
          button.dataset.categoryId = String(item.category_id);
          button.dataset.typeId = String(item.type_id);
        }
        const toggle = document.createElement("span");
        toggle.className = "caf-tree-toggle";
        toggle.textContent = item.has_children ? "▶" : "•";
        const label = document.createElement("span");
        label.className = "caf-tree-label";
        label.textContent = item.name_zh;
        button.append(toggle, label);
        row.appendChild(button);
        if (item.has_children) {
          const children = document.createElement("div");
          children.className = "caf-tree-children";
          children.hidden = true;
          row.appendChild(children);
          button.addEventListener("click", async () => {
            if (children.dataset.loaded) {
              children.hidden = !children.hidden;
              toggle.textContent = children.hidden ? "▶" : "▼";
              return;
            }
            await loadCategoryTree(item.node_id, children);
            children.dataset.loaded = "true";
            children.hidden = false;
            toggle.textContent = "▼";
          });
        } else {
          button.addEventListener("click", () => selectCategory(item));
        }
        container.appendChild(row);
      });
      if (!items.length) container.innerHTML = '<div class="caf-tree-empty">此分支没有可选子类目</div>';
      refreshCategoryActiveState();
    } catch (error) {
      categoryCacheStatus.textContent = "本地中文类目缓存：读取失败";
      container.innerHTML = "";
      const failure = document.createElement("div");
      failure.className = "caf-tree-empty";
      failure.textContent = `类目树加载失败：${error.message}`;
      container.appendChild(failure);
    }
  }

  async function loadCategorySearch(query = "") {
    const enteredQuery = String(query || "").trim();
    categoryResults.textContent = enteredQuery ? `正在搜索“${enteredQuery}”…` : "正在读取最近类目…";
    try {
      let limit = 20;
      if (!enteredQuery) {
        const prefs = await collectorApi("/api/collector/categories/preferences");
        const combined = [...(prefs.favorites || []), ...(prefs.recent || [])];
        const unique = combined.filter((item, index) => combined.findIndex((other) => other.category_id === item.category_id && other.type_id === item.type_id) === index);
        if (unique.length) {
          renderCategoryResults(unique);
          return;
        }
        query = capture.title_cn || "";
        limit = 3;
      }
      const cache = await loadLocalCategoryTreeCache();
      renderCategoryResults(searchLocalCategoryCache(cache, query, limit));
    } catch (error) {
      categoryResults.textContent = `类目加载失败：${error.message}`;
    }
  }

  function runCategorySearch(delay = 0) {
    window.clearTimeout(categorySearchTimer);
    categoryResults.textContent = categorySearch.value.trim()
      ? `正在搜索“${categorySearch.value.trim()}”…`
      : "请输入中文、俄文、关键词或类目编号";
    categorySearchTimer = window.setTimeout(() => loadCategorySearch(categorySearch.value), delay);
  }

  categorySearch.addEventListener("input", () => {
    runCategorySearch(180);
  });
  categorySearch.addEventListener("change", () => runCategorySearch());
  categorySearch.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      runCategorySearch();
    }
  });
  categorySearchButton.addEventListener("click", () => runCategorySearch());
  root.querySelector(".caf-category-recent").addEventListener("click", async () => {
    const prefs = await collectorApi("/api/collector/categories/preferences");
    renderCategoryResults(prefs.recent || []);
  });
  root.querySelector(".caf-category-favorites").addEventListener("click", async () => {
    const prefs = await collectorApi("/api/collector/categories/preferences");
    renderCategoryResults(prefs.favorites || []);
  });
  root.querySelector(".caf-category-favorite-current").addEventListener("click", async () => {
    if (!selectedCategory) {
      setMessage("请先选择一个最终Ozon类目。");
      return;
    }
    await collectorApi("/api/collector/categories/favorite", {
      method: "PUT",
      body: JSON.stringify({ category_id: selectedCategory.category_id, type_id: selectedCategory.type_id, favorite: true })
    });
    categorySelected.textContent = `${categorySelected.textContent} · 已收藏`;
  });
  loadCategoryTree();

  function renderStats(visibleCount = skus.length) {
    const realIdCount = skus.filter((sku) => isRealSkuId(sku.sku_id)).length;
    stats.textContent = `原始SKU总数：${skus.length} | 真实SKU ID：${realIdCount}/${skus.length} | 当前显示：${visibleCount} | 已选：${selected.size} | 最大可选数量：${MAX_SELECTED_SKUS}`;
  }

  function renderFilters() {
    filters.innerHTML = "";
    getFilterValues(skus).forEach((group) => {
      group.values.forEach((value) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "caf-filter";
        button.textContent = `${group.name}: ${value}`;
        if (activeFilters.get(group.name) === value) button.classList.add("active");
        button.addEventListener("click", () => {
          if (activeFilters.get(group.name) === value) activeFilters.delete(group.name);
          else activeFilters.set(group.name, value);
          render();
        });
        filters.appendChild(button);
      });
    });
  }

  function skuMatches(sku) {
    const query = search.value.trim().toLowerCase();
    const haystack = [skuDisplayName(sku), skuDimensions(sku), sku.sku_id, skuRuntimeKey(sku), String(sku.purchase_price || "")].join(" ").toLowerCase();
    if (query && !haystack.includes(query)) return false;
    for (const [name, value] of activeFilters.entries()) {
      if (!(sku.option_values || []).some((option) => (option.name_cn || option.name || "规格") === name && (option.value_cn || option.value || "unknown") === value)) {
        return false;
      }
    }
    return true;
  }

  function visibleSelectableSkus() {
    return skus.filter((sku) => skuMatches(sku) && isSkuSelectable(sku));
  }

  function render() {
    list.innerHTML = "";
    renderFilters();
    const visible = skus.filter(skuMatches);
    renderStats(visible.length);
    const listTitle = document.createElement("div");
    listTitle.className = "caf-sku-list-title";
    listTitle.textContent = `选择SKU（勾选下方商品）· 当前显示 ${visible.length} · 已选 ${selected.size}`;
    list.appendChild(listTitle);
    visible.forEach((sku) => {
      const row = document.createElement("label");
      const selectable = isSkuSelectable(sku);
      row.className = `caf-sku${selectable ? "" : " caf-disabled"}`;
      const runtimeKey = skuRuntimeKey(sku);
      const checked = selected.has(runtimeKey) ? "checked" : "";
      const disabled = selectable ? "" : "disabled";
      row.innerHTML = `
        <input type="checkbox" ${checked} ${disabled}>
        <img alt="">
        <div>
          <div class="caf-name"></div>
          <div class="caf-meta"></div>
          <div class="caf-id"></div>
        </div>
      `;
      const rowImg = row.querySelector("img");
      if (sku.image_url && sku.image_url !== "unknown" && sku.sku_image_missing !== true) {
        rowImg.src = sku.image_url;
        rowImg.title = sku.source_data?.sku_image_source || "sku image";
      } else {
        rowImg.removeAttribute("src");
        rowImg.alt = "无SKU图";
        rowImg.title = "1688未提供SKU专属图片";
      }
      row.querySelector(".caf-name").textContent = skuDisplayName(sku);
      row.querySelector(".caf-meta").textContent = `${skuDimensions(sku) || "规格: unknown"} | ¥${sku.purchase_price ?? "unknown"} | ${sku.price_source || "unknown"} | ${sku.availability || "unknown"} | 起订量: ${capture.minimum_order_quantity?.raw_text || "unknown"}`;
      row.querySelector(".caf-id").textContent = `sku_id: ${isRealSkuId(sku.sku_id) ? sku.sku_id : "unknown（未解析真实1688 sku_id）"}`;
      row.querySelector("input").addEventListener("change", (event) => {
        if (event.target.checked) {
          if (selected.size >= MAX_SELECTED_SKUS) {
            event.target.checked = false;
            setMessage("单个商品最多选择10个SKU，请先取消其他SKU。");
            return;
          }
          selected.add(runtimeKey);
        } else {
          selected.delete(runtimeKey);
        }
        setMessage("");
        renderStats(visible.length);
      });
      list.appendChild(row);
    });
  }

  search.addEventListener("input", render);
  root.querySelector(".caf-clear-filters").addEventListener("click", () => {
    activeFilters.clear();
    search.value = "";
    setMessage("");
    render();
    list.scrollTop = 0;
  });
  root.querySelector(".caf-select-all").addEventListener("click", () => {
    const visibleSelectable = visibleSelectableSkus();
    const alreadySelectedVisible = visibleSelectable.filter((sku) => selected.has(skuRuntimeKey(sku))).length;
    const newSelectionCount = visibleSelectable.length - alreadySelectedVisible;
    const remaining = MAX_SELECTED_SKUS - selected.size;
    if (!visibleSelectable.length) {
      setMessage("当前筛选结果没有可选择SKU。");
      return;
    }
    if (newSelectionCount > remaining) {
      setMessage(`当前可见可选SKU为 ${visibleSelectable.length} 个，剩余可选 ${remaining} 个。请先搜索或筛选到10个以内。`);
      return;
    }
    visibleSelectable.forEach((sku) => selected.add(skuRuntimeKey(sku)));
    setMessage("");
    render();
  });
  root.querySelector(".caf-clear").addEventListener("click", () => {
    selected.clear();
    setMessage("");
    render();
  });
  root.querySelector(".caf-debug").addEventListener("click", () => {
    downloadJson("sku-debug.json", buildSkuDebugExport({
      ...capture,
      raw_snapshot: {
        ...(capture.raw_snapshot || {}),
        all_raw_skus: skus,
        sku_debug: buildSkuDebug(skus, {
          sku_source: capture.raw_snapshot?.sku_source,
          structured_count: capture.raw_snapshot?.structured_data_summary?.structured_candidates
        })
      }
    }));
    setMessage("已导出 sku-debug.json。");
  });
  root.querySelector(".caf-cancel").addEventListener("click", () => {
    removeSkuDrawer();
    window.postMessage({ type: "CAF_SKU_SELECTION_CANCELLED" }, "*");
  });
  root.querySelector(".caf-confirm").addEventListener("click", () => {
    if (selected.size < 1) {
      setMessage("请至少选择1个SKU。");
      return;
    }
    const selectedSkus = skus
      .filter((sku) => selected.has(skuRuntimeKey(sku)))
      .map((sku, index) => ({ ...sku, selection_order: index + 1 }));
    const selectedIds = selectedSkus.map((sku) => sku.sku_id).filter(isRealSkuId);
    const selectedKeys = selectedSkus.map(skuRuntimeKey);
    if (selectedIds.length !== selectedSkus.length) {
      setMessage(`所选SKU中有 ${selectedSkus.length - selectedIds.length} 个缺少真实1688 sku_id，请刷新页面后重试。`);
      return;
    }
    if (!selectedCategory || !categoryRules) {
      setMessage("请先选择最终Ozon类目，并等待必填属性、字典值和is_aspect规则加载完成。");
      return;
    }
    const selectedCapture = {
      ...capture,
      skus: selectedSkus,
      selected_sku_ids: selectedIds,
      selected_sku_keys: selectedKeys,
      sku_selection: {
        original_sku_count: skus.length,
        available_sku_count: skus.filter(isSkuSelectable).length,
        selected_sku_count: selectedSkus.length,
        unselected_sku_count: Math.max(skus.length - selectedSkus.length, 0),
        selected_sku_ids: selectedIds,
        selected_sku_keys: selectedKeys,
        real_sku_id_count: selectedIds.length,
        missing_real_sku_id_count: selectedSkus.length - selectedIds.length,
        selected_at: new Date().toISOString()
      },
      raw_snapshot: {
        ...(capture.raw_snapshot || {}),
        all_raw_skus: skus,
        sku_debug: buildSkuDebug(skus),
        selected_sku_ids: selectedIds,
        selected_sku_keys: selectedKeys,
        sku_selection_time: new Date().toISOString()
      },
      ozon_category_selection: {
        category_id: selectedCategory.category_id,
        type_id: selectedCategory.type_id,
        category_path: selectedCategory.path,
        category_name_zh: selectedCategory.name_zh,
        category_path_zh: selectedCategory.path_zh,
        selected_at: new Date().toISOString(),
        rules_snapshot: categoryRules
      }
    };
    removeSkuDrawer();
    showToast("正在保存所选SKU...");
    postSelectedCapture(selectedCapture)
      .then((result) => {
        showToast(`采集完成：${result.product_id}\nSKU：${result.counts?.skus ?? selectedSkus.length}`);
      })
      .catch((error) => {
        showToast(`采集失败：${error.message}`, true);
      });
  });
  render();
  loadCategorySearch(capture.title_cn || "");
}

function buildCapture() {
  const structured = parseJsonScripts();
  const title = extractTitle(structured);
  const supplier = extractSupplier(structured);
  const attrs = extractAttributes();
  const price = extractPriceInfo(structured);
  const moq = extractMinimumOrder();
  const mainImages = extractMainImages();
  const detailImages = extractDetailImages();
  const productRangePrice = productPriceForQuantity(price.value, moq.value.value);
  const skus = extractSkus(structured, price.value.price_ranges.length > 0, productRangePrice);
  const warnings = [];
  const realSkuIdCount = skus.values.filter((sku) => isRealSkuId(sku.sku_id)).length;
  const skuDebug = buildSkuDebug(skus.values, {
    sku_source: skus.source || "unknown",
    structured_count: structured.length,
    window_variable_count: pageWindowProductData.length
  });
  const skuPropertyImageDebug = buildSkuPropertyImageDebug(skus.propertyGroups, skus.values);
  const diagnostics = [
    diagnostic("title_cn", title.candidates.length ? "script_init_data" : "candidate_selector", title.value !== "unknown", title.value === "unknown" ? "No title candidate found" : null, title.candidates.length),
    diagnostic("supplier_name", supplier.candidates.length ? "script_init_data" : "candidate_selector", supplier.value !== "unknown", supplier.value === "unknown" ? "No supplier candidate found" : null, supplier.candidates.length),
    diagnostic("product_attributes", "candidate_selector", attrs.values.length > 0, attrs.values.length ? null : "No product attribute candidates found", attrs.values.length),
    diagnostic("price_information", "script_init_data", price.value.price_ranges.length > 0, price.value.price_ranges.length ? null : "No price candidates found", price.candidateCount),
    diagnostic("minimum_order_quantity", "text_inference", moq.value.value !== null, moq.value.value === null ? "No minimum order quantity text found" : null, moq.candidateCount),
    diagnostic("main_images", "candidate_selector", mainImages.values.length > 0, mainImages.values.length ? null : "No main image candidates found", mainImages.values.length),
    diagnostic("detail_images", "dom_semantic", detailImages.values.length > 0, detailImages.values.length ? null : "No detail image candidates found in detail area", detailImages.values.length),
    diagnostic("skus", skus.source || (skus.candidateCount ? "script_init_data" : "dom_semantic"), skus.values.length > 0, skus.values.length ? null : "No SKU candidates found in script data or SKU DOM area", skus.values.length)
  ];
  diagnostics.forEach((item) => {
    if (!item.hit) warnings.push(`${item.field}: ${item.failure_reason}`);
  });
  if (skus.values.length && realSkuIdCount < skus.values.length) {
    warnings.push(`skus: ${skus.values.length - realSkuIdCount} SKU missing real 1688 sku_id`);
  }
  if (skuDebug.missing_image_skus.length) {
    warnings.push(`skus: ${skuDebug.missing_image_skus.length} SKU missing dedicated image`);
  }
  if (skuDebug.missing_price_skus.length) {
    warnings.push(`skus: ${skuDebug.missing_price_skus.length} SKU missing sku-specific price`);
  }
  return {
    source_platform: "1688",
    source_url: location.href,
    captured_at: new Date().toISOString(),
    page_title: document.title || "unknown",
    title_cn: title.value,
    supplier_name: supplier.value,
    product_attributes: attrs.values,
    price_information: price.value,
    minimum_order_quantity: moq.value,
    main_images: mainImages.values,
    detail_images: detailImages.values,
    skus: skus.values,
    sku_property_groups: skus.propertyGroups || [],
    field_diagnostics: diagnostics,
    capture_warnings: warnings,
    plugin_version: PLUGIN_VERSION,
    raw_snapshot: {
      structured_data_summary: {
        script_count: document.scripts.length,
        structured_candidates: structured.length
      },
      candidate_selectors: {
        title: title.selectors,
        supplier: supplier.selectors,
        attributes: attrs.selectors,
        price: price.selectors,
        main_images: mainImages.selectors,
        detail_images: detailImages.selectors
      },
      title_candidates: title.candidates.slice(0, 10),
      supplier_candidates: supplier.candidates.slice(0, 10)
      ,
      sku_candidate_count: skus.values.length,
      sku_real_id_count: realSkuIdCount,
      sku_missing_real_id_count: Math.max(skus.values.length - realSkuIdCount, 0),
      sku_debug: skuDebug,
      sku_property_image_debug: skuPropertyImageDebug,
      sku_source: skus.source || "unknown"
    },
    is_collectable: /1688\.com/.test(location.hostname),
    reason: /1688\.com/.test(location.hostname) ? null : "Not a 1688 page"
  };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "OPEN_SKU_SELECTOR") {
    showSkuDrawer(message.capture, {
      previous_selected_sku_ids: message.previous_selected_sku_ids || []
    });
    sendResponse({ opened: true });
    return true;
  }
  if (message.type === "COLLECTOR_PREVIEW" || message.type === "COLLECTOR_CAPTURE") {
    sendResponse(buildCapture());
  }
  if (message.type === "EXPORT_SKU_DEBUG") {
    const capture = buildCapture();
    const debug = buildSkuDebugExport(capture);
    downloadJson("sku-debug.json", debug);
    sendResponse(debug);
  }
  return true;
});
