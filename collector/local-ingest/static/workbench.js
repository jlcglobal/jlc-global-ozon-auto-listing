const root = document.getElementById("view-root");
const shell = document.querySelector(".app-shell");
const notice = document.getElementById("notice");
const searchInput = document.getElementById("global-search");

const viewMeta = {
  review: ["预览检查", "直接修改商品资料和图片"],
  confirm: ["批量确认", "本批次只确认一次"],
  inbox: ["我的采集箱", "只显示我采集的商品"],
  attention: ["需要我处理", "问题、失败和待上传商品集中在这里"],
  listed: ["已上架商品", "查看已经通过Ozon审核的商品"],
  batches: ["任务状态", "生成、上传和失败状态"],
  shops: ["店铺设置", "添加、验证和管理Ozon店铺"],
  settings: ["系统设置", "生产安全规则"],
};

const STEP_LABELS = {
  queue: "排队等待", validate_source: "检查采集数据", product_analysis: "分析商品事实",
  category_match: "匹配Ozon类目", variant_rules: "判断SKU变体", measurements: "处理商品和包装尺寸",
  offer_exists_check: "检查Ozon是否已有商品", upload_feasibility: "检查上传条件",
  product_positioning: "确定商品定位", russian_copy: "生成俄文标题和文案",
  style_selector: "确定图片视觉风格", image_plan: "规划图片方案", image_generation: "生成商品图片",
  image_qc: "进行图片质检", marketplace_content: "生成Ozon商品资料", field_completion: "填写Ozon属性",
  ozon_upload: "提交Ozon",
};

const state = {
  view: "inbox",
  products: [],
  currentProductId: null,
  currentProduct: null,
  currentImageSlot: null,
  saveTimer: null,
  pendingDraftPatch: {},
  pollTimer: null,
  autoAdvance: true,
  reviewMode: "manual",
  reviewDepth: "quick",
  queueCollapsed: false,
  selectedStoreIds: new Set(),
  selectedBatchProducts: new Set(),
  batchProducts: [],
  draftSaveFailed: false,
  editingStoreId: null,
  shops: [],
  lastImageSignature: "",
  deletePreview: null,
  accessCodeResolve: null,
  accessCodeRequest: null,
  categoryCandidates: [],
  categoryChoice: null,
  categoryRules: null,
  categorySearchTimer: null,
  workbenchSettings: {auto_mode_enabled:false, default_review_mode:"manual", learning_threshold:2, fixed_cny_to_rub:12, rub_rounding:10},
  inboxFilter: "全部",
  dragImageSlot: null,
  confirmationBatchId: null,
  confirmationData: null,
  confirmationProductId: null,
  confirmationEvidenceTab: "sku",
  session: null,
  notifications: [],
  notificationTimer: null,
};

const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
const display = (value, fallback = "未确认") => value === null || value === undefined || value === "" || value === "unknown" ? fallback : String(value);
const number = (value, digits = 0) => typeof value === "number" ? value.toLocaleString("zh-CN", {maximumFractionDigits: digits}) : "未确认";
const dateText = (value) => {
  if (!value || value === "unknown") return "未记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", {month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit"});
};

function toast(message, type = "info", duration = 3600) {
  notice.textContent = message;
  notice.className = `notice ${type}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => notice.classList.add("hidden"), duration);
}

function maybeNotifyListingSuccess(product) {
  const status = String(product.raw_status || product.status?.status || "").toUpperCase();
  if (!["UPLOADED", "ACTIVE"].includes(status)) return;
  const productId = product.product_id || "unknown";
  const batchId = product.batch_id || product.status?.batch_id || "unknown";
  const signature = `${batchId}|${status}`;
  const storageKey = `caf-listing-success:${productId}`;
  if (localStorage.getItem(storageKey) === signature) return;
  localStorage.setItem(storageKey, signature);
  const title = product.title_cn || product.source?.title_cn || productId;
  const successfulStores = (product.stores || [])
    .filter((shop) => product.publications?.stores?.[shop.id]?.status === "SUCCESS")
    .map((shop) => shop.display_name);
  const storeText = successfulStores.length ? `（${successfulStores.join("、")}）` : "";
  toast(`上架成功：${title} 已通过Ozon审核并正式上架${storeText}`, "success", 9000);
}

async function api(url, options = {}, accessAttempts = 0) {
  const accessCode = localStorage.getItem("cafAccessCode") || "";
  const headers = {"Content-Type": "application/json", ...(options.headers || {})};
  if (accessCode) headers["X-Factory-Access-Code"] = accessCode;
  const response = await fetch(url, {...options, headers});
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && data.detail?.code === "ACCESS_CODE_REQUIRED" && accessAttempts < 3) {
    localStorage.removeItem("cafAccessCode");
    const entered = await requestAccessCode(data.detail.message);
    localStorage.setItem("cafAccessCode", entered);
    return api(url, options, accessAttempts + 1);
  }
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : data.detail?.message || JSON.stringify(data.detail || data));
  return data;
}

function requestAccessCode(message) {
  if (state.accessCodeRequest) return state.accessCodeRequest;
  const dialog = document.getElementById("access-dialog");
  const input = document.getElementById("access-code-input");
  document.getElementById("access-code-error").textContent = message || "请输入工作室访问码";
  input.value = "";
  dialog.showModal();
  state.accessCodeRequest = new Promise((resolve) => { state.accessCodeResolve = resolve; });
  return state.accessCodeRequest;
}

function riskPill(risk) {
  const labels = {high: "高风险", medium: "中风险", low: "低风险"};
  return `<span class="status-pill ${escapeHtml(risk?.level || "low")}">${labels[risk?.level] || "低风险"}</span>`;
}

function statePill(value) {
  const map = {"完成":"completed", "失败":"failed", "处理中":"processing", "待处理":""};
  return `<span class="status-pill ${map[value] || ""}">${escapeHtml(value)}</span>`;
}

function setHeading(view) {
  const [title, subtitle] = viewMeta[view] || viewMeta.home;
  document.getElementById("page-title").textContent = title;
  document.getElementById("page-subtitle").textContent = subtitle;
  document.querySelectorAll(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === (view === "confirm" ? "batches" : view)));
}

async function navigate(view, options = {}) {
  state.view = view;
  setHeading(view);
  root.innerHTML = `<div class="empty-state"><strong>正在读取真实数据</strong><span>请稍候</span></div>`;
  const renderers = {home: renderHome, review: renderReview, confirm: renderBatchConfirmation, inbox: renderInbox, attention: renderAttention, listed: renderListed, batches: renderBatches, images: renderImages, risks: renderRisks, shops: renderShops, skills: renderSkills, experience: renderExperience, logs: renderLogs, settings: renderSettings};
  try {
    await (renderers[view] || renderHome)(options);
  } catch (error) {
    root.innerHTML = `<div class="empty-state"><strong>页面读取失败</strong><span>${escapeHtml(error.message)}</span></div>`;
  }
  if (window.innerWidth <= 700) shell.classList.remove("mobile-nav-open");
}

async function loadProducts(query = "") {
  const data = await api(`/api/workbench/products?page_size=100&q=${encodeURIComponent(query)}`);
  state.products = data.items;
  state.products.forEach(maybeNotifyListingSuccess);
  if (!state.currentProductId || !state.products.some((item) => item.product_id === state.currentProductId)) {
    state.currentProductId = state.products[0]?.product_id || null;
  }
  return data;
}

async function loadWorkbenchSettings() {
  state.workbenchSettings = await api("/api/workbench/settings");
  const checkbox = document.getElementById("global-auto-mode");
  if (checkbox) checkbox.checked = Boolean(state.workbenchSettings.auto_mode_enabled);
  const label = document.getElementById("global-mode-label");
  const note = document.getElementById("global-mode-note");
  if (label) label.textContent = state.workbenchSettings.auto_mode_enabled ? "自动模式" : "手动检查";
  if (note) note.textContent = state.workbenchSettings.auto_mode_enabled ? "运行任务后自动质检并上传" : "自动模式已关闭";
  return state.workbenchSettings;
}

async function loadSession() {
  state.session = await api("/api/workbench/session");
  const operator = state.session.operator || {};
  document.getElementById("operator-name").textContent = operator.display_name || "工作室成员";
  document.getElementById("operator-role").textContent = operator.role === "owner" ? "负责人设置 · 只看我的商品" : "工作室成员 · 只看我的商品";
  document.querySelectorAll("[data-owner-only]").forEach((element) => element.classList.toggle("is-hidden", !state.session.can_manage_settings));
  return state.session;
}

async function pollNotifications({showDesktop = true} = {}) {
  try {
    const data = await api("/api/workbench/notifications");
    state.notifications = data.items || [];
    const count = data.count ? String(data.count) : "";
    document.getElementById("notification-count").textContent = count;
    document.getElementById("attention-nav-count").textContent = count;
    if (showDesktop && "Notification" in window && Notification.permission === "granted") {
      for (const item of state.notifications) {
        const key = `caf-notification:${state.session?.operator?.id || "operator"}:${item.id}`;
        if (localStorage.getItem(key)) continue;
        localStorage.setItem(key, new Date().toISOString());
        const systemNotice = new Notification(item.title, {body:`${item.product_title}：${item.message}`, tag:item.id});
        systemNotice.onclick = () => {
          window.focus();
          state.currentProductId = item.product_id;
          navigate(item.type === "question" ? "attention" : "review", {productId:item.product_id});
          systemNotice.close();
        };
      }
    }
    return data;
  } catch (_) { return {items:[], count:0}; }
}

async function enableDesktopNotifications() {
  if (!("Notification" in window)) return toast("当前浏览器不支持电脑通知", "error");
  const permission = await Notification.requestPermission();
  if (permission !== "granted") return toast("电脑通知未开启，可在浏览器网站权限中重新允许", "error");
  toast("电脑通知已开启；只通知属于你的商品", "success");
  await pollNotifications();
}

async function renderHome() {
  const [summary, products, logs] = await Promise.all([
    api("/api/workbench/summary"),
    api("/api/workbench/products?page_size=6"),
    api("/api/workbench/logs"),
  ]);
  const focusView = summary.focus.type === "risk" ? "risks" : summary.focus.type === "batch" ? "batches" : summary.focus.type === "inbox" ? "inbox" : "review";
  root.innerHTML = `
    <section class="section-head"><div><h2>当前生产状态</h2><p>${summary.batch.running ? "批次正在后台运行" : "没有占用中的批次进程"}</p></div><div class="toolbar"><button class="primary-button" data-go="review">继续审核</button></div></section>
    <section class="metric-grid">
      <article class="metric"><span>待处理</span><strong>${summary.counts["待处理"]}</strong></article>
      <article class="metric processing"><span>处理中</span><strong>${summary.counts["处理中"]}</strong></article>
      <article class="metric success"><span>完成</span><strong>${summary.counts["完成"]}</strong></article>
      <article class="metric failed"><span>失败</span><strong>${summary.counts["失败"]}</strong></article>
    </section>
    <section class="focus-band">
      <div><span class="eyebrow">当前优先事项</span><h2>${escapeHtml(summary.focus.title)}</h2></div>
      <button class="primary-button" data-go="${focusView}">${escapeHtml(summary.focus.action)}</button>
    </section>
    <section class="activity-grid">
      <article class="panel"><div class="panel-head"><h3>最近商品</h3><button class="ghost-button" data-go="inbox">查看全部</button></div><div class="panel-body list-layout">${products.items.slice(0, 4).map(compactProduct).join("") || empty("采集箱为空")}</div></article>
      <article class="panel"><div class="panel-head"><h3>最近操作</h3><button class="ghost-button" data-go="logs">完整日志</button></div><div class="log-list">${logs.items.slice(0, 8).map(logEntry).join("") || empty("暂无日志")}</div></article>
    </section>`;
}

function compactProduct(product) {
  return `<button class="queue-item" data-open-product="${product.product_id}">
    ${thumbnail(product)}
    <span><strong>${escapeHtml(product.title_cn)}</strong><span class="queue-meta"><i class="risk-dot ${product.risk.level}"></i>${product.product_id} · ${product.sku_count} SKU · ${product.state}</span></span>
  </button>`;
}

function reviewQueueItem(product) {
  return `<div class="queue-item review-queue-item ${product.product_id === state.currentProductId ? "active" : ""}">
    <button class="queue-select" data-select-product="${product.product_id}">${thumbnail(product)}<span><strong>${escapeHtml(product.title_cn)}</strong><span class="queue-meta"><i class="risk-dot ${product.risk.level}"></i>${product.product_id} · <span data-queue-progress="${product.product_id}">${product.progress}% · ${escapeHtml(stepLabel(product.current_step))}</span></span></span></button>
    ${productMenu(product.product_id)}
  </div>`;
}

function productMenu(productId) {
  return `<div class="product-menu"><button class="menu-trigger" data-menu-product="${productId}" aria-label="商品操作" title="商品操作">•••</button><div class="menu-popover"><button class="menu-delete" data-delete-product="${productId}"><span class="trash-icon" aria-hidden="true"></span>彻底删除</button></div></div>`;
}

function stepLabel(step) {
  return STEP_LABELS[String(step || "")] || "等待任务状态";
}

function liveProgressText(product) {
  const step = String(product.status?.current_step || "queue");
  const generated = (product.images || []).filter((item) => item.url).length;
  const total = (product.images || []).length;
  if (step === "product_analysis" && product.analysis && Object.keys(product.analysis).length) {
    return "商品分析已生成 · 正在进入类目匹配";
  }
  if (step === "category_match" && product.category && Object.keys(product.category).length) {
    return "类目已生成 · 正在整理属性";
  }
  if (step === "russian_copy" && product.content?.title_ru) {
    return "俄文资料已生成 · 正在进入图片流程";
  }
  if (step === "image_generation" && total) return `${stepLabel(step)} · 已完成 ${generated}/${total} 张`;
  if (step === "image_qc" && total) return `${stepLabel(step)} · 正在逐张检查 ${generated}/${total} 张`;
  return stepLabel(step);
}

function renderLiveProgress(product) {
  const progress = Math.max(0, Math.min(100, Number(product.progress) || 0));
  const error = product.status?.error_message && product.status.error_message !== "unknown" ? product.status.error_message : "后台正在处理，页面会自动更新";
  return `<div class="live-progress" id="live-progress"><div class="live-progress-head"><strong data-progress-step>${escapeHtml(liveProgressText(product))}</strong><span data-progress-value>${progress}%</span></div><div class="progress-track"><span data-progress-bar style="width:${progress}%"></span></div><small data-progress-status>${escapeHtml(error)}</small></div>`;
}

async function renderReview(options = {}) {
  await loadProducts(options.query || "");
  if (options.productId) state.currentProductId = options.productId;
  if (!state.currentProductId) {
    root.innerHTML = empty("没有可审核商品");
    return;
  }
  state.currentProduct = await api(`/api/workbench/products/${state.currentProductId}`);
  await loadWorkbenchSettings();
  state.reviewMode = state.workbenchSettings.auto_mode_enabled ? "auto" : "manual";
  state.reviewDepth = "full";
  state.autoAdvance = state.currentProduct.content.auto_advance !== false;
  const product = state.currentProduct;
  state.selectedStoreIds = new Set(Object.values(product.publications?.stores || {}).filter((item) => item.selected).map((item) => item.store_id));
  const running = ["QUEUED", "PROCESSING", "CATEGORY_MATCHED", "CONTENT_GENERATED", "IMAGES_GENERATED", "PRICED", "UPLOADING"].includes(String(product.status?.status || "").toUpperCase());
  const readyToUpload = String(product.status?.status || "").toUpperCase() === "OZON_READY";
  const canRunProduct = ["run", "fix", "review_upload"].includes(product.primary_action?.key);
  const primaryText = readyToUpload
    ? `确认修改并立即上传（${state.selectedStoreIds.size} 家店铺）`
    : product.primary_action?.key === "run" || product.primary_action?.key === "fix"
      ? `${product.primary_action.label}（${state.selectedStoreIds.size} 家店铺）`
      : product.primary_action?.label || "查看状态";
  root.innerHTML = `<article class="product-preview-page">
    <header class="preview-toolbar">
      <button class="preview-back" data-go="inbox">返回采集箱</button>
      <div class="preview-status">${riskPill(product.risk)} ${statePill(product.public_state)}<span>${escapeHtml(product.product_id)}</span></div>
      <span class="preview-toolbar-spacer"></span>
      ${renderReviewStoreSelector(product)}
      ${running ? `<button class="safe-stop-button" data-batch-action="stop">安全停止</button>` : ""}
      <button class="preview-delete" data-delete-product="${product.product_id}">彻底删除</button>
    </header>
    ${product.pending_question?.question ? `<section class="task-summary"><div><h2>需要你确认一个关键问题</h2><p>${escapeHtml(product.pending_question.question)}</p></div><button class="primary-button" data-primary-action="answer" data-product-id="${product.product_id}">回答问题</button></section>` : ""}
    ${running ? renderLiveProgress(product) : ""}
    <section class="preview-title-block">
      <input class="preview-title-input" data-draft-field="title_ru" value="${escapeHtml(product.content.title_ru || product.source.title_cn)}" aria-label="俄文商品标题">
      <p><span>中文参考</span>${escapeHtml(product.content.title_zh_reference || product.source.title_cn)}</p>
    </section>
    <div class="visual-preference"><label><span>整套图片风格意见（可选）</span><input id="visual-set-hint" maxlength="120" value="${escapeHtml(product.visual_preference?.set_hint || "")}" placeholder="例如：更明亮、更科技感、户外感更强"></label><button class="secondary-button" data-action="save-visual-preference">应用到整套图片</button></div>
    <section class="preview-gallery" id="image-workspace">${renderPreviewGallery(product)}</section>
    <section class="preview-section">
      <h2>商品信息</h2>
      ${renderPreviewProductInfo(product)}
    </section>
    <section class="preview-section">
      <h2>特征</h2>
      ${renderPreviewAttributes(product)}
    </section>
    <section class="preview-section">
      <h2>主题标签 <small>${(product.content.tags || []).length}/30</small></h2>
      <div class="preview-tags">${(product.content.tags || []).map((tag, index) => `<span>${escapeHtml(tag)}<button data-remove-tag="${index}" aria-label="删除标签">×</button></span>`).join("")}</div>
      <div class="tag-add preview-tag-add"><input id="new-tag" maxlength="30" placeholder="输入俄文标签"><button data-action="add-tag">添加</button></div>
    </section>
    <section class="preview-section preview-description-section">
      <h2>简介</h2>
      <div class="bilingual-editor"><label><span>俄文实际上传内容</span><textarea data-draft-field="description_ru">${escapeHtml(product.content.description_ru || "")}</textarea></label><aside><span>中文参考</span><p>${escapeHtml(product.content.description_zh_reference || "暂无中文参考")}</p></aside></div>
    </section>
    <section class="preview-section">
      <h2>SKU与售价</h2>
      ${renderPreviewSkus(product)}
      ${renderPerStorePrices(product)}
    </section>
    <section class="preview-section">
      <h2>店铺发布状态</h2>
      ${renderPublicationMatrix(product)}
    </section>
    <footer class="preview-submit-bar">
      <button class="preview-back" data-go="inbox">返回</button>
      <span id="save-state" class="preview-save-state">${product.draft.saved_at ? `修改已自动保存 v${product.draft.version}` : "当前为AI初稿"}</span>
      <button class="preview-primary" data-action="run-product" ${running || !canRunProduct || !state.selectedStoreIds.size || state.draftSaveFailed ? "disabled" : ""}>${escapeHtml(primaryText)}</button>
    </footer>
  </article>`;
  state.currentImageSlot = state.currentImageSlot && product.images.some((item) => item.slot === state.currentImageSlot) ? state.currentImageSlot : product.images[0]?.slot || null;
  state.lastImageSignature = imageSignature(product);
  startReviewPolling();
}

function renderPreviewGallery(product) {
  return product.images.map((image, index) => {
    const src = image.url
      ? `<img src="${image.url}?v=${Date.now()}" alt="${escapeHtml(image.slot)}" data-open-image="${image.url}">`
      : `<div class="preview-image-empty"><strong>${escapeHtml(image.slot)}</strong><span>${image.state === "WAITING" ? "等待生成" : "正在生成"}</span></div>`;
    return `<article class="preview-image-card ${index === 0 ? "preview-main-image" : ""}" draggable="true" data-image-slot="${escapeHtml(image.slot)}">
      ${src}<span class="preview-image-state">${escapeHtml(image.state)}</span>
      <div class="preview-image-actions"><button data-image-action="keep">保留</button><button data-image-action="regenerate">重做</button><button data-image-action="replace">替换</button><button data-image-action="move-up">前移</button><button class="danger-mini" data-image-action="delete">删除</button></div>
      <input type="file" accept="image/*" data-replace-file hidden>
    </article>`;
  }).join("") || `<div class="preview-gallery-empty">图片生成后会在这里完整预览</div>`;
}

function renderPreviewProductInfo(product) {
  const firstSku = product.skus?.[0] || {};
  const analysis = product.analysis || {};
  const facts = analysis.facts || {};
  const dimensions = facts.dimensions || {};
  const productDimensions = dimensions.product || dimensions;
  const dimensionValues = [
    productDimensions.length_cm ?? productDimensions.length,
    productDimensions.width_cm ?? productDimensions.width,
    productDimensions.height_cm ?? productDimensions.height,
  ];
  const dimensionUnit = productDimensions.unit || "cm";
  const dimensionsText = dimensionValues.some((value) => value !== undefined && value !== null)
    ? `${dimensionValues.map((value) => display(value)).join(" × ")} ${dimensionUnit}`
    : display(dimensions);
  const productWeight = facts.weight?.product?.value_g ?? facts.weight?.value_g ?? facts.weight;
  const weightText = typeof productWeight === "number" ? `${number(productWeight, 0)} 克` : display(productWeight);
  const categoryPath = (product.category.category_path_zh || product.category.category_path || []).join(" › ") || display(product.category.category_name_zh || product.category.category_name);
  return `<div class="preview-field-list">
    <div><span>类目和类型</span><strong>${escapeHtml(categoryPath)}</strong></div>
    <div><span>默认人民币售价</span><strong>${product.skus?.length ? `¥${number(product.skus[0].selling_price_cny, 2)}` : "尚未生成"}</strong></div>
    <div><span>货号</span><strong>${escapeHtml(display(firstSku.offer_id, product.product_id))}</strong></div>
    <div><span>Ozon Product ID</span><strong>${escapeHtml(display(product.ozon?.product_id))}</strong></div>
    <div><span>商品重量</span><strong>${escapeHtml(weightText)}</strong></div>
    <div><span>长 × 宽 × 高</span><strong>${escapeHtml(dimensionsText)}</strong></div>
  </div>`;
}

function renderPreviewAttributes(product) {
  const attrs = [...(product.attributes?.attributes || [])];
  const required = attrs.filter((item) => item.required);
  const optional = attrs.filter((item) => !item.required);
  const optionalFilled = optional.filter((item) => attributeHasValue(item.value));
  const optionalEmpty = optional.filter((item) => !attributeHasValue(item.value));
  return `<div class="preview-attribute-group"><h3>必填属性 <span>${required.length}项</span></h3>${required.map(previewAttributeRow).join("") || `<p class="preview-empty-copy">当前类目没有额外必填属性</p>`}</div>
    <details class="preview-more-attributes"><summary>更多属性（已填写 ${optionalFilled.length} / ${optional.length}）</summary><div>
      ${optionalFilled.map(previewAttributeRow).join("") || `<p class="preview-empty-copy">暂时没有可安全自动填写的可选属性</p>`}
      ${optionalEmpty.length ? `<details class="preview-empty-attributes"><summary>未提供或不适用（${optionalEmpty.length}项，可手动补充）</summary><div>${optionalEmpty.map(previewAttributeRow).join("")}</div></details>` : ""}
    </div></details>`;
}

function attributeHasValue(value) {
  if (value === null || value === undefined) return false;
  if (typeof value === "string") return value.trim() !== "" && value.trim().toLowerCase() !== "unknown";
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value).length > 0;
  return true;
}

function attributeDisplayValue(value) {
  if (!attributeHasValue(value)) return "";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function attributeSourceLabel(item) {
  const source = String(item.source || "");
  const labels = {"1688":"1688资料", "AI_estimated":"AI估算", "workspace_default":"系统默认", "human_override":"人工修改", "人工修改":"人工修改"};
  const label = labels[source] || "已填写";
  const confidence = Number(item.confidence);
  return source === "AI_estimated" && confidence > 0 ? `${label} ${Math.round(confidence * 100)}%` : label;
}

function previewAttributeRow(item) {
  const value = attributeDisplayValue(item.value);
  const allowed = item.allowed_values || [];
  const control = allowed.length
    ? `<select data-attribute-id="${item.attribute_id}"><option value="">请选择</option>${allowed.map((entry) => `<option value="${escapeHtml(entry.value)}" ${String(entry.value) === String(value) ? "selected" : ""}>${escapeHtml(entry.value)}</option>`).join("")}</select>`
    : `<input data-attribute-id="${item.attribute_id}" value="${escapeHtml(value)}" placeholder="尚未填写">`;
  return `<label class="preview-attribute-row"><span><strong>${escapeHtml(item.attribute_name_zh || item.attribute_name || item.attribute_id)}${item.required ? " *" : ""}</strong><small>${escapeHtml(item.attribute_name || "Ozon字段")}</small></span>${control}<em>${value ? escapeHtml(attributeSourceLabel(item)) : item.required ? "必须填写" : "未提供"}</em></label>`;
}

function renderPreviewSkus(product) {
  return `<div class="preview-sku-table"><div class="preview-sku-head"><span>SKU</span><span>规格</span><span>采购价</span><span>人民币售价（可修改）</span><span>约合卢布</span></div>${(product.skus || []).map((sku) => `<div class="preview-sku-row"><strong>${escapeHtml(display(sku.sku_id))}</strong><span>${escapeHtml(display(sku.option_text || sku.name))}</span><span>¥${number(sku.purchase_price_cny, 2)}</span><label>¥<input type="number" min="0.01" step="0.01" data-sku-price="${escapeHtml(sku.sku_id)}" value="${typeof sku.selling_price_cny === "number" ? sku.selling_price_cny : ""}"></label><span>₽${number(sku.selling_price_rub, 0)}</span></div>`).join("")}</div>`;
}

function renderPerStorePrices(product) {
  const selected = (product.stores || []).filter((shop) => state.selectedStoreIds.has(shop.id));
  if (!selected.length) return "";
  const rate = Number(product.workbench_settings?.fixed_cny_to_rub || state.workbenchSettings.fixed_cny_to_rub || 12);
  return `<details class="preview-store-prices"><summary>按店铺修改售价（可选）</summary><p>默认所有店铺使用上面的统一售价；这里只填写有差异的店铺。</p>${selected.map((shop) => {
    const publication = product.publications?.stores?.[shop.id] || {};
    const bySku = Object.fromEntries((publication.sku_publications || []).map((item) => [String(item.sku_id), item]));
    return `<div class="preview-store-price-group"><h3>${escapeHtml(shop.display_name)}</h3>${(product.skus || []).map((sku) => {
      const saved = bySku[String(sku.sku_id)]?.price_override_cny;
      return `<label><span>${escapeHtml(display(sku.option_text || sku.sku_id))}</span><span>¥ <input type="number" min="0.01" step="0.01" data-store-price-cny="${escapeHtml(shop.id)}" data-store-price-sku="${escapeHtml(sku.sku_id)}" value="${typeof saved === "number" ? saved : ""}" placeholder="统一价 ${number(sku.selling_price_cny, 2)}"><small>${typeof saved === "number" ? `约 ₽${number(saved * rate, 0)}` : "留空使用统一价"}</small></span></label>`;
    }).join("")}</div>`;
  }).join("")}</details>`;
}

function collectStoreOverrides() {
  const rate = Number(state.currentProduct?.workbench_settings?.fixed_cny_to_rub || state.workbenchSettings.fixed_cny_to_rub || 12);
  const rounding = Number(state.currentProduct?.workbench_settings?.rub_rounding || state.workbenchSettings.rub_rounding || 10);
  const overrides = {};
  root.querySelectorAll("[data-store-price-cny]").forEach((input) => {
    const cny = Number(input.value);
    if (!(cny > 0)) return;
    const storeId = input.dataset.storePriceCny;
    const skuId = input.dataset.storePriceSku;
    const rub = Math.round((cny * rate) / rounding) * rounding;
    overrides[storeId] ||= {sku_prices:{}, sku_prices_cny:{}};
    overrides[storeId].sku_prices[skuId] = rub;
    overrides[storeId].sku_prices_cny[skuId] = cny;
  });
  return overrides;
}

function renderReviewStoreSelector(product) {
  const stores = product.stores || [];
  const options = stores.map((shop) => {
    const available = shop.enabled && shop.connection_status === "connected";
    return `<label class="store-option ${available ? "" : "unavailable"}"><input type="checkbox" data-product-store="${escapeHtml(shop.id)}" ${state.selectedStoreIds.has(shop.id) ? "checked" : ""} ${available ? "" : "disabled"}><span><strong>${escapeHtml(shop.display_name)}</strong><small>${storeStatusLabel(shop.connection_status)} · ${shop.credentials_display}</small></span>${available ? "" : `<span class="status-pill medium">不可上传</span>`}</label>`;
  }).join("");
  return `<details class="store-selector"><summary>上传至 ${state.selectedStoreIds.size} 家店铺</summary><div class="store-selector-popover"><div class="store-options">${options || `<p class="form-help">请先到店铺中心添加并验证店铺</p>`}</div><div class="store-card-actions"><button class="secondary-button" data-action="select-all-stores" type="button">全选可用</button><button class="primary-button" data-action="save-product-stores" type="button" ${state.selectedStoreIds.size ? "" : "disabled"}>保存选择</button></div></div></details>`;
}

function storeStatusLabel(value) {
  return ({connected:"已连接", unverified:"未验证", failed:"连接失败", disabled:"已禁用"})[value] || "未验证";
}

function renderImageWorkspace(product) {
  const passed = product.images.filter((item) => ["PASS", "COMPLETED"].includes(item.state)).length;
  return `<div class="image-header"><div><h2>${escapeHtml(product.source.title_cn)}</h2><span>${product.images.length} 张规划图 · ${passed} 张可用 · 3:4</span></div><span>${product.image_qc?.score ? `质检 ${product.image_qc.score} 分` : "等待质检"}</span></div>
    <div class="image-grid">${product.images.map(imageTile).join("") || empty("尚未生成图片")}</div>`;
}

function imageTile(image) {
  const stateClass = image.state.toLowerCase();
  const issue = image.issues?.[0] || image.purpose || "等待生成结果";
  const src = image.url ? `<img src="${image.url}?v=${Date.now()}" alt="${escapeHtml(image.slot)}" data-open-image="${image.url}">` : `<div class="image-placeholder"><strong>${escapeHtml(image.slot)}</strong><span>${image.state === "WAITING" ? "等待生成" : "正在生成"}</span></div>`;
  return `<article class="image-tile ${state.currentImageSlot === image.slot ? "selected" : ""}" data-image-slot="${escapeHtml(image.slot)}" ${image.product_id ? `data-product-id="${escapeHtml(image.product_id)}"` : ""}>
    <div class="image-frame ${image.state === "GENERATING" || image.state === "RETRYING" ? "generating" : image.state === "WAITING" ? "waiting" : ""}">${src}<span class="image-badge ${stateClass}">${escapeHtml(image.state)}</span></div>
    <div class="image-info"><h3>${escapeHtml(image.slot)} · ${escapeHtml(image.type)}</h3><p>${escapeHtml(issue)}</p>
      <div class="image-actions"><button data-image-action="prompt">单图意见</button><button data-image-action="regenerate">重生成</button>${image.download_url ? `<a href="${image.download_url}">下载</a>` : ""}</div>
      <div class="image-actions-more">${image.url ? `<button data-image-action="keep">保留</button><button data-image-action="copy-url">复制URL</button><button data-image-action="set-main">设为主图</button><button data-image-action="set-detail">设为详情图</button><button data-image-action="move-up">前移</button><button data-image-action="replace">替换</button><button class="danger-mini" data-image-action="delete">删除</button>` : ""}</div>
      <input type="file" accept="image/*" data-replace-file hidden>
      <div class="prompt-editor"><textarea data-prompt-input placeholder="例如：背景更明亮、产品再大一点">${escapeHtml(image.prompt)}</textarea><div class="image-actions"><button data-image-action="queue-prompt">应用意见并重生成</button></div></div>
    </div>
  </article>`;
}

function bulletText(value) {
  if (typeof value === "string") return value;
  return value?.text_ru || "";
}

function renderDataWorkspace(product) {
  const content = product.content;
  const analysis = product.analysis || {};
  const attrs = product.attributes?.attributes || [];
  const attrSummary = product.attributes?.summary || {};
  const pricing = product.pricing?.sku_pricing || [];
  const locked = new Set(product.draft.locked_fields || []);
  const tags = content.tags || [];
  const ozon = product.ozon || {};
  const fullClass = state.reviewDepth === "full" ? "" : "review-hidden";
  const score = product.prelisting_assessment || {};
  const canChangeCategory = ["COLLECTED", "FAILED_HARD_BLOCKER"].includes(product.status?.status) && Number(product.status?.api_write_count || 0) === 0;
  return `<div class="data-sticky"><strong>Ozon商品资料</strong><span class="save-state" id="save-state">${product.draft.saved_at ? `已保存 v${product.draft.version}` : "AI原始版本"}</span></div>
    ${renderAnalysisSummary(analysis)}
    <details class="data-section" open><summary>俄文资料 ${locked.has("title_ru") || locked.has("description_ru") ? `<span class="locked">人工锁定</span>` : ""}</summary><div class="data-content">
      ${editField("俄文标题", "title_ru", content.title_ru, locked)}
      ${editField("短标题", "short_title", content.short_title, locked)}
      ${editField("详细描述", "description_ru", content.description_ru, locked, true)}
      ${editField("核心卖点", "bullets_ru", (content.bullets_ru || []).map(bulletText).join("\n"), locked, true)}
    </div></details>
    <details class="data-section full-review-only ${fullClass}" open><summary>主题标签 <span class="tag-count ${tags.length !== 30 ? "bad" : ""}">${tags.length}/30</span></summary><div class="data-content"><div class="tag-editor" id="tag-editor">${tags.map((tag, index) => `<span class="tag-chip"><span>${escapeHtml(tag)}</span><button data-remove-tag="${index}" aria-label="删除标签">×</button></span>`).join("")}</div><div class="tag-add"><input id="new-tag" maxlength="30" placeholder="#俄文标签"><button class="secondary-button" data-action="add-tag">添加</button></div></div></details>
    <details class="data-section full-review-only ${fullClass}" ${product.risk.items.some((item) => item.code === "required_attributes") ? "open" : ""}><summary>类目与属性 <span>${attrSummary.mapped_count || 0}/${attrSummary.required_count || 0}</span></summary><div class="data-content">
      <div class="summary-row"><span>类目</span><strong>${escapeHtml(display(product.category.category_name_zh || product.category.category_name))}</strong></div>
      <div class="summary-row"><span>category_id / type_id</span><strong>${display(product.category.category_id)} / ${display(product.category.type_id)}</strong></div>
      <div class="summary-row"><span>类目路径</span><strong>${escapeHtml((product.category.category_path_zh || product.category.category_path || []).join(" / ") || "未确认")}</strong></div>
      <div class="summary-row"><span>置信度</span><strong>${typeof product.category.confidence === "number" ? `${Math.round(product.category.confidence * 100)}%` : "未确认"}</strong></div>
      <div class="category-change-row"><button class="secondary-button" data-action="change-category" ${canChangeCategory ? "" : "disabled"}>修改最终类目</button><small>${canChangeCategory ? "修改后旧属性、图片策略和上传数据会失效，需重新运行" : "已进入批次、远端处理中或已有Ozon写入，不能直接改类目"}</small></div>
      <div class="table-wrap"><table class="attribute-table"><thead><tr><th>字段</th><th>值</th><th>来源</th><th>状态</th></tr></thead><tbody>${attrs.map(attributeRow).join("")}</tbody></table></div>
    </div></details>
    <details class="data-section full-review-only ${fullClass}" ${skuHasDifferences(product.skus) ? "open" : ""}><summary>SKU与变体 <span>${product.skus.length} 个${skuHasDifferences(product.skus) ? " · 存在差异" : ""}</span></summary><div class="data-content"><div class="table-wrap"><table class="sku-table"><thead><tr><th>SKU</th><th>规格</th><th>采购价</th><th>售价/利润</th><th>变体依据</th></tr></thead><tbody>${product.skus.map(skuRow).join("")}</tbody></table></div></div></details>
    <details class="data-section" open><summary>初始定价 <span>${pricing.length} 个SKU</span></summary><div class="data-content">${pricing.map(pricingRows).join("") || `<div class="summary-row"><span>定价</span><strong>尚未生成</strong></div>`}</div></details>
    <details class="data-section full-review-only ${fullClass}"><summary>Rich Content</summary><div class="data-content"><pre>${escapeHtml(JSON.stringify(product.rich_content || {}, null, 2))}</pre></div></details>
    <details class="data-section" open><summary>上架前评分与价格建议 <span>${score.overall_score || 0}分</span></summary><div class="data-content">${renderPrelistingScore(score)}</div></details>
    <details class="data-section full-review-only ${fullClass}" open><summary>店铺发布状态 <span>${product.publication_summary?.selected || 0} 家</span></summary><div class="data-content">${renderPublicationMatrix(product)}</div></details>
    <details class="data-section"><summary>AI建议 <span>${product.ai_suggestions?.filter((item) => item.status === "pending").length || 0}</span></summary><div class="data-content">${renderSuggestions(product.ai_suggestions || [])}</div></details>
    <details class="data-section" open><summary>风险与时间线 <span>${product.risk.items.length}</span></summary><div class="data-content">${product.risk.items.map((item) => `<div class="summary-row"><span>${item.level === "high" ? "高风险" : "中风险"}</span><strong>${escapeHtml(item.message)}</strong></div>`).join("") || `<div class="summary-row"><span>风险</span><strong>未发现阻断风险</strong></div>`}${product.timeline.slice(0, 8).map((item) => `<div class="summary-row"><span>${dateText(item.at)}</span><strong>${escapeHtml(item.message)}</strong></div>`).join("")}</div></details>`;
}

function renderAnalysisSummary(analysis) {
  if (!analysis || !Object.keys(analysis).length) {
    return `<details class="data-section" open><summary>商品分析</summary><div class="data-content"><div class="summary-row"><span>状态</span><strong>尚未生成</strong></div></div></details>`;
  }
  const facts = analysis.facts || {};
  const points = (analysis.selling_points || []).map((item) => typeof item === "string" ? item : item?.text).filter(Boolean);
  const unknowns = (analysis.unknowns || []).map((item) => typeof item === "string" ? item : item?.field).filter(Boolean);
  const risks = (analysis.risks || []).map((item) => typeof item === "string" ? item : item?.message).filter(Boolean);
  const dimensions = facts.dimensions || {};
  const dimensionsText = [dimensions.length_cm, dimensions.width_cm, dimensions.height_cm].map((value) => value ?? "unknown").join(" × ");
  return `<details class="data-section" open><summary>商品分析 <span class="status-pill low">已生成</span></summary><div class="data-content">
    <div class="summary-row"><span>商品类型</span><strong>${escapeHtml(display(analysis.product_type))}</strong></div>
    <div class="summary-row"><span>分析类目</span><strong>${escapeHtml(display(analysis.category))}</strong></div>
    <div class="summary-row"><span>品牌</span><strong>${escapeHtml(display(facts.brand))}</strong></div>
    <div class="summary-row"><span>商品尺寸</span><strong>${escapeHtml(dimensionsText)}</strong></div>
    <div class="summary-row"><span>已确认卖点</span><strong>${points.length ? escapeHtml(points.join("；")) : "unknown"}</strong></div>
    <div class="summary-row"><span>未知字段</span><strong>${unknowns.length ? escapeHtml(unknowns.join("、")) : "无"}</strong></div>
    ${risks.length ? `<div class="analysis-risk"><span>风险提示</span>${risks.map((risk) => `<p>${escapeHtml(risk)}</p>`).join("")}</div>` : ""}
  </div></details>`;
}

function skuHasDifferences(skus) {
  const signature = (key) => new Set(skus.map((item) => String(item[key] ?? "unknown"))).size > 1;
  return ["purchase_price_cny", "selling_price_rub", "profit_cny", "option_text"].some(signature);
}

function renderPrelistingScore(score) {
  const price = score.pricing_advice || {};
  return `<div class="score-grid"><div class="score-cell"><span>利润潜力</span><strong>${score.profit_potential || 0}</strong></div><div class="score-cell"><span>俄罗斯适配</span><strong>${score.russia_fit || 0}</strong></div><div class="score-cell"><span>图片潜力</span><strong>${score.image_sales_potential || 0}</strong></div><div class="score-cell"><span>竞争风险</span><strong>${score.competition_risk || 0}</strong></div><div class="score-cell"><span>退货风险</span><strong>${score.return_risk || 0}</strong></div><div class="score-cell"><span>建议</span><strong>${escapeHtml(score.advice || "待评估")}</strong></div></div><div class="summary-row"><span>最低保本价</span><strong>₽${number(price.break_even_price_rub, 0)}</strong></div><div class="summary-row"><span>规则售价</span><strong>₽${number(price.rule_price_rub, 0)}</strong></div><div class="summary-row"><span>建议区间</span><strong>${price.suggested_range_rub?.length ? `₽${number(price.suggested_range_rub[0])}–₽${number(price.suggested_range_rub[1])}` : "未生成"}</strong></div>`;
}

function renderPublicationMatrix(product) {
  return `<div class="publication-matrix">${(product.stores || []).map((shop) => { const publication = product.publications?.stores?.[shop.id] || {}; const sku = publication.sku_publications?.[0] || {}; const failed = publication.status === "FAILED"; const failureReason = publication.last_error && !["unknown", "UNKNOWN"].includes(publication.last_error) ? publication.last_error : "失败原因未记录，请先查看本地日志后再重试"; return `<article class="publication-row"><div class="publication-row-head"><strong>${escapeHtml(shop.display_name)}</strong><span class="status-pill ${failed ? "failed" : publication.status === "NOT_SELECTED" ? "" : "processing"}">${escapeHtml(display(publication.status, "未选择"))}</span></div><p>${escapeHtml(sku.action || "UNKNOWN")} · task ${escapeHtml(display(sku.task_id))} · product ${escapeHtml(display(sku.ozon_product_id))}</p>${failed ? `<p class="publication-error"><strong>失败原因：</strong>${escapeHtml(failureReason)}</p>` : ""}${publication.has_store_overrides ? `<p class="locked">该店铺存在专属修改</p>` : ""}${failed ? `<button class="secondary-button retry-store-button" data-retry-store="${escapeHtml(shop.id)}">只重试这家店</button>` : ""}</article>`; }).join("") || `<p class="form-help">尚未配置店铺</p>`}</div>`;
}

function renderSuggestions(items) {
  return items.filter((item) => item.status === "pending").map((item) => `<article class="suggestion-card"><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.detail)}</p><div class="suggestion-actions"><button class="primary-button" data-suggestion="${escapeHtml(item.id)}" data-suggestion-action="accept">采纳</button><button class="secondary-button" data-suggestion="${escapeHtml(item.id)}" data-suggestion-action="ignore">忽略</button><button class="ghost-button" data-suggestion="${escapeHtml(item.id)}" data-suggestion-action="mute_similar">以后不提醒</button></div></article>`).join("") || `<p class="form-help">当前没有待处理建议</p>`;
}

function editField(label, name, value, locked, textarea = false) {
  const input = textarea ? `<textarea data-draft-field="${name}">${escapeHtml(value)}</textarea>` : `<input data-draft-field="${name}" value="${escapeHtml(value)}">`;
  return `<label class="field"><span>${label}${locked.has(name) ? `<b class="locked">人工锁定</b>` : ""}</span>${input}</label>`;
}

function attributeRow(item) {
  const status = item.value === "unknown" ? (item.required ? "缺失" : "未知") : "已填写";
  return `<tr><td>${escapeHtml(item.attribute_name || item.attribute_id)}${item.required ? " *" : ""}</td><td><input data-attribute-id="${item.attribute_id}" value="${escapeHtml(item.value === "unknown" ? "" : item.value)}" placeholder="unknown"></td><td>${escapeHtml(display(item.source, "未知"))}</td><td>${status}</td></tr>`;
}

function skuRow(sku) {
  return `<tr><td>${escapeHtml(display(sku.sku_id))}</td><td>${escapeHtml(display(sku.option_text || sku.name))}</td><td>¥${number(sku.purchase_price_cny, 2)}<br>${number(sku.weight_g, 0)}g</td><td>₽${number(sku.selling_price_rub, 0)}<br>¥${number(sku.profit_cny, 2)}</td><td>${escapeHtml(display(sku.variant_decision))}<br><small>${escapeHtml(display(sku.aspect_basis))}</small></td></tr>`;
}

function pricingRows(item) {
  const shipping = item.shipping || {};
  return `<div class="summary-row"><span>SKU</span><strong>${escapeHtml(item.sku_id)}</strong></div><div class="summary-row"><span>采购价 / 运费</span><strong>¥${number(item.purchase_cost_cny, 2)} / ¥${number(shipping.shipping_cost_cny, 2)}</strong></div><div class="summary-row"><span>总成本</span><strong>¥${number(item.base_cost_cny, 2)}</strong></div><div class="summary-row"><span>建议售价</span><strong class="locked">₽${number(item.selling_price_rub, 0)}</strong></div><div class="summary-row"><span>预计利润</span><strong>¥${number(item.estimated_profit_cny, 2)}</strong></div>`;
}

function collectDraftField(field, rawValue) {
  if (field === "bullets_ru") return rawValue.split("\n").map((value) => value.trim()).filter(Boolean);
  return rawValue;
}

function scheduleDraftSave(patch) {
  const saveState = document.getElementById("save-state");
  if (saveState) { saveState.textContent = "正在保存"; saveState.classList.remove("error"); }
  for (const [field, value] of Object.entries(patch)) {
    if (["attributes", "sku_overrides", "image_prompts"].includes(field) && value && typeof value === "object") {
      state.pendingDraftPatch[field] = {...(state.pendingDraftPatch[field] || {}), ...value};
    } else {
      state.pendingDraftPatch[field] = value;
    }
  }
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(() => {
    const pending = state.pendingDraftPatch;
    state.pendingDraftPatch = {};
    saveDraft(pending);
  }, 650);
}

async function saveDraft(patch) {
  try {
    const result = await api(`/api/workbench/products/${state.currentProductId}/draft`, {method: "PATCH", body: JSON.stringify(patch)});
    const saveState = document.getElementById("save-state");
    if (saveState) saveState.textContent = `已保存 v${result.version}`;
    state.draftSaveFailed = false;
  } catch (error) {
    for (const [field, value] of Object.entries(patch)) {
      if (["attributes", "sku_overrides", "image_prompts"].includes(field) && value && typeof value === "object") {
        state.pendingDraftPatch[field] = {...value, ...(state.pendingDraftPatch[field] || {})};
      } else if (!(field in state.pendingDraftPatch)) {
        state.pendingDraftPatch[field] = value;
      }
    }
    state.draftSaveFailed = true;
    const saveState = document.getElementById("save-state");
    if (saveState) { saveState.textContent = "保存失败"; saveState.classList.add("error"); }
    toast(`草稿保存失败：${error.message}`, "error");
  }
}

function imageSignature(product) {
  return JSON.stringify({
    status: product.status.status,
    current_step: product.status.current_step,
    progress: product.progress,
    outputs: {
      analysis: Boolean(product.analysis && Object.keys(product.analysis).length),
      category: Boolean(product.category && Object.keys(product.category).length),
      content: [product.content?.title_ru, product.content?.description_ru],
      pricing: Boolean(product.pricing && Object.keys(product.pricing).length),
      plan: Boolean(product.images?.length),
    },
    images: product.images.map((item) => [item.slot, item.state, item.url, item.issues]),
  });
}

function updateLiveProgress(product) {
  const progress = Math.max(0, Math.min(100, Number(product.progress) || 0));
  const step = document.querySelector("[data-progress-step]");
  const value = document.querySelector("[data-progress-value]");
  const bar = document.querySelector("[data-progress-bar]");
  const status = document.querySelector("[data-progress-status]");
  if (step) step.textContent = liveProgressText(product);
  if (value) value.textContent = `${progress}%`;
  if (bar) bar.style.width = `${progress}%`;
  if (status) status.textContent = product.status?.error_message && product.status.error_message !== "unknown" ? product.status.error_message : "后台正在处理，页面会自动更新";
  const queueProgress = document.querySelector(`[data-queue-progress="${product.product_id}"]`);
  if (queueProgress) queueProgress.textContent = `${progress}% · ${stepLabel(product.status?.current_step)}`;
}

function startReviewPolling() {
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    if (state.view !== "review" || !state.currentProductId) return;
    if (document.activeElement?.matches("input,textarea")) return;
    try {
      const product = await api(`/api/workbench/products/${state.currentProductId}`);
      maybeNotifyListingSuccess(product);
      const signature = imageSignature(product);
      if (signature !== state.lastImageSignature) {
        state.currentProduct = product;
        state.lastImageSignature = signature;
        updateLiveProgress(product);
        const imageWorkspace = document.getElementById("image-workspace");
        if (imageWorkspace) imageWorkspace.innerHTML = document.querySelector(".product-preview-page") ? renderPreviewGallery(product) : renderImageWorkspace(product);
        if (!document.activeElement?.matches("input,textarea")) {
          const dataWorkspace = document.getElementById("data-workspace");
          if (dataWorkspace) dataWorkspace.innerHTML = renderDataWorkspace(product);
        }
      }
    } catch (_) {}
  }, 3000);
}

async function changeProduct(direction) {
  const index = state.products.findIndex((item) => item.product_id === state.currentProductId);
  if (index < 0) return;
  const next = direction === "prev" ? Math.max(0, index - 1) : Math.min(state.products.length - 1, index + 1);
  if (next !== index) {
    state.currentProductId = state.products[next].product_id;
    state.currentImageSlot = null;
    await renderReview();
  }
}

async function queueImageRegeneration(slot, prompt = "") {
  const result = await api(`/api/workbench/products/${state.currentProductId}/images/${encodeURIComponent(slot)}/regenerate`, {method: "POST", body: JSON.stringify({prompt})});
  toast(result.message);
  state.currentProduct = await api(`/api/workbench/products/${state.currentProductId}`);
  document.getElementById("image-workspace").innerHTML = renderImageWorkspace(state.currentProduct);
}

async function renderInbox() {
  const data = await loadProducts(searchInput.value);
  state.selectedBatchProducts = new Set([...state.selectedBatchProducts].filter((id) => state.products.some((item) => item.product_id === id)));
  await loadWorkbenchSettings();
  const runnable = state.products.filter((item) => ["run", "fix"].includes(item.primary_action?.key));
  root.innerHTML = `<section class="task-summary"><div><h2>${data.total} 个我的商品</h2><p>采集后从这里直接继续下一步；每个商品最多10个SKU · 当前${state.workbenchSettings.auto_mode_enabled ? "自动模式" : "手动检查模式"}</p></div><button class="primary-button task-primary" data-action="open-batch" ${runnable.length ? "" : "disabled"}>运行可处理商品</button></section><div class="bulk-toolbar"><label><input type="checkbox" data-select-all-products> 多选商品</label><span>已选 ${state.selectedBatchProducts.size} 个</span><span class="spacer"></span><button class="primary-button" data-action="open-batch" ${state.selectedBatchProducts.size ? "" : "disabled"}>运行所选</button></div><div class="list-layout inbox-list">${state.products.map((product) => inboxCard(product, true)).join("") || empty("采集箱为空。请先在1688选择SKU和最终Ozon类目，再完成采集。")}</div>`;
}

async function renderAttention() {
  await loadProducts(searchInput.value);
  await pollNotifications({showDesktop:false});
  const items = state.products.filter((item) => item.attention_required);
  root.innerHTML = `<section class="task-summary"><div><h2>${items.length ? `${items.length} 个商品需要你处理` : "现在没有需要处理的商品"}</h2><p>这里只显示等待回答、明确失败或已经生成完成等待上传的商品。</p></div><button class="secondary-button" data-action="refresh-ozon">刷新状态</button></section><div class="list-layout inbox-list">${items.map((product) => inboxCard(product, false)).join("") || empty("你当前没有待处理事项")}</div>`;
}

async function renderListed() {
  await loadProducts(searchInput.value);
  const items = state.products.filter((item) => ["UPLOADED", "ACTIVE"].includes(String(item.raw_status || "").toUpperCase()));
  root.innerHTML = `<section class="task-summary"><div><h2>${items.length} 个已上架商品</h2><p>只显示你采集并已经通过Ozon审核的商品。</p></div><button class="secondary-button" data-action="refresh-ozon">刷新Ozon状态</button></section><div class="list-layout inbox-list">${items.map((product) => inboxCard(product, false)).join("") || empty("还没有已上架商品")}</div>`;
}

function inboxCard(product, selectable = false) {
  const action = product.primary_action || {key:"status", label:"查看进度"};
  const question = product.pending_question?.question ? `<p class="card-alert">需要确认：${escapeHtml(product.pending_question.question)}</p>` : "";
  return `<article class="list-card task-card ${product.attention_required ? "task-card-attention" : ""}">${selectable ? `<input class="batch-select" type="checkbox" data-select-batch-product="${product.product_id}" ${state.selectedBatchProducts.has(product.product_id) ? "checked" : ""}>` : `<span></span>`}${thumbnail(product)}<div><h3>${escapeHtml(product.title_cn)}</h3><p>${product.product_id} · ${product.sku_count} SKU · ${dateText(product.captured_at)}</p><p><span class="workflow-bucket">${escapeHtml(product.workflow_bucket)}</span> · ${escapeHtml(stepLabel(product.current_step))} · ${product.progress}%</p>${question}</div><div class="list-actions"><button class="primary-button" data-primary-action="${escapeHtml(action.key)}" data-product-id="${product.product_id}">${escapeHtml(action.label)}</button><a class="source-link" href="${escapeHtml(product.source_url)}" target="_blank" rel="noreferrer">查看1688来源</a></div>${productMenu(product.product_id)}</article>`;
}

function confidenceMeta(value) {
  const numberValue = Number(value) || 0;
  if (numberValue >= 80) return {label:"高", className:"high"};
  if (numberValue >= 55) return {label:"中", className:"medium"};
  return {label:"低", className:"low"};
}

function currentConfirmationProduct() {
  const items = state.confirmationData?.products || [];
  return items.find((item) => item.product_id === state.confirmationProductId) || items[0] || null;
}

function confirmationProductListItem(product) {
  const active = product.product_id === state.confirmationProductId;
  return `<button class="confirmation-product-item ${active ? "active" : ""}" data-confirm-product="${escapeHtml(product.product_id)}">
    <span class="confirmation-product-thumb">${product.thumbnail_url ? `<img src="${escapeHtml(product.thumbnail_url)}" alt="">` : `<span>无图</span>`}</span>
    <span><strong>${escapeHtml(product.title_cn)}</strong><small>${product.sku_count} 个SKU · 待确认 ${product.uncertain_count} 项</small></span>
  </button>`;
}

function confirmationDimensionInputs(product, fieldKey, value) {
  return `<span class="confirmation-dimensions">
    <label><small>长</small><input type="number" min="0.01" step="0.01" data-confirm-field="${fieldKey}.length" data-confirm-product-id="${product.product_id}" value="${escapeHtml(value.length)}"></label>
    <i>×</i><label><small>宽</small><input type="number" min="0.01" step="0.01" data-confirm-field="${fieldKey}.width" data-confirm-product-id="${product.product_id}" value="${escapeHtml(value.width)}"></label>
    <i>×</i><label><small>高</small><input type="number" min="0.01" step="0.01" data-confirm-field="${fieldKey}.height" data-confirm-product-id="${product.product_id}" value="${escapeHtml(value.height)}"></label><em>cm</em>
  </span>`;
}

function confirmationFieldRow(product, fieldKey, label) {
  const field = product.fields[fieldKey] || {};
  const confidence = confidenceMeta(field.confidence);
  let control = "";
  if (fieldKey.endsWith("dimensions")) control = confirmationDimensionInputs(product, fieldKey, field.value || {});
  else if (fieldKey.includes("weight")) control = `<label class="confirmation-number-input"><input type="number" min="0.01" step="0.01" data-confirm-field="${fieldKey}" data-confirm-product-id="${product.product_id}" value="${escapeHtml(field.value)}"><span>g</span></label>`;
  else control = `<input class="confirmation-text-input" data-confirm-field="${fieldKey}" data-confirm-product-id="${product.product_id}" value="${field.value === "unknown" ? "" : escapeHtml(field.value)}" placeholder="没有可靠依据，请填写或保持未知">`;
  return `<div class="confirmation-field-row">
    <span class="confirmation-field-name"><strong>${escapeHtml(label)}</strong><small>${field.estimated ? "AI估算，确认后保存" : field.needs_input ? "没有可靠依据" : "来自采集资料"}</small></span>
    ${control}
    <span class="confidence-badge ${confidence.className}">${confidence.label}</span>
    <span class="confirmation-source">${escapeHtml(field.source || "本地规则")}</span>
    <span class="confirmation-adopted" title="默认采用AI建议">已采用</span>
  </div>`;
}

function confirmationEvidence(product) {
  const skuImages = product.sku_images?.length ? product.sku_images : product.main_images || [];
  const referenceImages = product.reference_images || [];
  const active = state.confirmationEvidenceTab === "reference" ? referenceImages : skuImages;
  const fallback = active.length ? active : (state.confirmationEvidenceTab === "reference" ? skuImages : referenceImages);
  return `<div class="confirmation-tabs"><button class="${state.confirmationEvidenceTab === "sku" ? "active" : ""}" data-confirm-evidence-tab="sku">当前SKU原图</button><button class="${state.confirmationEvidenceTab === "reference" ? "active" : ""}" data-confirm-evidence-tab="reference">尺寸图 / 1688详情图</button></div>
    <div class="confirmation-evidence-grid">${fallback.slice(0, 4).map((image, index) => `<button data-open-image="${escapeHtml(image.url)}"><img src="${escapeHtml(image.url)}" alt="${escapeHtml(image.label || "商品参考图")}"><span>${escapeHtml(image.label || (index ? "1688详情图" : "当前SKU"))}</span></button>`).join("") || `<div class="confirmation-evidence-empty">当前商品没有可用的本地参考图</div>`}</div>
    <div class="confirmation-reasons"><h3>为什么这样建议</h3><p>尺寸和重量优先使用1688文字、SKU规格与尺寸图；缺失时才使用本地同类规则估算。</p><p>包装数据始终严格大于商品本体数据，运费只使用包装数据。</p><p>材质没有可靠依据时不会自动虚构。</p></div>`;
}

function renderConfirmationCenter(product) {
  const category = (product.category_path_zh || []).join(" › ") || "未显示类目路径";
  const skuChips = (product.skus || []).map((sku) => `<span>${escapeHtml(sku.option_text || sku.name)}</span>`).join("");
  return `<div class="confirmation-current-head"><div><span>当前商品</span><h2>${escapeHtml(product.title_cn)}</h2><p>Ozon类目：${escapeHtml(category)}</p></div><div class="confidence-legend">AI置信度 <span class="high">高</span><span class="medium">中</span><span class="low">低</span></div></div>
    <section class="confirmation-section"><h3>SKU与可变特征 <small>已按采集时选择锁定</small></h3><div class="confirmation-sku-chips">${skuChips || "没有SKU规格文字"}</div><p class="confirmation-section-note">${product.aspect_attribute_count} 个变体属性来自已选类目的 is_aspect 规则，运行时不会重新猜类目。</p></section>
    <section class="confirmation-section"><h3>尺寸、重量和材质 <small>只确认影响定价、图片和上传的字段</small></h3>
      <div class="confirmation-field-table"><div class="confirmation-field-head"><span>字段</span><span>AI建议值</span><span>置信度</span><span>依据来源</span><span>采用</span></div>
        ${confirmationFieldRow(product, "product_dimensions", "商品尺寸（长 × 宽 × 高）")}
        ${confirmationFieldRow(product, "product_weight_g", "商品净重")}
        ${confirmationFieldRow(product, "package_dimensions", "包装尺寸（长 × 宽 × 高）")}
        ${confirmationFieldRow(product, "package_weight_g", "包装重量")}
        ${confirmationFieldRow(product, "material", "材质")}
      </div>
      <div class="confirmation-omitted">认证、承重、特殊安全功能：无可靠证据时默认不填写；只有Ozon类目强制要求时才转入人工异常。</div>
    </section>
    <section class="confirmation-section confirmation-price-section"><h3>人民币进价 <small>默认使用采集价格，可以修改</small></h3><div class="confirmation-price-grid">${(product.skus || []).map((sku) => `<label><span>${escapeHtml(sku.option_text || sku.name)}</span><strong>¥ <input type="number" min="0.01" step="0.01" data-confirm-sku-price="${escapeHtml(sku.sku_id)}" data-confirm-product-id="${product.product_id}" value="${typeof sku.purchase_price_cny === "number" ? sku.purchase_price_cny : ""}"></strong></label>`).join("")}</div></section>
    <details class="confirmation-ordinary"><summary>查看自动填写的 ${product.ordinary_field_count} 个普通字段</summary><p>这些字段将按已锁定类目规则和本地经验自动生成，不影响本次快速确认。</p></details>`;
}

async function renderBatchConfirmation(options = {}) {
  if (options.batchId) state.confirmationBatchId = options.batchId;
  if (!state.confirmationBatchId) {
    root.innerHTML = empty("没有待确认批次");
    return;
  }
  if (options.reload !== false || !state.confirmationData || state.confirmationData.batch_id !== state.confirmationBatchId) {
    state.confirmationData = await api(`/api/workbench/batches/${encodeURIComponent(state.confirmationBatchId)}/confirmation`);
  }
  if (!state.confirmationProductId || !state.confirmationData.products.some((item) => item.product_id === state.confirmationProductId)) {
    state.confirmationProductId = state.confirmationData.products[0]?.product_id || null;
  }
  const product = currentConfirmationProduct();
  if (!product) { root.innerHTML = empty("本批次没有可确认商品"); return; }
  const currentIndex = state.confirmationData.products.findIndex((item) => item.product_id === product.product_id);
  root.innerHTML = `<article class="batch-confirmation-page">
    <header class="confirmation-header"><div><h2>批量确认 <span>手动模式 · 本批次只确认一次</span></h2><p>${state.confirmationData.product_count} 个商品 · ${state.confirmationData.uncertain_count} 个重要项 · 预计确认 ${state.confirmationData.estimated_seconds} 秒</p></div><button class="confirmation-adopt-all" data-action="adopt-all-confirmation">全部按AI建议</button></header>
    <ol class="confirmation-steps"><li class="done"><i>1</i>采集完成</li><li class="active"><i>2</i>批量确认</li><li><i>3</i>生成</li><li><i>4</i>预览</li><li><i>5</i>上传</li></ol>
    <div class="confirmation-info">为提高效率，已隐藏认证严格但证据不足的字段。普通字段自动填写，异常商品会进入人工检查。</div>
    <div class="confirmation-layout"><aside class="confirmation-products"><div class="confirmation-pane-title"><strong>本批次商品</strong><span>${state.confirmationData.product_count}</span></div>${state.confirmationData.products.map(confirmationProductListItem).join("")}</aside>
      <main class="confirmation-editor">${renderConfirmationCenter(product)}</main>
      <aside class="confirmation-evidence"><div class="confirmation-pane-title"><strong>证据与参考</strong></div>${confirmationEvidence(product)}<div class="confirmation-category-card"><span>最终类目</span><strong>${escapeHtml((product.category_path_zh || []).join(" › ") || "未显示")}</strong><span>目标店铺</span><strong>${escapeHtml((state.confirmationData.target_store_ids || []).join("、"))}</strong></div></aside>
    </div>
    <footer class="confirmation-footer"><button class="confirmation-back" data-go="inbox">返回采集箱</button><span class="confirmation-autosave">当前修改保存在本批次页面，确认后写入商品资料</span><div class="confirmation-nav"><button data-confirm-nav="prev" ${currentIndex <= 0 ? "disabled" : ""}>上一个商品</button><button data-confirm-nav="next" ${currentIndex >= state.confirmationData.products.length - 1 ? "disabled" : ""}>下一个商品</button></div><button class="confirmation-primary" data-action="confirm-batch">确认全部并开始生成</button></footer>
  </article>`;
}

async function cancelWaitingConfirmation(batchId) {
  if (!confirm("确认取消这次任务？商品资料不会删除，仍会保留在采集箱。")) return;
  const result = await api("/api/workbench/batches/control", {method:"POST", body:JSON.stringify({action:"cancel_confirmation", batch_id:batchId})});
  toast(result.message, "success");
  state.confirmationData = null;
  state.confirmationProductId = null;
  state.confirmationBatchId = null;
  return navigate("inbox");
}

async function openDeleteDialog(productId) {
  document.querySelectorAll(".product-menu.open").forEach((menu) => menu.classList.remove("open"));
  const dialog = document.getElementById("delete-dialog");
  const previewRoot = document.getElementById("delete-preview");
  const confirmButton = document.getElementById("confirm-delete");
  state.deletePreview = null;
  confirmButton.disabled = true;
  confirmButton.innerHTML = `<span class="trash-icon" aria-hidden="true"></span>确认彻底删除`;
  previewRoot.innerHTML = `<div class="delete-loading">正在读取商品和Ozon关联状态</div>`;
  document.getElementById("delete-remote-warning").classList.add("hidden");
  dialog.showModal();
  try {
    const preview = await api(`/api/workbench/products/${productId}/delete-preview`);
    state.deletePreview = preview;
    const shops = preview.associated_shops.length ? preview.associated_shops.join("、") : "未关联";
    previewRoot.innerHTML = `<div class="delete-product-summary">${preview.thumbnail_url ? `<img src="${preview.thumbnail_url}" alt="">` : `<span class="delete-thumb-empty">无图</span>`}<div><h3>${escapeHtml(preview.title)}</h3><p>${escapeHtml(preview.product_id)} · ${preview.sku_count} SKU</p></div></div><dl class="delete-facts"><div><dt>当前状态</dt><dd>${escapeHtml(preview.status)} · ${escapeHtml(preview.current_step)}</dd></div><div><dt>是否已提交Ozon</dt><dd>${preview.submitted_to_ozon ? "是" : "否"}</dd></div><div><dt>已关联店铺</dt><dd>${escapeHtml(shops)}</dd></div></dl>`;
    document.getElementById("delete-remote-warning").classList.toggle("hidden", !preview.remote_warning_required);
    confirmButton.disabled = false;
  } catch (error) {
    previewRoot.innerHTML = `<div class="delete-load-error">无法读取商品信息：${escapeHtml(error.message)}</div>`;
  }
}

function closeDeleteDialog() {
  state.deletePreview = null;
  document.getElementById("delete-dialog").close();
}

async function confirmPermanentDelete() {
  const preview = state.deletePreview;
  if (!preview) return;
  const button = document.getElementById("confirm-delete");
  button.disabled = true;
  button.textContent = "正在彻底删除";
  const previousIndex = state.products.findIndex((item) => item.product_id === preview.product_id);
  try {
    const result = await api(`/api/workbench/products/${preview.product_id}`, {method: "DELETE", body: JSON.stringify({permanent: true, confirm_product_id: preview.product_id})});
    closeDeleteDialog();
    state.products = state.products.filter((item) => item.product_id !== preview.product_id);
    state.currentProduct = null;
    state.currentImageSlot = null;
    state.currentProductId = state.products[Math.min(Math.max(previousIndex, 0), state.products.length - 1)]?.product_id || null;
    toast(result.message, "success");
    await navigate(state.view === "review" && state.currentProductId ? "review" : "inbox", {productId: state.currentProductId});
  } catch (error) {
    toast(`彻底删除失败：${error.message}`, "error");
    button.disabled = false;
    button.innerHTML = `<span class="trash-icon" aria-hidden="true"></span>重新执行清理`;
  }
}

function thumbnail(product) {
  return product.thumbnail_url
    ? `<img src="${product.thumbnail_url}" alt="" loading="lazy">`
    : `<span class="thumb-placeholder">无图</span>`;
}

async function renderBatches() {
  const data = await api("/api/workbench/batches");
  root.innerHTML = `<section class="section-head"><div><h2>任务状态</h2><p>${data.running_pid ? "当前有任务正在运行，可安全停止并保留断点" : "当前没有运行中的任务"}</p></div><div class="toolbar"><button class="secondary-button" data-action="open-batch">运行新任务</button><button class="danger-button" data-batch-action="stop" ${data.running_pid ? "" : "disabled"}>安全停止</button></div></section><div class="table-wrap"><table class="data-table"><thead><tr><th>批次</th><th>状态</th><th>目标店铺</th><th>模式</th><th>商品</th><th>成功/失败</th><th>进度</th><th>操作</th></tr></thead><tbody>${data.items.map((batch) => `<tr><td>${escapeHtml(batch.batch_id)}</td><td>${batch.status === "AWAITING_CONFIRMATION" ? `<span class="status-pill medium">等待批量确认</span>` : escapeHtml(display(batch.display_status || batch.status))}</td><td>${escapeHtml((batch.target_store_ids || []).join("、") || "未选择")}</td><td><span class="status-pill ${batch.auto_upload ? "auto-badge" : "manual-badge"}">${batch.auto_upload ? "自动处理并上传" : "一次确认后生成"}</span></td><td>${batch.product_count || 0}</td><td>${batch.success_count || 0} / ${batch.failed_count || 0}</td><td>${batch.progress || 0}%</td><td>${batch.status === "AWAITING_CONFIRMATION" ? `<span class="batch-confirm-actions"><button class="primary-button" data-open-confirmation="${escapeHtml(batch.batch_id)}">继续确认</button><button class="danger-button" data-cancel-confirmation="${escapeHtml(batch.batch_id)}">取消任务</button></span>` : dateText(batch.created_at || batch.started_at)}</td></tr>`).join("")}</tbody></table></div>`;
}

function formatDuration(seconds) {
  if (typeof seconds !== "number") return "未记录";
  const minutes = Math.floor(seconds / 60);
  const remain = Math.round(seconds % 60);
  return minutes ? `${minutes}分${remain}秒` : `${remain}秒`;
}

async function renderImages() {
  await loadProducts(searchInput.value);
  const details = await Promise.all(state.products.slice(0, 30).map((product) => api(`/api/workbench/products/${product.product_id}`)));
  const images = details.flatMap((product) => product.images.map((image) => ({...image, product_id: product.product_id, title: product.source.title_cn})));
  root.innerHTML = `<section class="section-head"><div><h2>当前图片资产</h2><p>${images.length} 个图片槽位，按商品当前版本展示</p></div></section><div class="image-grid">${images.map((image) => imageTile(image)).join("") || empty("暂无图片")}</div>`;
}

async function renderRisks() {
  const data = await api("/api/workbench/risks");
  const actionLabel = {allow:"自动通过", review:"人工确认", block:"禁止跳过"};
  root.innerHTML = `<section class="section-head"><div><h2>风险中心</h2><p>${data.items.length} 条商品风险 · ${data.rules.length} 条管理规则</p></div></section><div class="settings-grid">${data.rules.map((rule) => `<article class="setting-block"><div class="store-card-head"><div><h3>${escapeHtml(rule.name)}</h3><span class="status-pill ${rule.action === "block" ? "failed" : rule.action === "review" ? "medium" : "low"}">${actionLabel[rule.action]}</span></div>${rule.immutable ? `<span>硬规则</span>` : `<select data-risk-rule="${rule.id}"><option value="allow" ${rule.action === "allow" ? "selected" : ""}>自动通过</option><option value="review" ${rule.action === "review" ? "selected" : ""}>人工确认</option><option value="block" ${rule.action === "block" ? "selected" : ""}>禁止跳过</option></select>`}</div></article>`).join("")}</div><section class="section-head" style="margin-top:16px"><div><h2>风险商品</h2><p>修复后只重新执行受影响步骤</p></div></section><div class="risk-list">${data.items.map((item) => `<article class="risk-row ${item.level}"><strong>${escapeHtml(item.product_id)}</strong>${riskPill(item)}<p>${escapeHtml(item.message)}</p><button class="secondary-button" data-open-product="${item.product_id}">修复</button></article>`).join("") || empty("当前没有阻断风险")}</div>`;
}

async function renderShops() {
  const data = await api("/api/workbench/shops");
  state.shops = data.items;
  root.innerHTML = `<section class="section-head"><div><h2>Ozon店铺</h2><p>凭证只显示配置状态，不在页面、日志或浏览器存储暴露</p></div><button class="primary-button" data-action="add-shop">＋ 添加店铺</button></section><div class="settings-grid">${data.items.map((shop) => `<article class="setting-block"><div class="store-card-head"><div><h3>${escapeHtml(shop.display_name)}</h3><span class="status-pill ${shop.connection_status === "connected" ? "low" : shop.connection_status === "failed" ? "failed" : "medium"}">${storeStatusLabel(shop.connection_status)}</span></div><span>${shop.enabled ? "已启用" : "已禁用"}</span></div><div class="summary-row"><span>凭证</span><strong>${shop.credentials_display}</strong></div><div class="summary-row"><span>采购币种</span><strong>${escapeHtml(shop.currency)}</strong></div><div class="summary-row"><span>最近验证</span><strong>${dateText(shop.last_validated_at)}</strong></div><div class="summary-row"><span>关联商品 / 待处理</span><strong>${shop.associated_product_count} / ${shop.pending_task_count}</strong></div>${shop.last_validation_error ? `<p class="form-help">连接失败：${escapeHtml(shop.last_validation_error)}</p>` : ""}<div class="store-card-actions"><button class="primary-button" data-store-action="validate" data-store-id="${shop.id}">只读测试</button><button class="secondary-button" data-store-action="edit" data-store-id="${shop.id}">编辑</button><button class="secondary-button" data-store-action="toggle" data-store-id="${shop.id}" data-enabled="${shop.enabled ? "false" : "true"}">${shop.enabled ? "禁用" : "启用"}</button><button class="danger-button" data-store-action="delete" data-store-id="${shop.id}">删除本地配置</button></div></article>`).join("") || empty("没有店铺配置")}</div>`;
}

function openStoreDialog(storeId = null) {
  const shop = state.shops.find((item) => item.id === storeId);
  state.editingStoreId = storeId;
  document.getElementById("store-dialog-title").textContent = shop ? "编辑Ozon店铺" : "添加Ozon店铺";
  document.getElementById("store-edit-id").value = storeId || "";
  document.getElementById("store-display-name").value = shop?.display_name || "";
  document.getElementById("store-client-id").value = "";
  document.getElementById("store-api-key").value = "";
  document.getElementById("store-currency").value = shop?.currency || "CNY";
  document.getElementById("store-notes").value = shop?.notes || "";
  document.getElementById("store-dialog").showModal();
}

async function openBatchDialog() {
  await loadWorkbenchSettings();
  const shops = (await api("/api/workbench/shops")).items;
  state.shops = shops;
  state.batchProducts = state.selectedBatchProducts.size ? [...state.selectedBatchProducts] : state.products.filter((item) => item.state === "待处理" || item.state === "失败").map((item) => item.product_id);
  document.getElementById("batch-product-summary").textContent = `本批次包含 ${state.batchProducts.length} 个商品。必须明确选择目标店铺；新批次不会沿用上次选择。`;
  document.getElementById("batch-store-options").innerHTML = shops.map((shop) => { const available = shop.enabled && shop.connection_status === "connected"; return `<label class="store-option ${available ? "" : "unavailable"}"><input type="checkbox" data-batch-store="${escapeHtml(shop.id)}" ${available ? "" : "disabled"}><span><strong>${escapeHtml(shop.display_name)}</strong><small>${storeStatusLabel(shop.connection_status)} · ${shop.credentials_display}</small></span></label>`; }).join("") || `<p class="form-help">没有已配置店铺，请先到店铺中心添加并完成只读验证。</p>`;
  document.getElementById("batch-mode-summary").textContent = state.workbenchSettings.auto_mode_enabled
    ? "当前为自动模式：运行后自动生成、自动质检，并上传到所选店铺；异常商品会转入人工检查。"
    : "当前为手动检查模式：点击运行后先进行全批次唯一一次确认，确认完才开始生成；生成完成后进入预览页。";
  updateBatchDialogButton();
  document.getElementById("batch-dialog").showModal();
}

function renderCategoryDialogResults(items) {
  state.categoryCandidates = items || [];
  const container = document.getElementById("category-dialog-results");
  container.innerHTML = state.categoryCandidates.map((item, index) => `<button type="button" class="category-dialog-item" data-category-index="${index}"><strong>${escapeHtml(item.name_zh || "未翻译类目")}</strong><span>${escapeHtml((item.path_zh || []).join(" / "))}</span><small>category_id ${item.category_id} · type_id ${item.type_id}</small></button>`).join("") || `<p class="form-help">没有找到匹配类目</p>`;
}

async function searchCategoryDialog(query = "") {
  const status = document.getElementById("category-dialog-status");
  status.textContent = "正在搜索本地Ozon类目树…";
  try {
    if (!query.trim()) {
      const prefs = await api("/api/collector/categories/preferences");
      const combined = [...(prefs.favorites || []), ...(prefs.recent || [])];
      const unique = combined.filter((item, index) => combined.findIndex((other) => other.category_id === item.category_id && other.type_id === item.type_id) === index);
      if (unique.length) {
        renderCategoryDialogResults(unique);
        status.textContent = "显示收藏和最近类目";
        return;
      }
    }
    const result = await api(`/api/collector/categories?q=${encodeURIComponent(query || state.currentProduct?.source?.title_cn || "")}&limit=30`);
    renderCategoryDialogResults(result.items || []);
    status.textContent = `找到 ${result.count || 0} 个匹配类目`;
  } catch (error) {
    status.textContent = `类目搜索失败：${error.message}`;
  }
}

async function openCategoryDialog() {
  state.categoryChoice = null;
  state.categoryRules = null;
  document.getElementById("category-search-input").value = "";
  document.getElementById("category-dialog-selected").textContent = "尚未选择新类目";
  document.getElementById("confirm-category-change").disabled = true;
  document.getElementById("category-dialog").showModal();
  await searchCategoryDialog(state.currentProduct?.source?.title_cn || "");
}

function selectedBatchStores() {
  return [...document.querySelectorAll("[data-batch-store]:checked")].map((input) => input.dataset.batchStore);
}

function updateBatchDialogButton() {
  const button = document.getElementById("confirm-create-batch");
  const count = selectedBatchStores().length;
  button.disabled = !count || !state.batchProducts.length;
  button.textContent = count ? `创建批次 · ${count} 家店铺` : "请选择目标店铺";
}

async function refreshCurrentProduct() {
  state.currentProduct = await api(`/api/workbench/products/${state.currentProductId}`);
  await renderReview({productId: state.currentProductId});
}

async function handleImageAction(action, slot, tile) {
  if (action === "prompt") return tile.classList.toggle("editing");
  if (action === "regenerate") return queueImageRegeneration(slot);
  if (action === "queue-prompt") return queueImageRegeneration(slot, tile.querySelector("[data-prompt-input]").value);
  const image = state.currentProduct.images.find((item) => item.slot === slot);
  if (action === "copy-url") {
    await navigator.clipboard.writeText(new URL(image.url, location.origin).href);
    return toast("图片URL已复制");
  }
  if (action === "replace") return tile.querySelector("[data-replace-file]").click();
  if (action === "keep") {
    await api(`/api/workbench/products/${state.currentProductId}/images/${encodeURIComponent(slot)}`, {method:"PATCH", body:JSON.stringify({action:"keep"})});
    toast("已保留，系统会记住这类图片偏好", "success");
    return refreshCurrentProduct();
  }
  if (action === "delete") {
    if (!confirm(`删除图片 ${slot}？只删除当前商品的本地图片，不影响Ozon后台。`)) return;
    await api(`/api/workbench/products/${state.currentProductId}/images/${encodeURIComponent(slot)}`, {method:"DELETE"});
    toast("图片已删除");
    return refreshCurrentProduct();
  }
  if (["set-main", "set-detail", "move-up"].includes(action)) {
    const body = action === "move-up" ? {action:"move", direction:"up"} : {action:"set_role", role:action === "set-main" ? "main" : "detail"};
    await api(`/api/workbench/products/${state.currentProductId}/images/${encodeURIComponent(slot)}`, {method:"PATCH", body:JSON.stringify(body)});
    toast(action === "move-up" ? "图片已前移" : "图片角色已更新");
    return refreshCurrentProduct();
  }
}

async function renderSkills() {
  const data = await api("/api/workbench/skills");
  root.innerHTML = `<section class="section-head"><div><h2>已安装Skill</h2><p>${data.items.length} 个本地能力</p></div></section><div class="table-wrap"><table class="data-table"><thead><tr><th>名称</th><th>来源</th><th>状态</th><th>说明</th></tr></thead><tbody>${data.items.map((skill) => `<tr><td>${escapeHtml(skill.name)}</td><td>${escapeHtml(skill.source)}</td><td><span class="status-pill low">已启用</span></td><td>${escapeHtml(skill.summary)}</td></tr>`).join("")}</tbody></table></div>`;
}

async function renderExperience() {
  await loadProducts();
  const details = await Promise.all(state.products.map((product) => api(`/api/workbench/products/${product.product_id}`)));
  const edited = details.filter((product) => product.draft.version > 0);
  root.innerHTML = `<section class="section-head"><div><h2>人工修改记录</h2><p>只记录已发生的修改，不自动启用新规则</p></div></section><div class="metric-grid"><article class="metric"><span>有人工版本的商品</span><strong>${edited.length}</strong></article><article class="metric"><span>待管理员确认规则</span><strong>0</strong></article><article class="metric success"><span>已确认经验</span><strong>0</strong></article><article class="metric"><span>自动写入Skill</span><strong>0</strong></article></div><div class="list-layout" style="margin-top:12px">${edited.map((product) => { const card = state.products.find((item) => item.product_id === product.product_id) || {}; return `<article class="list-card">${thumbnail(card)}<div><h3>${escapeHtml(product.source.title_cn)}</h3><p>${product.product_id} · 当前人工版本 v${product.draft.version} · ${dateText(product.draft.saved_at)}</p><p>锁定字段：${escapeHtml((product.draft.locked_fields || []).join("、") || "无")}</p></div><div class="list-actions"><button data-open-product="${product.product_id}">查看</button></div></article>`; }).join("") || empty("还没有人工修改记录")}</div>`;
}

function logEntry(item) {
  return `<div class="log-entry"><time>${dateText(item.at)}</time><span>${escapeHtml(item.product_id || "系统")}</span><strong>${escapeHtml(item.message)}</strong></div>`;
}

async function renderLogs() {
  const data = await api("/api/workbench/logs");
  root.innerHTML = `<section class="section-head"><div><h2>运营时间线</h2><p>${data.items.length} 条可读记录</p></div></section><article class="panel"><div class="log-list">${data.items.map(logEntry).join("") || empty("暂无日志")}</div></article>`;
}

async function renderSettings() {
  if (!state.session?.can_manage_settings) throw new Error("只有工作室负责人可以打开系统设置");
  const [operators] = await Promise.all([api("/api/workbench/operators"), loadWorkbenchSettings()]);
  const currentId = state.session.operator?.id;
  root.innerHTML = `<section class="section-head"><div><h2>系统设置</h2><p>负责人可以修改全局模式和成员；任何人都只能看到自己的商品。</p></div></section><div class="settings-grid">
    <article class="setting-block primary-setting"><h3>处理模式</h3><label class="toggle-row"><span><strong>AI自动模式</strong><small>关闭：生成后停在检查上传；开启：运行任务后自动质检并上传，异常商品转入“需要我处理”</small></span><input type="checkbox" data-global-auto-mode ${state.workbenchSettings.auto_mode_enabled ? "checked" : ""}></label><div class="summary-row"><span>经验学习</span><strong>同类目同类修改出现2次后才启用</strong></div></article>
    <article class="setting-block"><div class="store-card-head"><div><h3>工作室成员</h3><span class="status-pill low">严格隔离</span></div><button class="primary-button" data-action="add-operator">添加成员</button></div><p class="form-help">负责人只能管理设置，不能查看成员商品；成员可以选择任意已配置店铺。</p><div class="member-list">${operators.items.map((item) => `<div class="member-row"><span><strong>${escapeHtml(item.display_name)}</strong><small>${escapeHtml(item.id)} · ${item.role === "owner" ? "负责人" : "成员"} · ${item.enabled ? "已启用" : "已停用"}</small></span><span class="store-card-actions">${item.id !== currentId ? `<button class="secondary-button" data-reset-operator="${escapeHtml(item.id)}">重置访问码</button>` : ""}${item.id !== currentId && item.role !== "owner" ? `<button class="danger-button" data-delete-operator="${escapeHtml(item.id)}">删除</button>` : ""}</span></div>`).join("")}</div></article>
    <article class="setting-block"><h3>永久安全规则</h3><div class="summary-row"><span>Ozon</span><strong>禁止重复CREATE、pending禁止重传</strong></div><div class="summary-row"><span>库存</span><strong>不提交库存字段，库存接口永久禁用</strong></div><div class="summary-row"><span>商品</span><strong>真实性失败阻断，单商品最多10个SKU</strong></div><div class="summary-row"><span>图片</span><strong>失败只重做单图，保留真实SKU差异</strong></div></article>
    <article class="setting-block"><h3>我的数据导出</h3><div class="store-card-actions"><a class="secondary-button" href="/api/workbench/export/csv">CSV</a><a class="secondary-button" href="/api/workbench/export/xlsx">Excel</a><a class="secondary-button" href="/api/workbench/export/json">JSON</a><a class="secondary-button" href="/api/workbench/export/backup">我的备份</a></div><p class="form-help">导出只包含当前成员自己的商品，自动排除所有明文店铺密钥。</p></article>
  </div>`;
}

function empty(message) {
  return `<div class="empty-state"><strong>${escapeHtml(message)}</strong></div>`;
}

document.getElementById("primary-nav").addEventListener("click", (event) => {
  const button = event.target.closest("[data-view]");
  if (button) navigate(button.dataset.view);
});

document.getElementById("toggle-sidebar").addEventListener("click", () => {
  if (window.innerWidth <= 700) shell.classList.toggle("mobile-nav-open");
  else shell.classList.toggle("sidebar-collapsed");
});

document.getElementById("refresh-all").addEventListener("click", () => navigate(state.view));
document.getElementById("global-auto-mode").addEventListener("change", (event) => {
  updateGlobalAutoMode(event.target.checked).then(() => navigate(state.view)).catch((error) => {
    event.target.checked = !event.target.checked;
    toast(error.message, "error");
  });
});
searchInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") navigate("review", {query: searchInput.value});
});

root.addEventListener("input", (event) => {
  const confirmationProductId = event.target.dataset.confirmProductId;
  if (confirmationProductId && state.confirmationData) {
    const product = state.confirmationData.products.find((item) => item.product_id === confirmationProductId);
    if (product && event.target.dataset.confirmField) {
      const [fieldKey, childKey] = event.target.dataset.confirmField.split(".");
      if (childKey) product.fields[fieldKey].value[childKey] = event.target.value;
      else product.fields[fieldKey].value = event.target.value;
    }
    if (product && event.target.dataset.confirmSkuPrice) {
      const sku = product.skus.find((item) => item.sku_id === event.target.dataset.confirmSkuPrice);
      if (sku) sku.purchase_price_cny = event.target.value;
    }
    const saveText = root.querySelector(".confirmation-autosave");
    if (saveText) saveText.textContent = "修改已暂存，点击确认后写入商品资料";
    return;
  }
  const field = event.target.dataset.draftField;
  if (field) scheduleDraftSave({[field]: collectDraftField(field, event.target.value)});
  const attributeId = event.target.dataset.attributeId;
  if (attributeId) scheduleDraftSave({attributes: {[attributeId]: event.target.value || "unknown"}});
  const skuId = event.target.dataset.skuPrice;
  if (skuId && Number(event.target.value) > 0) scheduleDraftSave({sku_overrides: {[skuId]: {selling_price_cny:Number(event.target.value)}}});
});

root.addEventListener("change", (event) => {
  if (event.target.id === "auto-advance") {
    state.autoAdvance = event.target.checked;
    scheduleDraftSave({auto_advance: state.autoAdvance});
  }
  if (event.target.matches("[data-product-store]")) {
    const storeId = event.target.dataset.productStore;
    if (event.target.checked) state.selectedStoreIds.add(storeId); else state.selectedStoreIds.delete(storeId);
    const button = root.querySelector('[data-action="run-product"]');
    if (button) { button.textContent = `上传至 ${state.selectedStoreIds.size} 家店铺`; button.disabled = !state.selectedStoreIds.size || state.draftSaveFailed; }
    const saveStores = root.querySelector('[data-action="save-product-stores"]');
    if (saveStores) saveStores.disabled = !state.selectedStoreIds.size;
    const storeSummary = root.querySelector("details.store-selector summary");
    if (storeSummary) storeSummary.textContent = `上传至 ${state.selectedStoreIds.size} 家店铺`;
  }
  if (event.target.matches("[data-select-batch-product]")) {
    const productId = event.target.dataset.selectBatchProduct;
    if (event.target.checked) state.selectedBatchProducts.add(productId); else state.selectedBatchProducts.delete(productId);
    renderInbox();
  }
  if (event.target.matches("[data-select-all-products]")) {
    state.selectedBatchProducts = event.target.checked ? new Set(state.products.map((item) => item.product_id)) : new Set();
    renderInbox();
  }
  if (event.target.matches("[data-replace-file]")) {
    const file = event.target.files?.[0];
    const tile = event.target.closest("[data-image-slot]");
    if (!file || !tile) return;
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        await api(`/api/workbench/products/${state.currentProductId}/images/${encodeURIComponent(tile.dataset.imageSlot)}/content`, {method:"PUT", body:JSON.stringify({data_url:reader.result})});
        toast("图片已替换并等待重新质检");
        await refreshCurrentProduct();
      } catch (error) { toast(error.message, "error"); }
    };
    reader.readAsDataURL(file);
  }
  if (event.target.matches("[data-risk-rule]")) {
    api(`/api/workbench/risk-rules/${event.target.dataset.riskRule}`, {method:"PATCH", body:JSON.stringify({action:event.target.value})})
      .then(() => toast("风险规则已保存", "success"))
      .catch((error) => { toast(error.message, "error"); renderRisks(); });
  }
  if (event.target.matches("[data-global-auto-mode]")) {
    updateGlobalAutoMode(event.target.checked).then(() => renderSettings()).catch((error) => toast(error.message, "error"));
  }
});

async function updateGlobalAutoMode(enabled) {
  state.workbenchSettings = await api("/api/workbench/settings", {method:"PATCH", body:JSON.stringify({auto_mode_enabled:Boolean(enabled)})});
  await loadWorkbenchSettings();
  toast(enabled ? "自动模式已开启；异常商品仍会转入人工检查" : "已切换为手动预览检查", "success");
}

root.addEventListener("dragstart", (event) => {
  const card = event.target.closest(".preview-image-card[data-image-slot]");
  if (!card) return;
  state.dragImageSlot = card.dataset.imageSlot;
  card.classList.add("dragging");
  event.dataTransfer.effectAllowed = "move";
});

root.addEventListener("dragend", (event) => {
  event.target.closest(".preview-image-card")?.classList.remove("dragging");
  state.dragImageSlot = null;
});

root.addEventListener("dragover", (event) => {
  if (event.target.closest(".preview-image-card") && state.dragImageSlot) event.preventDefault();
});

root.addEventListener("drop", async (event) => {
  const target = event.target.closest(".preview-image-card[data-image-slot]");
  if (!target || !state.dragImageSlot || target.dataset.imageSlot === state.dragImageSlot) return;
  event.preventDefault();
  const order = state.currentProduct.images.map((item) => item.slot);
  const from = order.indexOf(state.dragImageSlot);
  const to = order.indexOf(target.dataset.imageSlot);
  order.splice(to, 0, order.splice(from, 1)[0]);
  try {
    await api(`/api/workbench/products/${state.currentProductId}/images/${encodeURIComponent(state.dragImageSlot)}`, {method:"PATCH", body:JSON.stringify({action:"reorder", order})});
    toast("图片顺序已保存", "success");
    await refreshCurrentProduct();
  } catch (error) { toast(error.message, "error"); }
});

root.addEventListener("click", async (event) => {
  const menuTrigger = event.target.closest("[data-menu-product]");
  if (menuTrigger) {
    const menu = menuTrigger.closest(".product-menu");
    document.querySelectorAll(".product-menu.open").forEach((item) => { if (item !== menu) item.classList.remove("open"); });
    menu.classList.toggle("open");
    return;
  }
  const deleteProduct = event.target.closest("[data-delete-product]");
  if (deleteProduct) return openDeleteDialog(deleteProduct.dataset.deleteProduct);
  const deleteOperator = event.target.closest("[data-delete-operator]");
  if (deleteOperator) {
    if (!confirm("删除该成员访问配置？不会删除店铺配置，也不会把其商品转给其他人。")) return;
    try {
      await api(`/api/workbench/operators/${encodeURIComponent(deleteOperator.dataset.deleteOperator)}`, {method:"DELETE"});
      toast("成员访问配置已删除", "success");
      return renderSettings();
    } catch (error) { return toast(error.message, "error"); }
  }
  const resetOperator = event.target.closest("[data-reset-operator]");
  if (resetOperator) {
    if (!confirm("重置后旧访问码会立即失效。确认继续？")) return;
    try {
      const result = await api(`/api/workbench/operators/${encodeURIComponent(resetOperator.dataset.resetOperator)}`, {method:"PATCH", body:JSON.stringify({regenerate_access_code:true})});
      document.getElementById("operator-one-time-code").textContent = result.one_time_access_code;
      document.getElementById("access-code-result-dialog").showModal();
      return renderSettings();
    } catch (error) { return toast(error.message, "error"); }
  }
  const go = event.target.closest("[data-go]");
  if (go) return navigate(go.dataset.go);
  const inboxFilter = event.target.closest("[data-inbox-filter]");
  if (inboxFilter) { state.inboxFilter = inboxFilter.dataset.inboxFilter; return renderInbox(); }
  const primaryAction = event.target.closest("[data-primary-action]");
  if (primaryAction) {
    const productId = primaryAction.dataset.productId;
    const actionKey = primaryAction.dataset.primaryAction;
    state.currentProductId = productId;
    state.currentImageSlot = null;
    if (actionKey === "answer") {
      const product = state.products.find((item) => item.product_id === productId) || await api(`/api/workbench/products/${productId}`);
      document.getElementById("question-product-id").value = productId;
      document.getElementById("question-product-title").textContent = product.title_cn || product.source?.title_cn || productId;
      document.getElementById("question-text").textContent = product.pending_question?.question || "请确认商品关键信息";
      document.getElementById("question-answer").value = "";
      document.getElementById("question-dialog").showModal();
      return;
    }
    if (actionKey === "run") {
      state.selectedBatchProducts = new Set([productId]);
      return openBatchDialog();
    }
    return navigate("review", {productId});
  }
  const open = event.target.closest("[data-open-product]");
  if (open) { state.currentProductId = open.dataset.openProduct; state.currentImageSlot = null; return navigate("review", {productId: state.currentProductId}); }
  const openConfirmation = event.target.closest("[data-open-confirmation]");
  if (openConfirmation) {
    state.confirmationBatchId = openConfirmation.dataset.openConfirmation;
    state.confirmationData = null;
    state.confirmationProductId = null;
    return navigate("confirm", {batchId: state.confirmationBatchId});
  }
  const cancelConfirmation = event.target.closest("[data-cancel-confirmation]");
  if (cancelConfirmation) {
    try { return await cancelWaitingConfirmation(cancelConfirmation.dataset.cancelConfirmation); }
    catch (error) { return toast(error.message, "error"); }
  }
  const confirmationProduct = event.target.closest("[data-confirm-product]");
  if (confirmationProduct) {
    state.confirmationProductId = confirmationProduct.dataset.confirmProduct;
    state.confirmationEvidenceTab = "sku";
    return renderBatchConfirmation({reload:false});
  }
  const evidenceTab = event.target.closest("[data-confirm-evidence-tab]");
  if (evidenceTab) {
    state.confirmationEvidenceTab = evidenceTab.dataset.confirmEvidenceTab;
    return renderBatchConfirmation({reload:false});
  }
  const confirmationNav = event.target.closest("[data-confirm-nav]");
  if (confirmationNav && state.confirmationData) {
    const products = state.confirmationData.products;
    const currentIndex = products.findIndex((item) => item.product_id === state.confirmationProductId);
    const nextIndex = confirmationNav.dataset.confirmNav === "prev" ? Math.max(0, currentIndex - 1) : Math.min(products.length - 1, currentIndex + 1);
    state.confirmationProductId = products[nextIndex].product_id;
    state.confirmationEvidenceTab = "sku";
    return renderBatchConfirmation({reload:false});
  }
  const select = event.target.closest("[data-select-product]");
  if (select) { state.currentProductId = select.dataset.selectProduct; state.currentImageSlot = null; return renderReview(); }
  const nav = event.target.closest("[data-review-nav]");
  if (nav) return changeProduct(nav.dataset.reviewNav);
  const mode = event.target.closest("[data-review-mode]");
  if (mode) {
    state.reviewMode = mode.dataset.reviewMode;
    await saveDraft({review_mode: state.reviewMode});
    document.querySelectorAll("[data-review-mode]").forEach((button) => button.classList.toggle("active", button.dataset.reviewMode === state.reviewMode));
    if (state.reviewMode === "auto") toast("AI自动审核仍受真实性、平台硬规则和防重复规则限制");
    return;
  }
  const depth = event.target.closest("[data-review-depth]");
  if (depth) {
    state.reviewDepth = depth.dataset.reviewDepth;
    await saveDraft({review_depth: state.reviewDepth});
    return renderReview({productId: state.currentProductId});
  }
  const imageTarget = event.target.closest("[data-image-slot]");
  if (imageTarget) {
    state.currentImageSlot = imageTarget.dataset.imageSlot;
    if (imageTarget.dataset.productId) state.currentProductId = imageTarget.dataset.productId;
  }
  const openImage = event.target.closest("[data-open-image]");
  if (openImage) {
    document.getElementById("dialog-image").src = openImage.dataset.openImage;
    document.getElementById("image-dialog").showModal();
    return;
  }
  const imageAction = event.target.closest("[data-image-action]");
  if (imageAction) {
    const tile = imageAction.closest("[data-image-slot]");
    const slot = tile.dataset.imageSlot;
    state.currentImageSlot = slot;
    try { await handleImageAction(imageAction.dataset.imageAction, slot, tile); } catch (error) { toast(error.message, "error"); }
    return;
  }
  const removeTag = event.target.closest("[data-remove-tag]");
  if (removeTag) {
    const tags = [...(state.currentProduct.content.tags || [])];
    tags.splice(Number(removeTag.dataset.removeTag), 1);
    state.currentProduct.content.tags = tags;
    await saveDraft({tags});
    return renderReview();
  }
  const retryStore = event.target.closest("[data-retry-store]");
  if (retryStore) {
    const storeId = retryStore.dataset.retryStore;
    const shop = (state.currentProduct.stores || []).find((item) => item.id === storeId);
    if (!confirm(`只重试店铺“${shop?.display_name || storeId}”？成功或处理中店铺不会重传。`)) return;
    retryStore.disabled = true;
    try {
      const result = await api(`/api/workbench/products/${state.currentProductId}/stores/${encodeURIComponent(storeId)}/retry`, {method:"POST"});
      toast(`失败店铺重试已启动：${result.batch_id}`, "success");
      return refreshCurrentProduct();
    } catch (error) { retryStore.disabled = false; return toast(error.message, "error"); }
  }
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "toggle-queue") {
    state.queueCollapsed = !state.queueCollapsed;
    document.querySelector(".review-shell")?.classList.toggle("queue-collapsed", state.queueCollapsed);
    return;
  }
  if (action === "select-all-stores") {
    (state.currentProduct.stores || []).filter((shop) => shop.enabled && shop.connection_status === "connected").forEach((shop) => state.selectedStoreIds.add(shop.id));
    return renderReview({productId: state.currentProductId});
  }
  if (action === "save-product-stores") {
    try {
      await api(`/api/workbench/products/${state.currentProductId}/stores`, {method:"PUT", body:JSON.stringify({store_ids:[...state.selectedStoreIds], overrides:collectStoreOverrides()})});
      toast(`已保存 ${state.selectedStoreIds.size} 家目标店铺`, "success");
      return refreshCurrentProduct();
    } catch (error) { return toast(error.message, "error"); }
  }
  if (action === "add-shop") return openStoreDialog();
  if (action === "add-operator") {
    document.getElementById("operator-form").reset();
    return document.getElementById("operator-dialog").showModal();
  }
  if (action === "change-category") return openCategoryDialog();
  if (action === "open-batch") return openBatchDialog();
  if (action === "adopt-all-confirmation") {
    toast("已采用全部AI建议；你仍可以修改任何一项", "success");
    return;
  }
  if (action === "confirm-batch") {
    const button = event.target.closest("button");
    button.disabled = true;
    button.textContent = "正在保存并启动";
    const products = (state.confirmationData?.products || []).map((product) => ({
      product_id: product.product_id,
      fields: {
        product_dimensions: product.fields.product_dimensions.value,
        product_weight_g: product.fields.product_weight_g.value,
        package_dimensions: product.fields.package_dimensions.value,
        package_weight_g: product.fields.package_weight_g.value,
        material: product.fields.material.value || "unknown",
      },
      sku_prices: Object.fromEntries((product.skus || []).map((sku) => [sku.sku_id, sku.purchase_price_cny])),
    }));
    try {
      const result = await api(`/api/workbench/batches/${encodeURIComponent(state.confirmationBatchId)}/confirm`, {method:"POST", body:JSON.stringify({products})});
      toast(result.status === "queued" ? `确认已保存，批次排队位置 ${result.queue_position}` : "确认已保存，开始生成商品资料", "success");
      state.confirmationData = null;
      state.confirmationProductId = null;
      state.confirmationBatchId = null;
      return navigate("inbox");
    } catch (error) {
      toast(error.message, "error");
      button.disabled = false;
      button.textContent = "确认全部并开始生成";
      return;
    }
  }
  if (action === "add-tag") {
    const input = document.getElementById("new-tag");
    let tag = input.value.trim();
    if (!tag) return;
    if (!tag.startsWith("#")) tag = `#${tag}`;
    if (tag.length > 30) return toast("单个标签不能超过30个字符", "error");
    const tags = [...(state.currentProduct.content.tags || [])];
    if (!tags.includes(tag) && tags.length < 30) tags.push(tag);
    state.currentProduct.content.tags = tags;
    await saveDraft({tags});
    return renderReview();
  }
  if (action === "skip") return changeProduct("next");
  if (action === "regenerate-current" && state.currentImageSlot) return queueImageRegeneration(state.currentImageSlot);
  if (action === "save-visual-preference") {
    const hint = document.getElementById("visual-set-hint")?.value.trim() || "";
    try {
      const result = await api(`/api/workbench/products/${state.currentProductId}/visual-preference`, {method:"PUT", body:JSON.stringify({set_hint:hint})});
      toast(result.invalidated_steps.length ? "风格意见已保存；旧图片方案已失效，重新运行后生效" : "风格意见已保存", "success");
      return refreshCurrentProduct();
    } catch (error) { return toast(error.message, "error"); }
  }
  if (action === "run-product") {
    if (state.draftSaveFailed) return toast("草稿保存失败，已阻止上传", "error");
    if (!state.selectedStoreIds.size) return toast("请先选择并保存目标店铺", "error");
    const readyToUpload = String(state.currentProduct.status?.status || "").toUpperCase() === "OZON_READY";
    const autoUpload = readyToUpload || Boolean(state.workbenchSettings.auto_mode_enabled);
    const confirmation = autoUpload
      ? `确认后将上传至 ${state.selectedStoreIds.size} 家店铺。系统不会提交库存，处理中不会重复上传。是否继续？`
      : `确认后开始生成商品资料；完成后进入预览检查页，不会自动上传。是否继续？`;
    if (!confirm(confirmation)) return;
    const button = event.target.closest("button");
    button.disabled = true; button.classList.add("uploading"); button.textContent = "正在启动";
    try {
      const result = await api(`/api/workbench/products/${state.currentProductId}/run`, {method: "POST", body:JSON.stringify({store_ids:[...state.selectedStoreIds], overrides:collectStoreOverrides(), auto_upload:autoUpload})});
      if (result.status === "awaiting_confirmation") {
        toast("商品已进入本批次唯一一次确认", "success");
        state.confirmationBatchId = result.batch_id;
        state.confirmationData = null;
        state.confirmationProductId = null;
        return navigate("confirm", {batchId:result.batch_id});
      }
      const message = result.status === "queued"
        ? `单商品任务已排队：${result.batch_id}，前面还有 ${Math.max((result.queue_position || 1) - 1, 0)} 个任务`
        : result.status === "already_queued"
          ? `该商品已在任务中：${result.batch_id}`
          : `任务已启动：${result.batch_id}`;
      toast(message, "success");
      if (state.autoAdvance) changeProduct("next");
    } catch (error) { toast(error.message, "error"); button.disabled = false; button.classList.remove("uploading"); button.textContent = autoUpload ? `确认修改并立即上传（${state.selectedStoreIds.size} 家店铺）` : "运行任务生成商品资料"; }
    return;
  }
  if (action === "refresh-ozon") {
    try { const result = await api("/api/inbox/refresh-ozon-status", {method: "POST"}); toast(`已只读刷新 ${result.synced_product_ids.length} 个任务`); await navigate(state.view); } catch (error) { toast(error.message, "error"); }
  }
  const batchAction = event.target.closest("[data-batch-action]");
  if (batchAction) {
    try { const result = await api("/api/workbench/batches/control", {method: "POST", body: JSON.stringify({action: batchAction.dataset.batchAction})}); toast(result.message || `批次操作完成：${result.status}`, "success"); await (state.view === "review" ? refreshCurrentProduct() : renderBatches()); } catch (error) { toast(error.message, "error"); }
  }
  const suggestion = event.target.closest("[data-suggestion]");
  if (suggestion) {
    try {
      await api(`/api/workbench/products/${state.currentProductId}/suggestions/${encodeURIComponent(suggestion.dataset.suggestion)}`, {method:"POST", body:JSON.stringify({action:suggestion.dataset.suggestionAction})});
      toast("建议状态已保存");
      return refreshCurrentProduct();
    } catch (error) { return toast(error.message, "error"); }
  }
  const storeAction = event.target.closest("[data-store-action]");
  if (storeAction) {
    const storeId = storeAction.dataset.storeId;
    try {
      if (storeAction.dataset.storeAction === "edit") return openStoreDialog(storeId);
      if (storeAction.dataset.storeAction === "validate") {
        toast("正在执行Seller API只读连接测试");
        const result = await api(`/api/workbench/shops/${storeId}/validate`, {method:"POST"});
        toast(result.connection_status === "connected" ? "只读连接测试通过" : `连接失败：${result.last_validation_error}`, result.connection_status === "connected" ? "success" : "error");
      }
      if (storeAction.dataset.storeAction === "toggle") await api(`/api/workbench/shops/${storeId}/enabled`, {method:"POST", body:JSON.stringify({enabled:storeAction.dataset.enabled === "true"})});
      if (storeAction.dataset.storeAction === "delete") {
        if (!confirm("只删除本地店铺配置，不会删除Ozon后台商品。确认删除？")) return;
        await api(`/api/workbench/shops/${storeId}`, {method:"DELETE"});
        toast("本地店铺配置已删除", "success");
      }
      return renderShops();
    } catch (error) { return toast(error.message, "error"); }
  }
});

document.getElementById("close-image").addEventListener("click", () => document.getElementById("image-dialog").close());
document.getElementById("cancel-delete").addEventListener("click", closeDeleteDialog);
document.getElementById("cancel-delete-x").addEventListener("click", closeDeleteDialog);
document.getElementById("confirm-delete").addEventListener("click", confirmPermanentDelete);
document.getElementById("enable-notifications").addEventListener("click", enableDesktopNotifications);
document.getElementById("access-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const code = document.getElementById("access-code-input").value.trim();
  if (!code || !state.accessCodeResolve) return;
  document.getElementById("access-dialog").close();
  const resolve = state.accessCodeResolve;
  state.accessCodeResolve = null;
  state.accessCodeRequest = null;
  resolve(code);
});
document.getElementById("delete-dialog").addEventListener("cancel", (event) => { event.preventDefault(); closeDeleteDialog(); });
document.querySelectorAll("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => document.getElementById(button.dataset.closeDialog).close()));
document.getElementById("category-search-input").addEventListener("input", (event) => {
  clearTimeout(state.categorySearchTimer);
  state.categorySearchTimer = setTimeout(() => searchCategoryDialog(event.target.value), 250);
});
document.getElementById("category-dialog-results").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-category-index]");
  if (!button) return;
  state.categoryChoice = state.categoryCandidates[Number(button.dataset.categoryIndex)];
  state.categoryRules = null;
  document.querySelectorAll(".category-dialog-item").forEach((item) => item.classList.toggle("active", item === button));
  const selected = document.getElementById("category-dialog-selected");
  const confirmButton = document.getElementById("confirm-category-change");
  confirmButton.disabled = true;
  selected.textContent = `正在加载“${state.categoryChoice.name_zh}”的官方必填属性、字典值和is_aspect…`;
  try {
    state.categoryRules = await api("/api/collector/categories/rules", {
      method: "POST",
      body: JSON.stringify({category_id:state.categoryChoice.category_id, type_id:state.categoryChoice.type_id, allow_readonly_fetch:true}),
    });
    selected.textContent = `已选：${state.categoryChoice.name_zh} · category_id ${state.categoryChoice.category_id} · type_id ${state.categoryChoice.type_id} · 必填 ${state.categoryRules.required_attribute_ids.length} · SKU维度 ${state.categoryRules.aspect_attribute_ids.length}`;
    confirmButton.disabled = false;
  } catch (error) {
    selected.textContent = `规则加载失败：${error.message}`;
  }
});
document.getElementById("confirm-category-change").addEventListener("click", async () => {
  if (!state.categoryChoice || !state.categoryRules) return;
  if (!confirm("确认修改最终Ozon类目？旧属性、图片策略和上传数据会失效，原始1688资料不会删除。")) return;
  const button = document.getElementById("confirm-category-change");
  button.disabled = true;
  try {
    const result = await api(`/api/collector/products/${state.currentProductId}/category`, {
      method: "PUT",
      body: JSON.stringify({
        category_id: state.categoryChoice.category_id,
        type_id: state.categoryChoice.type_id,
        category_path: state.categoryChoice.path,
        category_name_zh: state.categoryChoice.name_zh,
        category_path_zh: state.categoryChoice.path_zh,
        selected_at: new Date().toISOString(),
        rules_snapshot: state.categoryRules,
      }),
    });
    document.getElementById("category-dialog").close();
    toast(`最终类目已修改，${result.invalidated.length} 项旧结果已失效；请重新运行商品`, "success");
    await refreshCurrentProduct();
  } catch (error) {
    button.disabled = false;
    toast(error.message, "error");
  }
});
document.getElementById("store-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const storeId = document.getElementById("store-edit-id").value;
  const payload = {
    display_name: document.getElementById("store-display-name").value.trim(),
    client_id: document.getElementById("store-client-id").value.trim(),
    api_key: document.getElementById("store-api-key").value.trim(),
    currency: document.getElementById("store-currency").value,
    notes: document.getElementById("store-notes").value.trim(), enabled: true,
  };
  try {
    await api(storeId ? `/api/workbench/shops/${storeId}` : "/api/workbench/shops", {method:storeId ? "PATCH" : "POST", body:JSON.stringify(payload)});
    document.getElementById("store-dialog").close();
    toast(storeId ? "店铺配置已更新，修改凭证后需重新验证" : "店铺已添加，请执行只读连接测试", "success");
    await renderShops();
  } catch (error) { toast(error.message, "error"); }
});
document.getElementById("question-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const productId = document.getElementById("question-product-id").value;
  const answer = document.getElementById("question-answer").value.trim();
  if (!productId || !answer) return;
  try {
    await api(`/api/workbench/products/${productId}/question/answer`, {method:"POST", body:JSON.stringify({answer})});
    document.getElementById("question-dialog").close();
    toast("回答已保存。商品现在可以从失败步骤继续运行。", "success");
    await pollNotifications({showDesktop:false});
    return navigate("attention");
  } catch (error) { toast(error.message, "error"); }
});
document.getElementById("operator-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    id: document.getElementById("operator-id").value.trim(),
    display_name: document.getElementById("operator-display-name").value.trim(),
    role: "member",
  };
  try {
    const result = await api("/api/workbench/operators", {method:"POST", body:JSON.stringify(payload)});
    document.getElementById("operator-dialog").close();
    document.getElementById("operator-one-time-code").textContent = result.one_time_access_code || "访问码已更新";
    document.getElementById("access-code-result-dialog").showModal();
    await renderSettings();
  } catch (error) { toast(error.message, "error"); }
});
document.getElementById("batch-store-options").addEventListener("change", updateBatchDialogButton);
document.getElementById("batch-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const storeIds = selectedBatchStores();
  if (!storeIds.length) return;
  try {
    const result = await api("/api/workbench/batches/create", {method:"POST", body:JSON.stringify({product_ids:state.batchProducts, store_ids:storeIds})});
    document.getElementById("batch-dialog").close();
    state.selectedBatchProducts = new Set();
    if (result.status === "awaiting_confirmation") {
      toast("批次已建立，请一次确认重要信息", "success");
      state.confirmationBatchId = result.batch_id;
      state.confirmationData = null;
      state.confirmationProductId = null;
      await navigate("confirm", {batchId:result.batch_id});
      return;
    }
    const message = result.status === "queued"
      ? `批次已排队：${result.batch_id}，排队位置 ${result.queue_position}`
      : result.status === "already_queued"
        ? "所选商品已在任务队列中"
        : result.status === "started"
          ? `批次已启动：${result.batch_id}`
          : "没有可运行商品";
    toast(message, ["started", "queued"].includes(result.status) ? "success" : "info");
    await navigate("inbox");
  } catch (error) { toast(error.message, "error"); }
});

document.addEventListener("click", (event) => {
  if (!event.target.closest(".product-menu")) document.querySelectorAll(".product-menu.open").forEach((menu) => menu.classList.remove("open"));
});

document.addEventListener("keydown", (event) => {
  if (event.target.matches("input,textarea")) return;
  if (state.view !== "review") return;
  if (event.key === "ArrowLeft") changeProduct("prev");
  if (event.key === "ArrowRight") changeProduct("next");
  if (event.key.toLowerCase() === "r" && state.currentImageSlot) queueImageRegeneration(state.currentImageSlot);
});

async function bootstrapWorkbench() {
  await loadSession();
  try { await loadWorkbenchSettings(); } catch (_) {}
  await pollNotifications({showDesktop:false});
  clearInterval(state.notificationTimer);
  state.notificationTimer = setInterval(() => pollNotifications(), 15000);
  await navigate("inbox");
}

bootstrapWorkbench();
