const root = document.getElementById("view-root");
const shell = document.querySelector(".app-shell");
const notice = document.getElementById("notice");
const searchInput = document.getElementById("global-search");

const viewMeta = {
  review: ["商品详情", "看图、改资料、确认上传"],
  confirm: ["批量确认", "本批次只确认一次"],
  inbox: ["采集箱", "本次采集、生成和上传入口"],
  market: ["选品与关键词", "近期热销商品、增长机会和搜索词"],
  waiting: ["等待上传", "资料和图片完成，点商品确认上传"],
  attention: ["需要处理", "失败商品和可继续任务"],
  listed: ["已提交商品", "查看本地已交接给 Ozon 的商品"],
  finance: ["财务中心", "全部店铺利润、覆盖率与待核对明细"],
  batches: ["处理中", "正在生成、排队和上传的任务"],
  shops: ["店铺设置", "添加、验证和管理Ozon店铺"],
  settings: ["系统设置", "生产安全规则"],
};

const STEP_LABELS = {
  queue: "排队等待", validate_source: "检查采集数据", product_analysis: "分析商品事实",
  category_match: "匹配Ozon类目", variant_rules: "判断SKU变体", measurements: "处理商品和包装尺寸",
  offer_exists_check: "检查Ozon是否已有商品", upload_feasibility: "检查上传条件",
  product_positioning: "确定商品定位", ecommerce_design: "设计完整上架与图片销售方案", russian_copy: "生成俄文标题和文案",
  image_plan: "规划图片方案", image_generation: "生成商品图片",
  image_qc: "进行图片质检", store_variant_assets: "生成各店独立图片", field_completion: "填写Ozon属性",
  ozon_upload: "提交Ozon",
  manual_ozon_upload: "等待手动上传",
};

const STORE_GROUP_PRESETS = [
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
];

function currentStoreSelectionRule() {
  const selected = [...state.selectedStoreIds];
  if (!selected.length) return {mode:"empty"};
  if (selected.length === 1) return {mode:"single_store", storeIds:selected};
  const preset = STORE_GROUP_PRESETS.find((item) =>
    item.storeIds.length === selected.length && item.storeIds.every((storeId) => state.selectedStoreIds.has(storeId))
  );
  return preset ? {mode:"preset", preset, storeIds:preset.storeIds} : {mode:"invalid", storeIds:selected};
}

function storeSelectionHelp(rule = currentStoreSelectionRule()) {
  if (rule.mode === "preset") return `已选择 ${rule.preset.label}：${rule.preset.description}`;
  if (rule.mode === "single_store") return "单店上架：只生成这一家店的商品卡。";
  if (rule.mode === "invalid") return "多店仅允许组合 1256 或 V346；其他情况请只选一家店。";
  return "请选择一家店，或直接选择跨主体组合 1256 / V346。";
}

function refreshStoreSelectionUi() {
  const rule = currentStoreSelectionRule();
  const invalid = rule.mode === "invalid";
  root.querySelectorAll("[data-product-store]").forEach((input) => {
    input.checked = state.selectedStoreIds.has(input.dataset.productStore);
  });
  const note = root.querySelector("[data-store-selection-help]");
  if (note) {
    note.textContent = storeSelectionHelp(rule);
    note.classList.toggle("error", invalid);
  }
  const button = root.querySelector('[data-action="run-product"]');
  if (button) {
    const readyToUpload = String(state.currentProduct?.status?.status || "").toUpperCase() === "WAITING_MANUAL_REVIEW";
    const blocked = (state.currentProduct?.stores || []).some((shop) =>
      state.selectedStoreIds.has(shop.id) && (!shop.enabled || shop.connection_status !== "connected")
    );
    button.textContent = readyToUpload
      ? `确认修改并立即上传（${state.selectedStoreIds.size} 家店铺）`
      : `运行任务（${state.selectedStoreIds.size} 家店铺）`;
    button.disabled = blocked || invalid || !state.selectedStoreIds.size || state.draftSaveFailed;
  }
  const saveStores = root.querySelector('[data-action="save-product-stores"]');
  if (saveStores) saveStores.disabled = !state.selectedStoreIds.size || invalid;
  const summary = root.querySelector("details.store-selector summary");
  if (summary) summary.textContent = `上传至 ${state.selectedStoreIds.size} 家店铺`;
}

function isNeedsAttentionStatus(value) {
  return ["NEEDS_ATTENTION", "FAILED"].includes(String(value || "").toUpperCase());
}

function isTerminalStatus(value) {
  return ["CREATED", "UPLOADED", "ACTIVE"].includes(String(value || "").toUpperCase());
}

function isPipelineRunningLike(product = {}) {
  if (product && product.batch_running === false) return false;
  const raw = String(product?.status?.status || product?.raw_status || "").toUpperCase();
  if (isTerminalStatus(raw) || isNeedsAttentionStatus(raw) || ["COLLECTED", "STOPPED", "WAITING_MANUAL_REVIEW", "CATEGORY_MATCHED", "CONTENT_GENERATED", "IMAGES_GENERATED", "PRICED", "PARTIAL"].includes(raw)) return false;
  if (["QUEUED", "PROCESSING", "UPLOADING"].includes(raw)) return true;
  const status = product.status || {};
  const failedStep = String(status.failed_step || "").toLowerCase();
  const currentStep = String(status.current_step || product.current_step || "").toLowerCase();
  const nextAction = String(status.next_action || product.next_action || "").toLowerCase();
  const activeSteps = new Set(["product_analysis", "category_match", "variant_rules", "measurements", "offer_exists_check", "upload_feasibility", "product_positioning", "ecommerce_design", "russian_copy", "field_completion", "image_plan", "image_generation", "image_qc", "ozon_upload"]);
  return failedStep !== "unknown" && failedStep !== "" ? false : (activeSteps.has(currentStep) || activeSteps.has(nextAction));
}

function continuationStatusTitle(product = {}) {
  const raw = String(product?.status?.status || product?.raw_status || "").toUpperCase();
  if (raw === "PARTIAL") return "部分店铺未提交";
  if (raw === "STOPPED") return "已安全停止，可继续";
  if (["CATEGORY_MATCHED", "CONTENT_GENERATED", "IMAGES_GENERATED", "PRICED"].includes(raw)) return "等待继续生成";
  if (product.primary_action?.label) return product.primary_action.label;
  return "等待下一步";
}

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
  systemStatus: null,
  systemStatusTimer: null,
  marketRanking: "hot",
  marketCategory: "home",
  marketPeriod: 30,
  marketPage: 1,
  marketQuery: "",
  marketSearchTimer: null,
  financeTab: "overview",
  financeStoreId: "all",
  financeCurrency: "CNY",
  financePeriod: "current_month",
  financeDateFrom: null,
  financeDateTo: null,
  financeImportFileName: null,
  financeImportContent: null,
  financeImportPreview: null,
  pendingErrorFocus: null,
  executionPlan: null,
  queueSummary: null,
  productsCache: {query:"", fetchedAt:0, data:null, promise:null},
  inboxPollTimer: null,
  inboxPollBusy: false,
  inboxLastSyncedAt: null,
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

function uiErrorMessage(raw) {
  const text = String(raw || "");
  const lower = text.toLowerCase();
  if (lower.includes("failed to fetch") || lower.includes("networkerror") || lower.includes("connection refused")) {
    return "主电脑工作台没有回应：确认工作台服务正在运行后再试。";
  }
  if (lower.includes("timed out") || lower.includes("timeout")) {
    return "这一步等待时间太长：已保留已完成内容，可以稍后重试失败步骤。";
  }
  if (lower.includes("untrusted_origin")) {
    return "当前页面没有工作台访问权限，请从主电脑地址打开工作台。";
  }
  return text || "操作没有完成，请查看商品的处理原因。";
}

function workbenchDeviceIdentity() {
  let deviceId = localStorage.getItem("cafDeviceId") || "";
  if (!deviceId) {
    deviceId = globalThis.crypto?.randomUUID?.() || `device-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
    localStorage.setItem("cafDeviceId", deviceId);
  }
  return {id: deviceId, name: localStorage.getItem("cafDeviceName") || ""};
}

async function api(url, options = {}) {
  const device = workbenchDeviceIdentity();
  const headers = {"Content-Type": "application/json", ...(options.headers || {})};
  headers["X-Factory-Device-Id"] = device.id;
  if (device.name) headers["X-Factory-Device-Name"] = device.name;
  let response;
  try {
    response = await fetch(url, {...options, headers});
  } catch (error) {
    throw new Error(uiErrorMessage(error.message));
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : data.detail?.message;
    const fallback = response.status >= 500
      ? `主电脑处理出错（${response.status}），商品没有提交，请稍后重试。`
      : `操作未完成（${response.status}），请检查商品状态后重试。`;
    throw new Error(uiErrorMessage(detail || data.message || fallback));
  }
  return data;
}

function selectedStoreIdsFromProduct(product) {
  return Object.entries(product?.publications?.stores || {})
    .filter(([, record]) => record?.selected)
    .map(([storeId, record]) => String(record.store_id || storeId || "").trim())
    .filter(Boolean);
}

function runAutoUploadForProduct(product) {
  const rawProductStatus = String(product?.status?.status || product?.raw_status || "").toUpperCase();
  const readyToUpload = rawProductStatus === "WAITING_MANUAL_REVIEW";
  const resumeFailure = isNeedsAttentionStatus(rawProductStatus);
  const failedStep = String(product?.status?.failed_step || product?.status?.current_step || product?.current_step || "");
  const resumeUploadFailure = resumeFailure && ["ozon_upload", "manual_ozon_upload"].includes(failedStep);
  return readyToUpload || resumeUploadFailure || Boolean(state.workbenchSettings.auto_mode_enabled);
}

async function startProductRun(productId, button = null) {
  if (!productId) return toast("没有选中商品，无法继续", "error");
  if (state.draftSaveFailed && state.currentProductId === productId) return toast("草稿保存失败，已阻止继续", "error");
  const originalText = button?.textContent || "";
  let buttonLocked = false;
  try {
    await loadWorkbenchSettings();
    const currentDetail = state.currentProduct?.product_id === productId ? state.currentProduct : null;
    const product = currentDetail || await api(`/api/workbench/products/${productId}`);
    const missingSkuRefs = skusNeedingReferenceBinding(product).filter((sku) => sku.binding_required);
    if (productSkuReferenceBlocked(product) && missingSkuRefs.length) {
      if (state.view !== "review" || state.currentProductId !== productId) {
        state.currentProductId = productId;
        await navigate("review", {productId});
      }
      root.querySelector('[data-future-review-tab="sku"]')?.click();
      root.querySelector("[data-sku-reference-binding-board]")?.scrollIntoView({block:"center", behavior:"smooth"});
      toast(`先绑定 ${missingSkuRefs.length} 个SKU参考图，绑定完再继续生成。`, "info", 5200);
      return;
    }
    if (button) {
      button.disabled = true;
      button.classList.add("uploading");
      button.textContent = "正在启动";
      buttonLocked = true;
    }
    const selectedStores = currentDetail && state.selectedStoreIds.size
      ? [...state.selectedStoreIds]
      : selectedStoreIdsFromProduct(product);
    const overrides = currentDetail ? collectStoreOverrides() : {};
    const autoUpload = runAutoUploadForProduct(product);
    const result = await api(`/api/workbench/products/${productId}/run`, {
      method: "POST",
      body: JSON.stringify({store_ids: selectedStores, overrides, auto_upload: autoUpload}),
    });
    if (result.status === "awaiting_confirmation") {
      toast("请先确认类目、SKU、店铺和价格", "success");
      state.confirmationBatchId = result.batch_id;
      state.confirmationData = null;
      state.confirmationProductId = productId;
      return navigate("confirm", {batchId: result.batch_id});
    }
    const message = result.status === "queued"
      ? `任务已排队：${result.batch_id}，前面还有 ${Math.max((result.queue_position || 1) - 1, 0)} 个任务`
      : result.status === "already_queued"
      ? `该商品已在任务中：${result.batch_id}`
      : result.priority_upload
      ? result.message || `已设为优先上传：${result.batch_id}`
      : result.resumed_from_checkpoint
      ? `已继续当前商品：${result.batch_id}`
      : `任务已启动：${result.batch_id}`;
    toast(message, "success");
    state.inboxFilter = ["already_queued", "queued", "started"].includes(result.status) ? "运行中" : "全部";
    if (state.view === "review" && state.currentProductId === productId) {
      await refreshCurrentProduct();
      return;
    }
    await loadProducts(searchInput.value, {force:true});
    return navigate("inbox");
  } catch (error) {
    toast(error.message, "error", 7000);
    if (button && buttonLocked) {
      button.disabled = false;
      button.classList.remove("uploading");
      button.textContent = originalText || "继续";
    }
  }
}

function riskPill(risk) {
  const labels = {high: "严重问题", medium: "需注意", low: "已检查"};
  return `<span class="status-pill ${escapeHtml(risk?.level || "low")}">${labels[risk?.level] || "已检查"}</span>`;
}

function statePill(value) {
  const map = {"完成":"completed", "失败":"failed", "处理中":"processing", "等待上传":"processing", "待处理":""};
  return `<span class="status-pill ${map[value] || ""}">${escapeHtml(value)}</span>`;
}

function setHeading(view) {
  const [title, subtitle] = viewMeta[view] || viewMeta.home;
  document.getElementById("page-title").textContent = title;
  document.getElementById("page-subtitle").textContent = subtitle;
  document.querySelectorAll(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === (view === "confirm" ? "batches" : view)));
}

function setViewSyncing(syncing) {
  root.classList.toggle("view-syncing", Boolean(syncing));
}

async function navigate(view, options = {}) {
  state.view = view;
  document.body.dataset.activeView = view;
  if (view !== "inbox") clearTimeout(state.inboxPollTimer);
  setHeading(view);
  if (!root.children.length) {
    root.innerHTML = `<div class="empty-state"><strong>正在读取真实数据</strong><span>请稍候</span></div>`;
  }
  setViewSyncing(true);
  const renderers = {home: renderHome, review: renderReview, confirm: renderBatchConfirmation, inbox: renderInbox, waiting: renderWaitingUpload, market: renderMarket, finance: renderFinance, attention: renderAttention, listed: renderListed, batches: renderBatches, images: renderImages, risks: renderRisks, shops: renderShops, skills: renderSkills, experience: renderExperience, logs: renderLogs, settings: renderSettings};
  try {
    await (renderers[view] || renderHome)(options);
  } catch (error) {
    if (root.querySelector(".empty-state") && root.textContent.includes("正在读取真实数据")) {
      root.innerHTML = `<div class="empty-state"><strong>页面读取失败</strong><span>${escapeHtml(error.message)}</span></div>`;
    }
    toast(error.message, "error", 7000);
  } finally {
    setViewSyncing(false);
  }
  if (window.innerWidth <= 700) shell.classList.remove("mobile-nav-open");
}

function invalidateProductsCache() {
  state.productsCache = {query:"", fetchedAt:0, data:null, promise:null};
}

async function loadProducts(query = "", options = {}) {
  const normalizedQuery = String(query || "");
  const now = Date.now();
  const cache = state.productsCache || {};
  const cacheTtlMs = options.ttlMs ?? 12000;
  const cacheFresh = cache.data && cache.query === normalizedQuery && now - cache.fetchedAt < cacheTtlMs;
  if (!options.force && cacheFresh) {
    state.products = cache.data.items || [];
    state.executionPlan = cache.data.execution_plan || null;
    state.queueSummary = cache.data.queue_summary || null;
    return cache.data;
  }
  if (!options.force && cache.promise && cache.query === normalizedQuery) return cache.promise;
  const promise = api(`/api/workbench/products?page_size=100&q=${encodeURIComponent(normalizedQuery)}`).then((data) => {
    state.productsCache = {query:normalizedQuery, fetchedAt:Date.now(), data, promise:null};
    state.products = data.items;
    state.executionPlan = data.execution_plan || null;
    state.queueSummary = data.queue_summary || null;
    state.products.forEach(maybeNotifyListingSuccess);
    if (!state.currentProductId || !state.products.some((item) => item.product_id === state.currentProductId)) {
      state.currentProductId = state.products[0]?.product_id || null;
    }
    return data;
  }).catch((error) => {
    state.productsCache = {...state.productsCache, promise:null};
    throw error;
  });
  state.productsCache = {query:normalizedQuery, fetchedAt:cache.fetchedAt || 0, data:cache.data || null, promise};
  return promise;
}

async function loadWorkbenchSettings() {
  state.workbenchSettings = await api("/api/workbench/settings");
  const checkbox = document.getElementById("global-auto-mode");
  if (checkbox) { checkbox.checked = Boolean(state.workbenchSettings.auto_mode_enabled); checkbox.disabled = !state.session?.can_manage_settings; }
  const label = document.getElementById("global-mode-label");
  const note = document.getElementById("global-mode-note");
  if (label) label.textContent = state.workbenchSettings.auto_mode_enabled ? "自动连续流程" : "手动检查";
  if (note) note.textContent = state.workbenchSettings.auto_mode_enabled ? "运行后自动生成并上传" : "最终由你确认上传";
  return state.workbenchSettings;
}

async function loadSession() {
  state.session = await api("/api/workbench/session");
  const operator = state.session.operator || {};
  document.getElementById("operator-name").textContent = operator.device_name || operator.display_name || "工作室电脑";
  document.getElementById("operator-role").textContent = state.session.can_manage_settings ? "主电脑 · 可管理设置" : "共享工作台 · 操作自动留痕";
  const foot = document.querySelector(".sidebar-foot-label");
  if (foot) foot.textContent = operator.device_name || operator.display_name || "工作室电脑";
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
          if (item.type === "question") {
            state.inboxFilter = "需处理";
            navigate("inbox");
          } else {
            navigate("review", {productId:item.product_id});
          }
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

async function pollSystemStatus({notify = true} = {}) {
  try {
    const previous = state.systemStatus?.state;
    const previousSeerfarJobId = state.systemStatus?.seerfar_login?.job_id || "";
    const previousSeerfarStalled = Boolean(state.systemStatus?.seerfar_worker?.stalled);
    const status = await api("/api/workbench/system-status");
    state.systemStatus = status;
    const host = document.getElementById("image-host-status");
    if (host) {
      host.dataset.state = status.state;
      host.title = `${status.label}：${status.message}`;
    }
    document.getElementById("image-host-label").textContent = status.label;
    document.getElementById("image-host-note").textContent = status.active_worker_count
      ? `${status.active_worker_count} 个生图进程 · ${status.message}`
      : status.message;
    if (
      notify && previous && previous !== "needs_attention" && status.state === "needs_attention"
      && "Notification" in window && Notification.permission === "granted"
    ) {
      new Notification("JLC Global 需要处理", {body:status.message, tag:"caf-image-host"});
    }
    const seerfarLogin = status.seerfar_login || {};
    if (seerfarLogin.required && seerfarLogin.job_id && seerfarLogin.job_id !== previousSeerfarJobId) {
      const message = "Seerfar 登录已失效，请在 Chrome 的 Seerfar 页面重新登录；登录后会自动继续关键词查询。";
      toast(message, "error", 12000);
      if (notify && "Notification" in window && Notification.permission === "granted") {
        new Notification("JLC Global：Seerfar 需要重新登录", {body:message, tag:`caf-seerfar-login:${seerfarLogin.job_id}`});
      }
    }
    const seerfarWorker = status.seerfar_worker || {};
    if (seerfarWorker.stalled && !previousSeerfarStalled) {
      const message = seerfarWorker.message || "Seerfar 关键词队列已停止，请重新加载插件并刷新 Seerfar 页面。";
      toast(message, "error", 15000);
      if (notify && "Notification" in window && Notification.permission === "granted") {
        new Notification("JLC Global：Seerfar 已停止", {body:message, tag:"caf-seerfar-worker-stalled"});
      }
    }
    return status;
  } catch (_) {
    const host = document.getElementById("image-host-status");
    if (host) host.dataset.state = "needs_attention";
    document.getElementById("image-host-label").textContent = "主机无响应";
    document.getElementById("image-host-note").textContent = "启动器会尝试恢复工作台服务";
    return null;
  }
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
  return `<div class="product-menu"><button class="menu-trigger" data-menu-product="${productId}" aria-label="商品操作" title="商品操作"><span class="ph ph-dots-three" aria-hidden="true"></span></button><div class="menu-popover"><button class="menu-delete" data-delete-product="${productId}"><span class="ph ph-trash" aria-hidden="true"></span>彻底删除</button></div></div>`;
}

function stepLabel(step) {
  return STEP_LABELS[String(step || "")] || "等待任务状态";
}

function aiServiceWaiting(product) {
  return String(product?.status?.ai_service_state || "normal") === "waiting_for_recovery";
}

function aiUsageLimitWaiting(product) {
  return String(product?.status?.ai_service_reason || "unknown") === "codex_usage_limit";
}

function friendlyErrorInfo(product) {
  const status = product?.status || product || {};
  if (product?.error) return product.error;
  const raw = String(status.error_message || "任务没有完成");
  const text = raw.toLowerCase();
  const step = String(status.failed_step || status.current_step || "");
  const issueSummary = status.ozon_issue_summary || status.ozon?.issue_summary || {};
  const issueBucket = String(issueSummary.primary_bucket || "").toLowerCase();
  const result = {
    title: "商品处理没有完成",
    message: "这件商品在当前步骤没有完成，前面已经生成的内容会保留。点“立即修改”检查后再继续。",
    action: "检查并修改", tab: "risk", technical: raw, step,
  };
  if (issueSummary.has_issues) {
    result.title = issueSummary.primary_label || "Ozon 上传问题";
    result.message = issueSummary.message || raw;
    result.action = "查看上传问题";
    result.tab = "store";
    if (issueBucket === "image_link") {
      result.action = "重传图片";
      result.tab = "images";
    } else if (issueBucket === "numeric_contract") {
      result.action = "修正属性";
      result.tab = "category";
    } else if (issueBucket === "logistics_weight") {
      result.action = "修正尺寸重量";
      result.tab = "price";
    } else if (issueBucket === "description_decline") {
      result.action = "修正文案";
      result.tab = "content";
    } else if (issueBucket === "category_mismatch") {
      result.action = "修改类目";
      result.tab = "category";
    } else if (issueBucket === "duplicate_spu") {
      result.action = "处理重复";
      result.tab = "store";
    } else if (issueBucket === "store_auth") {
      result.action = "检查店铺";
      result.tab = "store";
    }
  } else if (String(status.ai_service_state || "normal") === "waiting_for_recovery") {
    const usageLimit = String(status.ai_service_reason || "unknown") === "codex_usage_limit";
    result.title = usageLimit ? "AI额度等待中" : "正在等待联网大模型恢复";
    result.message = usageLimit
      ? "AI额度暂时不可用，任务已停在当前步骤。商品断点和已完成结果都已保留，额度恢复后会自动继续。"
      : "任务已停在当前步骤，系统会自动重试。已完成内容和断点都已保留，不会使用本地备用分析，也不需要你手工操作。";
    result.action = "查看当前进度";
  } else if (text.includes("failed to fetch") || text.includes("connection")) {
    result.title = "主电脑工作台没有回应";
    result.message = "连接主电脑失败。先确认工作台服务正在运行，再点“重试”；不会重复上传商品。";
    result.action = "重试任务";
  } else if (productSkuReferenceBlocked({status})) {
    result.title = "SKU缺少参考图";
    result.message = "当前SKU没有可用于生图的真实参考图。请从本商品已采集图片中绑定一张，绑定完再继续生成。";
    result.action = "绑定SKU图"; result.tab = "sku";
  } else if (text.includes("image") || ["image_generation", "image_qc", "image_plan"].includes(step)) {
    result.title = "图片步骤没有完成";
    result.message = "部分图片没有生成或质检未通过，已完成图片会保留。进入“图片”页，只重做失败图片即可。";
    result.action = "修改图片"; result.tab = "images";
  } else if (["attribute", "required", "dictionary", "6383", "field_completion"].some((token) => text.includes(token)) || ["field_completion", "category_match", "variant_rules"].includes(step)) {
    result.title = "类目属性需要修改";
    result.message = "有类目属性没有填对。进入“类目”，修改带“必须填写”或错误提示的字段；可选字段不影响继续。";
    result.action = "修改类目属性"; result.tab = "category";
  } else if (["price", "pricing", "selling_price", "measurements"].some((token) => text.includes(token)) || step === "measurements") {
    result.title = "价格或尺寸需要修改";
    result.message = "售价、重量或尺寸资料不完整。进入“价格”或“SKU”，修改后再继续。";
    result.action = "修改价格或尺寸"; result.tab = "price";
  } else if (["upload", "offer", "duplicate", "pending", "store"].some((token) => text.includes(token)) || ["ozon_upload", "offer_exists_check", "upload_feasibility"].includes(step)) {
    result.title = "上传前还差一步";
    result.message = uploadErrorMessage(text, step);
    result.action = "查看上传问题"; result.tab = uploadErrorTab(text, step);
  } else if (["codex", "403", "429", "analysis"].some((token) => text.includes(token)) || ["product_analysis", "product_positioning", "russian_copy", "ecommerce_design"].includes(step)) {
    result.title = "商品资料生成没有完成";
    result.message = "商品资料生成遇到问题。已保留采集内容，进入“资料”页检查标题、卖点和简介后再继续。";
    result.action = "修改商品资料"; result.tab = "content";
  }
  return result;
}

function productSkuReferenceBlocked(product) {
  const status = product?.status || {};
  const step = String(status.failed_step || status.current_step || "").toLowerCase();
  const text = `${status.error_message || ""} ${status.human_message || ""}`.toLowerCase();
  return step === "image_source_preflight"
    || text.includes("sku参考图预检")
    || text.includes("缺少真实参考图")
    || text.includes("image_source_preflight")
    || text.includes("missing_required_sku_reference")
    || text.includes("has no registered sku reference")
    || text.includes("no registered sku-bound real image");
}

function uploadErrorTab(text = "", step = "") {
  if (["image", "detail", "main", "n+8", "缺图", "图片"].some((token) => text.includes(token))) return "images";
  if (["store", "shop", "店铺", "credential", "connection", "config"].some((token) => text.includes(token))) return "store";
  if (["price", "dimension", "weight", "尺寸", "重量", "价格"].some((token) => text.includes(token))) return "price";
  if (["attribute", "field", "category", "类目", "属性"].some((token) => text.includes(token))) return "category";
  return "store";
}

function uploadErrorMessage(text = "", step = "") {
  const has = (tokens) => tokens.some((token) => text.includes(token));
  if (has(["image", "detail image", "main image", "n+8", "缺图", "图片"])) {
    return "图片没有齐：请先生成或替换缺失图片。图片齐了以后，再点上传。";
  }
  if (has(["store", "shop", "credential", "connection", "config", "店铺", "凭证", "连接"])) {
    return "店铺没有准备好：请检查目标店铺是否已选择、已验证、仍然可用。";
  }
  if (has(["duplicate", "already", "task_id", "offer exists", "repeated", "重复", "已提交"])) {
    return "这件商品可能已经提交过：系统先拦住重复上传，避免在 Ozon 创建第二份。";
  }
  if (has(["pending", "unclear", "unknown remote", "状态不明确", "处理中"])) {
    return "上一次提交状态不清楚：先不要重复上传，查看诊断信息后再处理。";
  }
  if (has(["attribute", "field", "category", "类目", "属性", "required"])) {
    return "平台必填资料没有齐：请先补类目属性或商品资料。";
  }
  if (has(["price", "dimension", "weight", "尺寸", "重量", "价格"])) {
    return "价格、重量或尺寸不完整：请先补齐这些基础资料。";
  }
  return "上传没有完成。点商品进入诊断信息，查看具体原因和可修改位置。";
}

function errorFocusForProduct(product, preferredStep = "") {
  const error = preferredStep ? friendlyErrorInfo({status:{...(product?.status || {}), current_step: preferredStep, failed_step: preferredStep, error_message: product?.status?.error_message}}) : friendlyErrorInfo(product);
  const raw = `${error.technical || ""} ${product?.status?.error_message || ""} ${preferredStep}`.toLowerCase();
  let selector = null;
  if (error.tab === "content") selector = raw.includes("title") ? '[data-draft-field="title_ru"]' : '[data-draft-field="description_ru"]';
  if (error.tab === "price") selector = raw.includes("sku") || raw.includes("price") ? "[data-sku-price]" : '[data-future-review-pane="price"]';
  if (error.tab === "category") {
    const missing = (product?.attributes?.missing_required_attributes || [])[0];
    selector = missing?.attribute_id != null ? `[data-attribute-id="${String(missing.attribute_id).replace(/"/g, "\\\"")}"]` : "[data-attribute-id]";
  }
  if (error.tab === "images") selector = "#image-workspace";
  if (error.tab === "store") selector = '[data-future-review-pane="store"]';
  return {tab: error.tab || "risk", selector, error};
}

function focusPendingError() {
  const target = state.pendingErrorFocus;
  if (!target) return;
  state.pendingErrorFocus = null;
  const tab = root.querySelector(`[data-future-review-tab="${target.tab}"]`);
  tab?.click();
  requestAnimationFrame(() => {
    const element = target.selector ? root.querySelector(target.selector) : null;
    const pane = element || root.querySelector(`[data-future-review-pane="${target.tab}"]`);
    pane?.scrollIntoView({behavior:"smooth", block:"center"});
    if (element && typeof element.focus === "function") {
      element.focus({preventScroll:true});
      element.classList.add("error-edit-target");
      setTimeout(() => element.classList.remove("error-edit-target"), 2400);
    }
  });
}

async function openProductErrorEditor(productId, preferredStep = "") {
  const product = await api(`/api/workbench/products/${encodeURIComponent(productId)}`);
  state.currentProductId = productId;
  state.currentImageSlot = null;
  state.pendingErrorFocus = errorFocusForProduct(product, preferredStep);
  toast("已刷新商品状态，正在定位需要修改的位置", "info", 2400);
  await navigate("review", {productId});
  focusPendingError();
}

function liveProgressText(product) {
  if ((state.queueSummary?.priority_product_ids || []).includes(product.product_id)) {
    return state.queueSummary.message || "已进入优先上传队列";
  }
  const snapshot = product.pipeline_progress || {};
  const step = String(snapshot.step || product.status?.next_action || product.status?.current_step || "queue");
  if (aiServiceWaiting(product)) {
    return `${aiUsageLimitWaiting(product) ? "AI额度等待" : "等待联网大模型恢复"} · ${stepLabel(step)}`;
  }
  const generated = (product.images || []).filter((item) => item.url).length;
  const total = (product.images || []).length;
  if (step === "image_generation" && Number(snapshot.planned_image_slots || 0)) {
    const active = (snapshot.active_image_slots || []).slice(0, 3).join("、");
    const activeText = active ? ` · 正在跑 ${active}` : "";
    return `${snapshot.step_label || stepLabel(step)} · 生成 ${snapshot.generated_image_slots || generated}/${snapshot.planned_image_slots} · 通过 ${snapshot.completed_image_slots || 0}/${snapshot.planned_image_slots}${activeText}`;
  }
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
  const priorityUpload = (state.queueSummary?.priority_product_ids || []).includes(product.product_id);
  const rawError = product.status?.error_message && product.status.error_message !== "unknown";
  const snapshot = product.pipeline_progress || {};
  const extra = renderPipelineProgressMeta(snapshot);
  const statusText = priorityUpload
    ? "上传确认已记录；系统只会提交一次，并继续保留防重复与库存禁用门禁。"
    : aiServiceWaiting(product)
    ? "已暂停当前步骤，系统会自动重试；不会使用本地备用分析。"
    : rawError ? friendlyErrorInfo(product).message : (snapshot.status_note || "后台正在处理，页面会自动更新");
  return `<div class="live-progress" id="live-progress"><div class="live-progress-head"><strong data-progress-step>${escapeHtml(liveProgressText(product))}</strong><span data-progress-value>${progress}%</span></div><div class="progress-track"><span data-progress-bar style="width:${progress}%"></span></div><small data-progress-status>${escapeHtml(statusText)}</small><div class="live-progress-meta" data-progress-meta>${extra}</div></div>`;
}

function renderPipelineProgressMeta(snapshot = {}) {
  const items = [];
  const prefix = snapshot.is_running === false ? "下一步" : "当前";
  if (snapshot.step_label) items.push(`${prefix}：${snapshot.step_label}`);
  if (Number(snapshot.planned_image_slots || 0)) {
    items.push(`图片 ${snapshot.generated_image_slots || 0}/${snapshot.planned_image_slots}`);
    items.push(`通过 ${snapshot.completed_image_slots || 0}/${snapshot.planned_image_slots}`);
  }
  if ((snapshot.active_image_slots || []).length) {
    items.push(`进行中：${snapshot.active_image_slots.slice(0, 3).join("、")}`);
  }
  const activeElapsed = snapshot.is_running === false ? "" : elapsedDuration(snapshot.active_step?.started_at);
  if (activeElapsed) items.push(`已运行 ${activeElapsed}`);
  if (typeof snapshot.latest_step_duration_seconds === "number") {
    items.push(`本步 ${formatDuration(snapshot.latest_step_duration_seconds)}`);
  } else if (typeof snapshot.last_recorded_duration_seconds === "number") {
    items.push(`上步 ${formatDuration(snapshot.last_recorded_duration_seconds)}`);
  }
  return items.map((item) => `<span>${escapeHtml(item)}</span>`).join("");
}

function elapsedDuration(value) {
  const timestamp = Date.parse(value || "");
  if (!Number.isFinite(timestamp)) return "";
  return formatDuration(Math.max(0, Math.floor((Date.now() - timestamp) / 1000)));
}

function pipelineFailureEvents(product) {
  const status = product?.status || {};
  const seen = new Set();
  const events = [];
  const push = (event) => {
    const step = event.step || event.name || "unknown";
    const reason = String(event.reason || event.message || event.error_message || event.technical || "").trim();
    if (!reason || reason === "unknown") return;
    const key = `${step}|${reason}|${event.occurred_at || event.at || ""}`;
    if (seen.has(key)) return;
    seen.add(key);
    events.push({
      step,
      title: event.title || stepLabel(step),
      reason,
      occurred_at: event.occurred_at || event.finished_at || event.at || "",
      retryable: event.retryable !== false,
      technical: event.technical || reason,
      level: event.level || "high",
    });
  };
  (status.steps || []).forEach((step) => {
    const failed = String(step.status || "").toLowerCase().includes("fail") || step.error;
    if (!failed) return;
    const error = typeof step.error === "string" ? {reason: step.error} : (step.error || {});
    push({
      step: error.step || step.name,
      title: stepLabel(error.step || step.name),
      reason: error.reason || step.reason || step.message || status.error_message,
      occurred_at: error.occurred_at || step.finished_at || step.started_at,
      retryable: step.retryable,
      technical: error.reason || step.error || status.error_message,
    });
  });
  if (status.error_message && status.error_message !== "unknown") {
    push({
      step: status.failed_step !== "unknown" ? status.failed_step : status.current_step,
      reason: status.error_message,
      occurred_at: status.updated_at || status.completed_at,
      retryable: true,
    });
  }
  (status.warning_history || []).forEach((entry) => {
    (entry.warnings || []).forEach((warning) => push({
      step: entry.batch_id || "warning",
      title: "历史异常",
      reason: warning,
      occurred_at: entry.archived_at,
      retryable: true,
      level: "medium",
    }));
  });
  (status.warnings || []).forEach((warning) => push({
    step: status.current_step || "warning",
    title: "当前异常",
    reason: warning,
    occurred_at: status.updated_at || status.started_at,
    retryable: true,
    level: "medium",
  }));
  (status.ozon?.errors || []).forEach((error) => push({
    step: "ozon_upload",
    title: "Ozon上传错误",
    reason: typeof error === "string" ? error : (error.message || error.reason || JSON.stringify(error)),
    occurred_at: error.occurred_at || error.at,
    retryable: true,
  }));
  if (product?.production_readiness?.blocking) {
    push({
      step: "formal_input",
      title: "输入资料不符合当前流程",
      reason: product.production_readiness.message || "当前商品不是本次工作台采集的正式输入",
      retryable: false,
    });
  }
  return events;
}

function renderFailureReasonList(product, options = {}) {
  const events = pipelineFailureEvents(product);
  if (!events.length) return "";
  const title = options.title || "步骤失败原因";
  const subtitle = options.subtitle || "每条都来自本商品状态、步骤记录或上传返回；点击修改会先刷新状态再定位到对应面板。";
  const rowMarkup = events.map((event) => `<article class="failure-reason-row ${escapeHtml(event.level)}">
      <span class="failure-step">${escapeHtml(event.title)}</span>
      <div><strong>${escapeHtml(event.reason)}</strong><small>${escapeHtml(event.occurred_at ? dateText(event.occurred_at) : "时间未记录")} · ${event.retryable ? "可修复后继续" : "不可跳过"}</small></div>
      <button type="button" class="secondary-button" data-error-edit="${escapeHtml(product.product_id)}" data-error-step="${escapeHtml(event.step)}">修改</button>
    </article>`).join("");
  if (options.collapsed) {
    return `<section class="failure-reason-board compact collapsed">
    <header><div><span>历史异常</span><h3>${escapeHtml(title)}</h3><p>${escapeHtml(subtitle)}</p></div><strong>${events.length}</strong></header>
    <details class="failure-reason-details"><summary>展开查看历史异常，不影响当前看图和上传</summary><div class="failure-reason-list">${rowMarkup}</div></details>
  </section>`;
  }
  return `<section class="failure-reason-board ${options.compact ? "compact" : ""}">
    <header><div><span>当前诊断</span><h3>${escapeHtml(title)}</h3><p>${escapeHtml(subtitle)}</p></div><strong>${events.length}</strong></header>
    <div class="failure-reason-list">${rowMarkup}</div>
  </section>`;
}

function isTerminalProduct(product = state.currentProduct) {
  return isTerminalStatus(product?.status?.status);
}

function blockTerminalProductMutation() {
  if (!isTerminalProduct()) return false;
  toast("该商品已经提交 Ozon，本地记录只读；后续请在 Ozon 商品卡后台修改", "info");
  return true;
}

async function renderReview(options = {}) {
  await loadProducts(options.query || "", {force:Boolean(options.force)});
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
  const waitingForAi = aiServiceWaiting(product);
  state.selectedStoreIds = new Set(Object.values(product.publications?.stores || {}).filter((item) => item.selected).map((item) => item.store_id));
  const rawStatus = String(product.status?.status || "").toUpperCase();
  const priorityUpload = (state.queueSummary?.priority_product_ids || []).includes(product.product_id);
  const terminalPublication = isTerminalStatus(rawStatus);
  const formalInputBlocker = Boolean(product.production_readiness?.blocking);
  // 断点状态不是运行状态。只有真实 worker/队列状态才锁定按钮；否则
  // CONTENT_GENERATED 等状态会把“继续生成”误判为正在运行。
  // 只有真实排队/处理/上传状态才锁定按钮；类目修改会把旧 active_step 清掉，
  // 即使历史状态残留，也不能把 COLLECTED 商品误显示成“正在运行”。
  const activeProductRunning = isPipelineRunningLike(product);
  const running = activeProductRunning || priorityUpload;
  const readyToUpload = rawStatus === "WAITING_MANUAL_REVIEW";
  const failed = isNeedsAttentionStatus(rawStatus);
  const failure = failed ? friendlyErrorInfo(product) : null;
  const skuReferenceBlocked = failed && productSkuReferenceBlocked(product);
  const missingSkuReferenceCount = skusNeedingReferenceBinding(product).filter((sku) => sku.binding_required).length;
  const needsContinuation = ["PARTIAL", "CATEGORY_MATCHED", "CONTENT_GENERATED", "IMAGES_GENERATED", "PRICED", "STOPPED"].includes(rawStatus);
  const blockedSelectedStores = (product.stores || []).filter((shop) =>
    state.selectedStoreIds.has(shop.id) && (!shop.enabled || shop.connection_status !== "connected")
  );
  const selectedStoreBlocker = !terminalPublication && blockedSelectedStores.length > 0;
  const canRunProduct = !terminalPublication && !formalInputBlocker && ["run", "fix", "review_upload"].includes(product.primary_action?.key);
  const needsStoreSelection = true;
  state.currentImageSlot = state.currentImageSlot && product.images.some((item) => item.slot === state.currentImageSlot) ? state.currentImageSlot : product.images[0]?.slot || null;
  const primaryText = terminalPublication
    ? "已上架"
    : readyToUpload
    ? `确认修改并立即上传（${state.selectedStoreIds.size} 家店铺）`
    : running
      ? `后台生成中 · ${product.progress}%`
    : skuReferenceBlocked && missingSkuReferenceCount
      ? `绑定 ${missingSkuReferenceCount} 个SKU图`
    : failed
      ? `从${stepLabel(product.status?.failed_step || product.status?.current_step)}继续`
      : product.primary_action?.key === "run" || product.primary_action?.key === "fix"
      ? `${product.primary_action.label}${needsStoreSelection ? `（${state.selectedStoreIds.size} 家店铺）` : ""}`
      : product.primary_action?.label || "查看状态";
  root.innerHTML = `<article class="future-review-shell">
    <header class="preview-toolbar future-review-toolbar">
      <button class="preview-back" data-go="inbox"><span class="ph ph-arrow-left" aria-hidden="true"></span>返回采集箱</button>
      <div class="preview-status">${riskPill(product.risk)} ${statePill(product.public_state)}<span>${escapeHtml(product.product_id)}</span></div>
      <span class="preview-toolbar-spacer"></span>
      ${renderReviewStoreSelector(product)}
      ${activeProductRunning ? `<button class="safe-stop-button" data-batch-action="stop" data-batch-action-source="manual_toolbar_v2"><span class="ph ph-stop-circle" aria-hidden="true"></span>手动停止</button>` : ""}
      <button class="preview-delete" data-delete-product="${product.product_id}"><span class="ph ph-trash" aria-hidden="true"></span>彻底删除</button>
    </header>
    <div class="future-review-alerts">
      ${product.handoff_message ? `<section class="task-summary task-handoff-summary"><div><span class="success-kicker">等待Ozon处理</span><h2>正在生成商品卡</h2><p>${escapeHtml(product.handoff_message)}</p></div></section>` : ""}
      ${priorityUpload && !product.handoff_message ? `<section class="task-summary task-upload-priority"><div><span class="priority-kicker">优先上传</span><h2>${escapeHtml(state.queueSummary?.message || "上传任务已置顶")}</h2><p>当前生成任务完成后再处理本商品；不会中断正在上架的任务，也不会重复提交已完成内容。</p></div></section>` : ""}
      ${formalInputBlocker ? `<section class="task-summary task-failure-summary"><div><span class="error-kicker">输入资料不符合当前流程</span><h2>这件商品不能继续运行或上传</h2><p>${escapeHtml(product.production_readiness?.message || "请重新从工作台采集为新商品")}</p></div></section>` : ""}
      ${product.pending_question?.question ? `<section class="task-summary"><div><h2>需要你确认一个关键问题</h2><p>${escapeHtml(product.pending_question.question)}</p></div><button class="primary-button" data-primary-action="answer" data-product-id="${product.product_id}">回答问题</button></section>` : ""}
      ${failed ? `<section class="task-summary task-failure-summary"><div><span class="error-kicker">${escapeHtml(failure.title)}</span><h2>${skuReferenceBlocked ? "先绑定SKU参考图" : `停在${escapeHtml(stepLabel(product.status?.failed_step || product.status?.current_step))} · ${product.progress}%`}</h2><p>${escapeHtml(failure.message)}</p><details class="error-technical"><summary>查看诊断信息</summary><code>${escapeHtml(failure.technical)}</code></details></div><div class="error-actions"><button class="${skuReferenceBlocked ? "primary-button" : "secondary-button"}" data-action="edit-error">${skuReferenceBlocked ? "立即绑定SKU图" : "立即修改"}</button>${skuReferenceBlocked && missingSkuReferenceCount ? "" : `<button class="primary-button" data-action="run-product">继续生成</button>`}</div></section>` : ""}
      ${renderSkuReferenceBindingPanel(product, {compact:false, onlyMissing:true})}
      ${selectedStoreBlocker ? `<section class="task-summary task-failure-summary"><div><span class="error-kicker">店铺连接需要处理</span><h2>${blockedSelectedStores.length} 家已选店铺暂时不能上传</h2><p>${escapeHtml(blockedSelectedStores.map((shop) => `${shop.display_name}：${storeConnectionIssue(shop)}`).join("；"))}</p></div><div class="error-actions"><button class="secondary-button" data-future-review-tab="store">查看店铺状态</button></div></section>` : ""}
      ${running ? renderLiveProgress(product) : ""}
    </div>
    <div class="future-review-grid simplified-review-grid">
      ${renderFutureImageStage(product)}
      ${renderFutureInspector(product)}
    </div>
    <footer class="preview-submit-bar future-command-dock">
      <div class="future-dock-status"><span class="future-dock-check ph ${selectedStoreBlocker || formalInputBlocker || failed || needsContinuation ? "ph-warning" : "ph-check"}" aria-hidden="true"></span><div><strong>${product.handoff_message ? "等待Ozon生成商品卡" : priorityUpload ? "优先上传已接受" : waitingForAi ? "等待联网大模型恢复" : running ? "商品正在制作" : formalInputBlocker ? "输入资料需重新采集" : failed ? "商品需要修复" : selectedStoreBlocker ? "先修复店铺连接" : readyToUpload ? "等待确认上传" : continuationStatusTitle(product)}</strong><small>${product.handoff_message ? "任务号已保存 · 可只读查询结果 · 禁止重复提交" : priorityUpload ? "等待安全切换 · 上传同时1件 · 库存不会提交" : `${escapeHtml(stepLabel(product.status?.current_step))} · ${product.progress}% · 库存不会提交`}</small></div></div>
      <span id="save-state" class="preview-save-state">${product.draft.saved_at ? `修改已自动保存 v${product.draft.version}` : "当前为AI初稿"}</span>
      ${running
        ? `<div class="future-running-indicator" role="status" aria-live="polite"><span class="ph ph-spinner-gap" aria-hidden="true"></span><span><strong>${escapeHtml(primaryText)}</strong><small>正在${escapeHtml(stepLabel(product.status?.current_step))}，页面会自动更新</small></span></div>`
        : `<button class="preview-primary" data-action="${skuReferenceBlocked && missingSkuReferenceCount ? "edit-error" : "run-product"}" ${!canRunProduct || selectedStoreBlocker || (needsStoreSelection && !state.selectedStoreIds.size) || state.draftSaveFailed ? "disabled" : ""}><span class="ph ${skuReferenceBlocked && missingSkuReferenceCount ? "ph-image" : readyToUpload ? "ph-storefront" : "ph-play"}" aria-hidden="true"></span>${escapeHtml(primaryText)}</button>`}
    </footer>
  </article>`;
  state.lastImageSignature = imageSignature(product);
  startReviewPolling();
}

function renderFutureFlow(product) {
  const completed = new Set(product.status?.completed_steps || []);
  const failedSteps = new Set(pipelineFailureEvents(product).map((item) => item.step));
  const current = String(product.status?.current_step || "queue");
  const rawStatus = String(product.status?.status || "").toUpperCase();
  const running = isPipelineRunningLike(product);
  const visualCurrent = rawStatus === "WAITING_MANUAL_REVIEW" ? "manual_ozon_upload" : current;
  const groups = [
    ["01", "采集与确认", "1688资料与SKU", ["collect_source", "validate_source"]],
    ["02", "商品资料", "分析、俄文、属性", ["product_analysis", "category_match", "variant_rules", "ecommerce_design", "russian_copy", "field_completion"]],
    ["03", "定价计算", `${product.skus?.length || 0} 个独立售价`, ["measurements", "product_positioning"]],
    ["04", "图片制作", `${product.images?.length || 0} 个图片槽位`, ["image_plan", "image_generation"]],
    ["05", "图片质检", "图片技术检查", ["image_qc"]],
    ["06", "Ozon上架", `${product.publication_summary?.selected || 0} 家目标店铺`, ["ozon_upload", "manual_ozon_upload"]],
  ];
  const thumbnailUrl = product.images?.find((item) => item.url)?.url || "";
  return `<aside class="future-flow-panel">
    <div class="future-flow-head"><span>Production flow</span><strong>${Math.round(Number(product.progress) || 0)}%</strong></div>
    <div class="future-flow-progress" aria-label="商品制作进度 ${product.progress}%"><span style="width:${Math.max(0, Math.min(100, Number(product.progress) || 0))}%"></span></div>
    <div class="future-flow-product">${thumbnailUrl ? `<img src="${escapeHtml(thumbnailUrl)}" alt="">` : `<span class="ph ph-image" aria-hidden="true"></span>`}<div><strong>${escapeHtml(product.product_id)}</strong><small>${product.skus?.length || 0} SKU · ${escapeHtml(product.public_state)}</small></div><span class="ph ph-check-circle" aria-hidden="true"></span></div>
    <nav class="future-flow-steps" aria-label="商品制作步骤">${groups.map(([index, title, meta, stepNames]) => {
      const done = stepNames.some((step) => completed.has(step)) || Number(product.progress) === 100;
      const active = stepNames.includes(visualCurrent);
      const failed = stepNames.some((step) => failedSteps.has(step));
      const spinning = running && active;
      return `<button type="button" class="${done ? "done" : ""} ${active ? "active" : ""} ${failed ? "failed" : ""}" data-future-flow-step="${escapeHtml(failed ? [...failedSteps].find((step) => stepNames.includes(step)) : stepNames[0])}"><span class="future-flow-index">${index}</span><span><strong>${title}</strong><small>${failed ? "有失败记录，点这里查看" : escapeHtml(meta)}</small></span><i class="ph ${failed ? "ph-warning-circle" : done ? "ph-check" : spinning ? "ph-spinner-gap" : active ? "ph-hand" : "ph-circle"}" aria-hidden="true"></i></button>`;
    }).join("")}</nav>
    <button type="button" class="future-timeline-button" data-future-review-tab="risk"><span class="ph ph-clock-counter-clockwise" aria-hidden="true"></span>查看问题与时间线<span class="ph ph-arrow-right" aria-hidden="true"></span></button>
  </aside>`;
}

function renderFutureImageStage(product) {
  const images = product.images || [];
  const readOnly = isTerminalProduct(product);
  const image = images.find((item) => item.slot === state.currentImageSlot) || images[0] || null;
  const currentIndex = Math.max(0, images.findIndex((item) => item.slot === image?.slot));
  const currentVisual = image?.url
    ? `<img class="future-hero-image" src="${escapeHtml(image.url)}" alt="${escapeHtml(image.slot)}" data-open-image="${escapeHtml(image.url)}">`
    : `<div class="future-image-empty"><span class="ph ph-image-square" aria-hidden="true"></span><strong>${escapeHtml(image?.slot || "图片待生成")}</strong><small>${escapeHtml(image?.state || "WAITING")}</small></div>`;
  return `<section class="future-image-stage" id="image-workspace" data-image-slot="${escapeHtml(image?.slot || "")}">
    <div class="future-image-toolbar"><div><span class="future-status-dot"></span><strong>${escapeHtml(image?.state || "WAITING")}</strong><small>${currentIndex + 1} / ${Math.max(images.length, 1)}</small></div><div><button type="button" data-image-cycle="prev" aria-label="上一张图片"><span class="ph ph-arrow-left"></span></button><button type="button" data-image-cycle="next" aria-label="下一张图片"><span class="ph ph-arrow-right"></span></button></div></div>
    <article class="future-image-canvas">
      <div class="future-image-meta"><span>${escapeHtml(image?.type || "商品图片")}</span><strong>${escapeHtml(image?.slot || "等待生成")}</strong></div>
      ${currentVisual}
      ${image?.url ? `<button type="button" class="future-image-zoom" data-open-image="${escapeHtml(image.url)}"><span class="ph ph-eye" aria-hidden="true"></span>查看大图</button>` : ""}
      <input type="file" accept="image/*" data-replace-file hidden ${readOnly ? "disabled" : ""}>
      <div class="prompt-editor"><textarea data-prompt-input placeholder="${readOnly ? "已提交 Ozon，本地只读" : "输入本张图片的修改意见"}" ${readOnly ? "disabled" : ""}>${escapeHtml(image?.prompt || "")}</textarea><button type="button" data-image-action="queue-prompt" ${readOnly ? "disabled" : ""}>应用意见并重生成</button></div>
    </article>
    <div class="future-image-filmstrip">${images.map((item, index) => `<button type="button" class="${item.slot === image?.slot ? "active" : ""}" data-image-select="${escapeHtml(item.slot)}" aria-label="查看${escapeHtml(item.slot)}">${item.url ? `<img src="${escapeHtml(item.url)}" alt="">` : `<span class="ph ph-image" aria-hidden="true"></span>`}<small>${escapeHtml(imageStripLabel(item, index))}</small></button>`).join("") || `<span class="future-filmstrip-empty">图片生成后显示在这里</span>`}</div>
    <div class="future-image-commands">
      <button type="button" data-image-action="regenerate" ${image && !readOnly ? "" : "disabled"}><span class="ph ph-arrow-clockwise"></span>重新生成图片</button>
      <button type="button" data-image-action="replace" ${image && !readOnly ? "" : "disabled"}><span class="ph ph-upload-simple"></span>替换图片</button>
      <button type="button" data-open-image="${escapeHtml(image?.url || "")}" ${image?.url ? "" : "disabled"}><span class="ph ph-eye"></span>查看大图</button>
    </div>
  </section>`;
}

function imageStripLabel(item, index) {
  const slot = String(item?.slot || "").toLowerCase();
  const type = String(item?.type || "").toLowerCase();
  if (type.includes("main") || type.includes("主") || slot.startsWith("main") || slot.includes("variant-main")) return "主";
  const detailNumber = slot.match(/detail[-_]?0*([0-9]+)/);
  if (detailNumber) return String(Number(detailNumber[1])).padStart(2, "0");
  return String(index + 1).padStart(2, "0");
}

function renderImageAssetBuckets(product) {
  const assets = product.image_assets || {};
  const legacySubmitted = product.production_readiness?.state === "legacy_submitted_read_only";
  const groups = [
    ["original", "原始素材", legacySubmitted ? "历史商品原始资料，仅供查看" : "只来自本商品本次工作台采集，AI不会覆盖"],
    ["candidate", "生成候选", legacySubmitted ? "旧流程生成结果，仅供查看，不会再次提交" : "技术质检通过后自动用于上传；不需要逐张确认"],
    ["rejected", "已拒绝", "不会回流为输入或当前有效图片"],
  ];
  if (legacySubmitted || (assets.accepted || []).length) {
    groups.push(["accepted", "历史确认副本", legacySubmitted ? "旧流程已经提交，不需要补做新版确认" : "旧流程人工确认记录；新流程不再要求逐张确认"]);
  }
  return `<div class="asset-bucket-grid">${groups.map(([key, title, note]) => {
    const items = assets[key] || [];
    return `<section class="asset-bucket asset-bucket-${key}"><header><div><strong>${title}</strong><small>${note}</small></div><span>${items.length}</span></header><div class="asset-bucket-images">${items.map((item) => `<button type="button" data-open-image="${escapeHtml(item.url)}" title="${escapeHtml(item.path)}"><img src="${escapeHtml(item.url)}" alt="${escapeHtml(item.name)}"><small>${escapeHtml(item.name)}</small></button>`).join("") || `<p>暂无图片</p>`}</div></section>`;
  }).join("")}</div>`;
}

function renderFutureInspector(product) {
  const content = product.content || {};
  const tags = content.tags || [];
  const pricing = product.pricing?.sku_pricing || [];
  const score = product.prelisting_assessment || {};
  const riskItems = product.risk?.items || [];
  const failureBoard = renderFailureReasonList(product, {compact:true, title:"步骤失败原因", subtitle:"按步骤列出失败、异常和上传返回。"});
  const readOnly = isTerminalProduct(product);
  const disabled = readOnly ? "disabled" : "";
  return `<aside class="future-inspector">
    <nav class="future-inspector-tabs" role="tablist" aria-label="商品资料面板">
      ${[["content","资料"],["images","图片"],["sku","SKU"],["price","价格"],["category","类目"],["store","店铺"],["risk","诊断"]].map(([key,label], index) => `<button type="button" role="tab" aria-selected="${index === 0 ? "true" : "false"}" class="${index === 0 ? "active" : ""}" data-future-review-tab="${key}">${label}</button>`).join("")}
    </nav>
    <div class="future-inspector-scroll">
      <section class="future-inspector-pane active" data-future-review-pane="content">
        <div class="future-pane-heading"><div><span>Product copy</span><h2>商品资料</h2></div><span class="future-save-badge"><span class="ph ${readOnly ? "ph-lock" : "ph-cloud-check"}"></span>${readOnly ? "已提交 · 只读" : "自动保存"}</span></div>
        <label class="future-title-field"><span>俄文实际上传标题</span><input class="preview-title-input" data-draft-field="title_ru" value="${escapeHtml(content.title_ru || product.source.title_cn)}" ${disabled}></label>
        <div class="future-reference-copy"><span>中文参考</span><p>${escapeHtml(content.title_zh_reference || product.source.title_cn)}</p></div>
        ${renderPreviewProductInfo(product)}
        <label class="future-description-field"><span>俄文实际上传简介</span><textarea data-draft-field="description_ru" ${disabled}>${escapeHtml(content.description_ru || "")}</textarea></label>
        <div class="future-reference-copy"><span>中文参考</span><p>${escapeHtml(content.description_zh_reference || "暂无中文参考")}</p></div>
        <div class="future-tags"><div><span>主题标签</span><strong>${tags.length}/30</strong></div><div class="preview-tags">${tags.map((tag, index) => `<span>${escapeHtml(tag)}<button data-remove-tag="${index}" aria-label="删除标签" ${disabled}><span class="ph ph-x" aria-hidden="true"></span></button></span>`).join("")}</div><div class="tag-add preview-tag-add"><input id="new-tag" maxlength="30" placeholder="${readOnly ? "已提交 Ozon，本地只读" : "输入俄文标签"}" ${disabled}><button data-action="add-tag" ${disabled}>添加</button></div></div>
        ${renderSuggestions(product.ai_suggestions || [], readOnly)}
      </section>
      <section class="future-inspector-pane hidden" data-future-review-pane="images">
        <div class="future-pane-heading"><div><span>Image package</span><h2>图片包</h2></div><span class="future-count-badge">${product.images?.length || 0} 张</span></div>
        <div class="visual-preference"><label><span>整套图片风格意见（可选）</span><input id="visual-set-hint" maxlength="120" value="${escapeHtml(product.visual_preference?.set_hint || "")}" placeholder="${readOnly ? "已提交 Ozon，本地只读" : "例如：更明亮、更科技感、户外感更强"}" ${disabled}></label><button class="secondary-button" data-action="save-visual-preference" ${disabled}>应用到整套图片</button></div>
        <div class="future-safety-note"><span class="ph ph-shield-check"></span><div><strong>保持商品真实性</strong><p>单图重做只生成新版本，不改变结构、颜色、SKU差异或配件数量。</p></div></div>
        ${renderImageAssetBuckets(product)}
      </section>
      <section class="future-inspector-pane hidden" data-future-review-pane="sku"><div class="future-pane-heading"><div><span>Variants</span><h2>SKU与售价</h2></div><span class="future-count-badge">${product.skus?.length || 0} 个</span></div>${renderSkuReferenceBindingPanel(product, {compact:true})}${renderPreviewSkus(product, readOnly)}${renderPerStorePrices(product, readOnly)}</section>
      <section class="future-inspector-pane hidden" data-future-review-pane="price"><div class="future-pane-heading"><div><span>Pricing</span><h2>定价与评分</h2></div><span class="future-count-badge">${pricing.length} 个SKU</span></div><div class="future-score-card">${renderPrelistingScore(score)}</div><div class="future-pricing-list">${pricing.map(pricingRows).join("") || `<p class="form-help">尚未生成定价结果</p>`}</div></section>
      <section class="future-inspector-pane hidden" data-future-review-pane="category"><div class="future-pane-heading"><div><span>Category</span><h2>类目与属性</h2></div><button class="secondary-button" data-action="change-category" ${!readOnly && (product.status?.status === "COLLECTED" || isNeedsAttentionStatus(product.status?.status)) && Number(product.status?.api_write_count || 0) === 0 ? "" : "disabled"}>修改类目</button></div>${renderPreviewAttributes(product, readOnly)}</section>
      <section class="future-inspector-pane hidden" data-future-review-pane="store"><div class="future-pane-heading"><div><span>Publication</span><h2>店铺发布状态</h2></div><span class="future-count-badge">${product.publication_summary?.selected || 0} 家</span></div>${renderPublicationMatrix(product)}</section>
      <section class="future-inspector-pane hidden" data-future-review-pane="risk"><div class="future-pane-heading"><div><span>Diagnostics</span><h2>诊断信息</h2></div><span class="future-count-badge">${riskItems.length} 项</span></div>${failureBoard}${renderAnalysisSummary(product.analysis || {})}<div class="future-risk-list">${riskItems.map((item) => `<article class="future-risk-row ${escapeHtml(item.level || "low")}"><span class="ph ${item.level === "high" ? "ph-warning-circle" : "ph-shield-check"}"></span><div><strong>${escapeHtml(item.title || (item.level === "high" ? "严重问题" : "检查项"))}</strong><p>${escapeHtml(item.message)}</p>${item.technical ? `<details class="error-technical"><summary>查看诊断信息</summary><code>${escapeHtml(item.technical)}</code></details>` : ""}</div>${item.code === "pipeline_failed" ? `<button class="secondary-button" data-action="edit-error">立即修改</button>` : ""}</article>`).join("") || `<div class="future-safety-note"><span class="ph ph-shield-check"></span><div><strong>没有需要处理的问题</strong><p>真实性、包装关系和库存禁用规则检查通过。</p></div></div>`}</div></section>
    </div>
  </aside>`;
}

function renderPreviewGallery(product) {
  return product.images.map((image, index) => {
    const src = image.url
      ? `<img src="${image.url}" alt="${escapeHtml(image.slot)}" data-open-image="${image.url}">`
      : `<div class="preview-image-empty"><strong>${escapeHtml(image.slot)}</strong><span>${image.state === "WAITING" ? "等待生成" : "正在生成"}</span></div>`;
    return `<article class="preview-image-card ${index === 0 ? "preview-main-image" : ""}" draggable="true" data-image-slot="${escapeHtml(image.slot)}">
      ${src}<span class="preview-image-state">${escapeHtml(image.state)}</span>
      <div class="preview-image-actions"><button data-image-action="regenerate">重做</button><button data-image-action="replace">替换</button><button data-image-action="move-up">前移</button><button class="danger-mini" data-image-action="delete">拒绝此图</button></div>
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
  const skuDimensions = firstSku.dimensions_cm || {};
  const dimensionValues = [
    skuDimensions.length ?? productDimensions.length_cm ?? productDimensions.length,
    skuDimensions.width ?? productDimensions.width_cm ?? productDimensions.width,
    skuDimensions.height ?? productDimensions.height_cm ?? productDimensions.height,
  ];
  const dimensionUnit = productDimensions.unit || "cm";
  const dimensionsText = dimensionValues.some((value) => value !== undefined && value !== null)
    ? `${dimensionValues.map((value) => display(value)).join(" × ")} ${dimensionUnit}`
    : display(dimensions);
  const productWeight = facts.weight?.product_value_g ?? facts.weight?.product?.value_g ?? facts.weight?.value_g ?? facts.weight;
  const weightText = typeof productWeight === "number" ? `${number(productWeight, 0)} 克` : display(productWeight);
  const categoryPath = (product.category.category_path_zh || product.category.category_path || []).join(" › ") || display(product.category.category_name_zh || product.category.category_name);
  return `<div class="preview-field-list">
    <div><span>类目和类型</span><strong>${escapeHtml(categoryPath)}</strong></div>
    <div><span>默认人民币售价</span><strong>${product.skus?.length ? `¥${number(product.skus[0].selling_price_cny, 2)}` : "尚未生成"}</strong></div>
    <div><span>货号</span><strong>${escapeHtml(display(firstSku.offer_id, product.product_id))}</strong></div>
    <div><span>Ozon商品编号</span><strong>${escapeHtml(display(product.ozon?.product_id))}</strong></div>
    <div><span>商品重量</span><strong>${escapeHtml(weightText)}</strong></div>
    <div><span>长 × 宽 × 高</span><strong>${escapeHtml(dimensionsText)}</strong></div>
  </div>`;
}

function renderPreviewAttributes(product, readOnly = false) {
  const attrs = [...(product.attributes?.attributes || [])];
  const required = attrs.filter((item) => item.required);
  const optional = attrs.filter((item) => !item.required);
  const optionalFilled = optional.filter((item) => attributeHasValue(item.value));
  const optionalEmpty = optional.filter((item) => !attributeHasValue(item.value));
  return `<div class="preview-attribute-group"><h3>必填属性 <span>${required.length}项</span></h3>${required.map((item) => previewAttributeRow(item, readOnly)).join("") || `<p class="preview-empty-copy">当前类目没有额外必填属性</p>`}</div>
    <details class="preview-more-attributes"><summary>更多属性（已填写 ${optionalFilled.length} / ${optional.length}）</summary><div>
      ${optionalFilled.map((item) => previewAttributeRow(item, readOnly)).join("") || `<p class="preview-empty-copy">暂时没有可安全自动填写的可选属性</p>`}
      ${optionalEmpty.length ? `<details class="preview-empty-attributes"><summary>未提供或不适用（${optionalEmpty.length}项${readOnly ? "" : "，可手动补充"}）</summary><div>${optionalEmpty.map((item) => previewAttributeRow(item, readOnly)).join("")}</div></details>` : ""}
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

function previewAttributeRow(item, readOnly = false) {
  const value = attributeDisplayValue(item.value);
  const allowed = item.allowed_values || [];
  const control = allowed.length
    ? `<select data-attribute-id="${item.attribute_id}" ${readOnly ? "disabled" : ""}><option value="">请选择</option>${allowed.map((entry) => `<option value="${escapeHtml(entry.value)}" ${String(entry.value) === String(value) ? "selected" : ""}>${escapeHtml(entry.value)}</option>`).join("")}</select>`
    : `<input data-attribute-id="${item.attribute_id}" value="${escapeHtml(value)}" placeholder="尚未填写" ${readOnly ? "disabled" : ""}>`;
  return `<label class="preview-attribute-row"><span><strong>${escapeHtml(item.attribute_name_zh || item.attribute_name || item.attribute_id)}${item.required ? " *" : ""}</strong><small>${escapeHtml(item.attribute_name || "Ozon字段")}</small></span>${control}<em>${value ? escapeHtml(attributeSourceLabel(item)) : item.required ? "必须填写" : "未提供"}</em></label>`;
}

function skuReferenceBindingCandidates(product) {
  return product?.sku_image_binding_candidates || state.currentProduct?.sku_image_binding_candidates || [];
}

function skuReferenceCandidateCards(product, skuId, selectedPath = "", {disabled = false, compact = false} = {}) {
  const candidates = skuReferenceBindingCandidates(product);
  if (!candidates.length) return `<div class="sku-reference-candidate-empty">当前商品没有可绑定图片</div>`;
  return `<div class="sku-reference-candidate-grid ${compact ? "compact" : ""}" role="list" aria-label="可绑定SKU参考图">${candidates.map((item, index) => {
    const label = item.label || item.display_source || item.path || `采集图 ${index + 1}`;
    const source = item.display_source || (item.image_type === "main" ? "1688主图" : item.image_type === "detail" ? "1688详情图" : "SKU图");
    const selected = item.path === selectedPath;
    const image = item.url
      ? `<img src="${escapeHtml(item.url)}" alt="${escapeHtml(label)}" loading="lazy">`
      : `<span class="sku-reference-candidate-placeholder">无图</span>`;
    return `<button type="button" class="sku-reference-candidate ${selected ? "selected" : ""}" data-sku-image-binding-choice="${escapeHtml(skuId)}" data-selected-image-path="${escapeHtml(item.path || "")}" aria-pressed="${selected ? "true" : "false"}" title="${escapeHtml(label)}" ${disabled || !item.path ? "disabled" : ""}>${image}<span>${escapeHtml(label)}</span><small>${escapeHtml(source)}</small></button>`;
  }).join("")}</div>`;
}

function skusNeedingReferenceBinding(product) {
  return (product?.skus || []).filter((sku) => sku.binding_required || sku.image_binding);
}

function renderSkuReferenceBindingPanel(product, {compact = false, onlyMissing = false} = {}) {
  const rows = skusNeedingReferenceBinding(product).filter((sku) => !onlyMissing || sku.binding_required);
  if (!rows.length) return "";
  const missingCount = rows.filter((sku) => sku.binding_required).length;
  const candidates = skuReferenceBindingCandidates(product);
  const candidateHint = candidates.length
    ? `可从 ${candidates.length} 张本商品已采集图片中选择`
    : "当前商品没有可绑定的采集图片";
  return `<section class="sku-reference-binding-board ${compact ? "compact" : ""}" data-sku-reference-binding-board>
    <header>
      <div><span>SKU参考图</span><h3>${missingCount ? `${missingCount} 个SKU缺少参考图` : "SKU参考图已绑定"}</h3><p>从本商品本次采集的主图、详情图或SKU图里选一张。绑定后你再点“继续生成”，系统从当前断点恢复。</p></div>
      <div class="sku-reference-binding-actions"><strong>${escapeHtml(candidateHint)}</strong><button type="button" class="secondary-button" data-future-review-tab="sku">去绑定SKU图</button></div>
    </header>
    <div class="sku-reference-binding-list">${rows.map((sku) => {
      const binding = sku.image_binding || null;
      const selectedPath = binding?.selected_image_path || "";
      return `<article class="sku-reference-binding-row">
        <div class="sku-reference-product">
          ${sku.image_url ? `<img src="${escapeHtml(sku.image_url)}" alt="">` : `<span>缺图</span>`}
          <div><strong>${escapeHtml(display(sku.option_text || sku.name || sku.sku_id))}</strong><small>SKU ${escapeHtml(display(sku.sku_id))}${binding ? " · 已绑定参考图" : " · 缺SKU图"}</small></div>
        </div>
        ${skuReferenceCandidateCards(product, sku.sku_id, selectedPath, {compact})}
      </article>`;
    }).join("")}</div>
  </section>`;
}

function previewSkuReferenceCell(product, sku, readOnly = false) {
  const binding = sku.image_binding || null;
  const candidates = skuReferenceBindingCandidates(product);
  const selectedPath = binding?.selected_image_path || "";
  const imageUrl = sku.image_url || "";
  const status = sku.binding_required
    ? "缺SKU图"
    : binding
      ? "已绑定参考图"
      : "SKU原图";
  return `<div class="preview-sku-reference-cell">
    ${imageUrl ? `<img src="${escapeHtml(imageUrl)}" alt="">` : `<span>缺图</span>`}
    <small class="${sku.binding_required ? "missing" : binding ? "bound" : "ok"}">${status}</small>
    ${(sku.binding_required || binding) ? skuReferenceCandidateCards(product, sku.sku_id, selectedPath, {disabled: readOnly || !candidates.length, compact: true}) : ""}
  </div>`;
}

function renderPreviewSkus(product, readOnly = false) {
  return `<div class="preview-sku-table"><div class="preview-sku-head"><span>参考图</span><span>SKU</span><span>规格</span><span>采购价</span><span>人民币售价${readOnly ? "" : "（可修改）"}</span><span>约合卢布</span></div>${(product.skus || []).map((sku) => `<div class="preview-sku-row">${previewSkuReferenceCell(product, sku, readOnly)}<strong>${escapeHtml(display(sku.sku_id))}</strong><span>${escapeHtml(display(sku.option_text || sku.name))}</span><span>¥${number(sku.purchase_price_cny, 2)}</span><label>¥<input type="number" min="0.01" step="0.01" data-sku-price="${escapeHtml(sku.sku_id)}" value="${typeof sku.selling_price_cny === "number" ? sku.selling_price_cny : ""}" ${readOnly ? "disabled" : ""}></label><span>₽${number(sku.selling_price_rub, 0)}</span></div>`).join("")}</div>`;
}

function renderPerStorePrices(product, readOnly = false) {
  const selected = (product.stores || []).filter((shop) => state.selectedStoreIds.has(shop.id));
  if (!selected.length) return "";
  const rate = Number(product.workbench_settings?.fixed_cny_to_rub || state.workbenchSettings.fixed_cny_to_rub || 12);
  return `<details class="preview-store-prices"><summary>按店铺修改售价（可选）</summary><p>默认所有店铺使用上面的统一售价；这里只填写有差异的店铺。</p>${selected.map((shop) => {
    const publication = product.publications?.stores?.[shop.id] || {};
    const bySku = Object.fromEntries((publication.sku_publications || []).map((item) => [String(item.sku_id), item]));
    return `<div class="preview-store-price-group"><h3>${escapeHtml(shop.display_name)}</h3>${(product.skus || []).map((sku) => {
      const saved = bySku[String(sku.sku_id)]?.price_override_cny;
      return `<label><span>${escapeHtml(display(sku.option_text || sku.sku_id))}</span><span>¥ <input type="number" min="0.01" step="0.01" data-store-price-cny="${escapeHtml(shop.id)}" data-store-price-sku="${escapeHtml(sku.sku_id)}" value="${typeof saved === "number" ? saved : ""}" placeholder="统一价 ${number(sku.selling_price_cny, 2)}" ${readOnly ? "disabled" : ""}><small>${typeof saved === "number" ? `约 ₽${number(saved * rate, 0)}` : "留空使用统一价"}</small></span></label>`;
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
  const rawStatus = String(product.status?.status || "").toUpperCase();
  if (isTerminalStatus(rawStatus)) {
    return `<div class="store-selector store-selector-readonly"><span>已提交至 ${product.publication_summary?.selected || state.selectedStoreIds.size} 家店铺</span><small>只保留记录，不会再次提交</small></div>`;
  }
  const options = stores.map((shop) => {
    const available = shop.enabled && shop.connection_status === "connected";
    const selected = state.selectedStoreIds.has(shop.id);
    return `<label class="store-option ${available ? "" : "unavailable"}"><input type="checkbox" data-product-store="${escapeHtml(shop.id)}" ${selected ? "checked" : ""} ${available || selected ? "" : "disabled"}><span><strong>${escapeHtml(shop.display_name)}</strong><small>${storeStatusLabel(shop.connection_status)} · ${shop.credentials_display}${shop.last_validation_error ? ` · ${escapeHtml(storeConnectionIssue(shop))}` : ""}</small></span>${available ? "" : `<span class="status-pill medium">不可上传</span>`}</label>`;
  }).join("");
  const rule = currentStoreSelectionRule();
  const presets = STORE_GROUP_PRESETS.map((preset) => {
    const unavailable = preset.storeIds.filter((storeId) => {
      const shop = stores.find((item) => item.id === storeId);
      return !shop || !shop.enabled || shop.connection_status !== "connected";
    });
    const selected = rule.mode === "preset" && rule.preset.id === preset.id;
    return `<button class="store-group-preset ${selected ? "selected" : ""}" data-action="select-store-group" data-store-group="${preset.id}" type="button" ${unavailable.length ? "disabled" : ""}><strong>${preset.label}</strong><small>${escapeHtml(preset.description)}${unavailable.length ? " · 有店铺不可用" : ""}</small></button>`;
  }).join("");
  return `<details class="store-selector"><summary>上传至 ${state.selectedStoreIds.size} 家店铺</summary><div class="store-selector-popover"><div class="store-group-presets"><p>跨主体组合</p><div>${presets}</div><small data-store-selection-help class="${rule.mode === "invalid" ? "error" : ""}">${escapeHtml(storeSelectionHelp(rule))}</small></div><div class="store-options">${options || `<p class="form-help">请先到店铺中心添加并验证店铺</p>`}</div><div class="store-card-actions"><button class="secondary-button" data-action="clear-store-selection" type="button">清空选择</button><button class="primary-button" data-action="save-product-stores" type="button" ${state.selectedStoreIds.size && rule.mode !== "invalid" ? "" : "disabled"}>保存选择</button></div></div></details>`;
}

function storeStatusLabel(value) {
  return ({connected:"已连接", unverified:"未验证", failed:"连接失败", disabled:"已禁用"})[value] || "未验证";
}

function storeConnectionIssue(shop) {
  const raw = String(shop?.last_validation_error || "");
  const lower = raw.toLowerCase();
  if (lower.includes("api-key is deactivated") || lower.includes("api key is deactivated")) {
    return "API密钥已失效，请在主电脑的店铺设置中更换密钥并重新测试连接";
  }
  if (lower.includes("unauthorized") || lower.includes("invalid api")) {
    return "店铺凭证无效，请在主电脑重新填写并测试连接";
  }
  return raw || "请在主电脑重新测试连接";
}

function renderImageWorkspace(product) {
  const passed = product.images.filter((item) => ["PASS", "COMPLETED"].includes(item.state)).length;
  const imageRules = product.image_contract || {};
  const groups = product.image_groups?.length ? product.image_groups : [{
    sku_id: product.skus?.[0]?.sku_id || product.product_id,
    option_text: product.skus?.[0]?.option_text || product.skus?.[0]?.name || "当前商品",
    images: product.images,
    detail_images: product.images.filter((item) => item.type !== "main"),
    main_image_missing: !product.images.some((item) => item.type === "main"),
  }];
  const sharedCount = Math.max(0, ...(groups.map((group) => group.detail_images?.length || 0)));
  const expectedMain = Number(imageRules.expected_main_count ?? groups.length);
  const expectedDetails = Number(imageRules.expected_shared_detail_count ?? 8);
  const expectedTotal = Number(contract.expected_total_count ?? (expectedMain + expectedDetails));
  return `<div class="image-header"><div><h2>${escapeHtml(product.source.title_cn)}</h2><span>${groups.length} 个SKU图片组 · 契约 ${expectedMain} 张SKU主图 + ${expectedDetails} 张共享详情图 = ${expectedTotal} 张 · 当前 ${product.images.length} 张 · ${passed} 张可用 · 3:4</span></div><span>${product.image_qc?.score ? `质检 ${product.image_qc.score} 分` : "等待质检"}</span></div>
    <div class="image-group-guide">每个已选SKU必须且只能有1张自己的主图；固定 ${expectedDetails} 张详情图为商品组共享内容，界面按SKU重复展示但只生成一套文件。</div>
    <div class="sku-image-groups">${groups.map((group, index) => {
      const groupImages = group.images || [];
      return `<section class="sku-image-group">
        <div class="sku-image-group-head"><div><strong>SKU ${index + 1}</strong><span>${escapeHtml(group.option_text || group.sku_name || group.sku_id)}</span></div><small>1 张专属主图 + ${group.detail_images?.length || 0} 张通用详情图</small></div>
        ${group.main_image_missing ? `<div class="sku-main-warning">这个SKU还没有专属主图，完成生图后才能上传。</div>` : ""}
        <div class="image-grid">${groupImages.map(imageTile).join("") || empty("尚未生成图片")}</div>
      </section>`;
    }).join("")}</div>`;
}

function imageTile(image) {
  const stateClass = image.state.toLowerCase();
  const issue = image.issues?.[0] || image.purpose || "等待生成结果";
  const src = image.url ? `<img src="${image.url}" alt="${escapeHtml(image.slot)}" data-open-image="${image.url}">` : `<div class="image-placeholder"><strong>${escapeHtml(image.slot)}</strong><span>${image.state === "WAITING" ? "等待生成" : "正在生成"}</span></div>`;
  return `<article class="image-tile ${state.currentImageSlot === image.slot ? "selected" : ""}" data-image-slot="${escapeHtml(image.slot)}" ${image.product_id ? `data-product-id="${escapeHtml(image.product_id)}"` : ""}>
    <div class="image-frame ${image.state === "GENERATING" || image.state === "RETRYING" ? "generating" : image.state === "WAITING" ? "waiting" : ""}">${src}<span class="image-badge ${stateClass}">${escapeHtml(image.state)}</span></div>
    <div class="image-info"><h3>${escapeHtml(image.slot)} · ${escapeHtml(image.type)}</h3><p>${escapeHtml(issue)}</p>
      <div class="image-actions"><button data-image-action="prompt">单图意见</button><button data-image-action="regenerate">重生成</button>${image.download_url ? `<a href="${image.download_url}">下载</a>` : ""}</div>
      <div class="image-actions-more">${image.url ? `<button data-image-action="copy-url">复制URL</button><button data-image-action="set-main">设为主图</button><button data-image-action="set-detail">设为详情图</button><button data-image-action="move-up">前移</button><button data-image-action="replace">替换</button><button class="danger-mini" data-image-action="delete">拒绝此图</button>` : ""}</div>
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
  const canChangeCategory = (product.status?.status === "COLLECTED" || isNeedsAttentionStatus(product.status?.status)) && Number(product.status?.api_write_count || 0) === 0;
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
    <details class="data-section full-review-only ${fullClass}" ${skuHasDifferences(product.skus) ? "open" : ""}><summary>SKU资料 <span>${product.skus.length} 行 · 单位已统一</span></summary><div class="data-content"><p class="form-help">AI已自动填好；你可以不改直接运行。这里只改当前SKU，不会覆盖其他SKU。</p><div class="table-wrap sku-fact-table-wrap"><table class="sku-table sku-fact-table"><thead><tr><th>图</th><th>1688 SKU</th><th>颜色</th><th>容量（ml）</th><th>SKU规格</th><th>商品重量（g）</th><th>商品长宽高（mm）</th><th>包装重量（g）</th><th>包装长宽高（mm）</th><th>装量（件）</th><th>SKU动态属性</th><th>售价</th><th>变体依据</th></tr></thead><tbody>${product.skus.map(skuRow).join("")}</tbody></table></div></div></details>
    <details class="data-section" open><summary>初始定价 <span>${pricing.length} 个SKU</span></summary><div class="data-content">${pricing.map(pricingRows).join("") || `<div class="summary-row"><span>定价</span><strong>尚未生成</strong></div>`}</div></details>
    <details class="data-section full-review-only ${fullClass}"><summary>Rich Content</summary><div class="data-content"><pre>${escapeHtml(JSON.stringify(product.rich_content || {}, null, 2))}</pre></div></details>
    <details class="data-section" open><summary>评分与价格建议 <span>${score.overall_score || 0}分</span></summary><div class="data-content">${renderPrelistingScore(score)}</div></details>
    <details class="data-section full-review-only ${fullClass}" open><summary>店铺发布状态 <span>${product.publication_summary?.selected || 0} 家</span></summary><div class="data-content">${renderPublicationMatrix(product)}</div></details>
    <details class="data-section"><summary>AI建议 <span>自动采纳</span></summary><div class="data-content">${renderSuggestions(product.ai_suggestions || [])}</div></details>
    <details class="data-section" open><summary>问题与时间线 <span>${product.risk.items.length}</span></summary><div class="data-content">${product.risk.items.map((item) => `<div class="summary-row"><span>${item.level === "high" ? "严重问题" : "普通问题"}</span><strong>${escapeHtml(item.message)}</strong></div>`).join("") || `<div class="summary-row"><span>问题</span><strong>未发现需要处理的问题</strong></div>`}${product.timeline.slice(0, 8).map((item) => `<div class="summary-row"><span>${dateText(item.at)}</span><strong>${escapeHtml(item.message)}</strong></div>`).join("")}</div></details>`;
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
  const failedStoreIds = (product.stores || []).filter((shop) => product.publications?.stores?.[shop.id]?.status === "FAILED").map((shop) => shop.id);
  const retryAll = failedStoreIds.length > 1 ? `<button class="secondary-button retry-failed-stores-button" data-retry-failed-stores="${escapeHtml(failedStoreIds.join(","))}">并行重试全部失败店铺（${failedStoreIds.length}家）</button>` : "";
  const statusLabels = {HANDED_OFF_TO_OZON:"等待Ozon处理", SUCCESS:"已上架", IMPORTED:"已上架", ACTIVE:"已上架", CREATED:"已上架", PENDING_REMOTE:"等待Ozon处理", OZON_MODERATION:"Ozon审核中", UPLOADING:"上传中", QUEUED:"等待上传", FAILED:"上传失败", NOT_SELECTED:"未选择"};
  const terminalStates = new Set(["SUCCESS", "IMPORTED", "ACTIVE", "CREATED"]);
  return `${retryAll}<div class="publication-matrix">${(product.stores || []).map((shop) => { const publication = product.publications?.stores?.[shop.id] || {}; const sku = publication.sku_publications?.[0] || {}; const failed = publication.status === "FAILED"; const statusClass = failed ? "failed" : terminalStates.has(publication.status) ? "completed" : publication.status === "NOT_SELECTED" ? "" : "processing"; const failureReason = publication.last_error && !["unknown", "UNKNOWN"].includes(publication.last_error) ? publication.last_error : "失败原因未记录，请先查看本地日志后再重试"; return `<article class="publication-row"><div class="publication-row-head"><strong>${escapeHtml(shop.display_name)}</strong><span class="status-pill ${statusClass}">${escapeHtml(statusLabels[publication.status] || display(publication.status, "未选择"))}</span></div><p>${escapeHtml(sku.action || publication.action || "UNKNOWN")} · task ${escapeHtml(display(sku.task_id))} · product ${escapeHtml(display(sku.ozon_product_id))}</p>${failed ? `<p class="publication-error"><strong>失败原因：</strong>${escapeHtml(failureReason)}</p>` : ""}${publication.has_store_overrides ? `<p class="locked">该店铺存在专属修改</p>` : ""}${failed ? `<button class="secondary-button retry-store-button" data-retry-store="${escapeHtml(shop.id)}">只重试这家店</button>` : ""}</article>`; }).join("") || `<p class="form-help">尚未配置店铺</p>`}</div>`;
}

function renderSuggestions(items, readOnly = false) {
  return items.map((item) => `<article class="suggestion-card"><strong>${escapeHtml(item.title)}</strong><p>${escapeHtml(item.detail)}</p><small>${readOnly ? "已提交 Ozon，仅保留处理记录" : "系统已自动采纳，无需操作"}</small></article>`).join("") || `<p class="form-help">当前没有需要处理的建议；后续AI建议将自动采用</p>`;
}

function editField(label, name, value, locked, textarea = false) {
  const input = textarea ? `<textarea data-draft-field="${name}">${escapeHtml(value)}</textarea>` : `<input data-draft-field="${name}" value="${escapeHtml(value)}">`;
  return `<label class="field"><span>${label}${locked.has(name) ? `<b class="locked">人工锁定</b>` : ""}</span>${input}</label>`;
}

function attributeRow(item) {
  const status = item.value === "unknown" ? (item.required ? "缺失" : "未知") : "已填写";
  return `<tr><td>${escapeHtml(item.attribute_name || item.attribute_id)}${item.required ? " *" : ""}</td><td><input data-attribute-id="${item.attribute_id}" value="${escapeHtml(item.value === "unknown" ? "" : item.value)}" placeholder="unknown"></td><td>${escapeHtml(display(item.source, "未知"))}</td><td>${status}</td></tr>`;
}

function skuImageCell(sku) {
  const binding = sku.image_binding || null;
  const selectedPath = binding?.selected_image_path || "";
  const showBindingSelect = sku.binding_required || binding;
  const badge = sku.binding_required
    ? `<small class="sku-image-warning">缺SKU图，请绑定</small>`
    : binding
      ? `<small class="sku-image-bound">已绑定参考图</small>`
      : `<small class="sku-image-ok">SKU原图</small>`;
  return `<div class="sku-image-cell">
    ${sku.image_url ? `<img class="sku-fact-image" src="${escapeHtml(sku.image_url)}" alt="">` : `<div class="sku-image-placeholder">缺SKU图</div>`}
    ${badge}
    ${showBindingSelect ? skuReferenceCandidateCards(state.currentProduct, sku.sku_id, selectedPath, {compact: true}) : ""}
  </div>`;
}

function skuRow(sku) {
  const row = sku.sku_row || {};
  return `<tr>
    <td>${skuImageCell(sku)}</td>
    <td><strong>${escapeHtml(display(sku.option_text || sku.name))}</strong><br><small>${escapeHtml(display(sku.sku_id))}</small></td>
    <td>${skuFactInput(sku, "color", skuFactValue(row.color), "颜色")}</td>
    <td>${skuFactInput(sku, "capacity_ml", skuFactValue(row.capacity), "ml", "number")}</td>
    <td>${skuFactInput(sku, "specification_text", skuFactValue(row.specification), "规格")}</td>
    <td>${skuFactInput(sku, "product_weight_g", skuFactValue(row.product_weight), "g", "number")}</td>
    <td>${dimensionInputs(sku, row.product_dimensions, "product")}</td>
    <td>${skuFactInput(sku, "package_weight_g", skuFactValue(row.package_weight), "g", "number")}</td>
    <td>${dimensionInputs(sku, row.package_dimensions, "package")}</td>
    <td>${skuFactInput(sku, "quantity_pcs", skuFactValue(row.quantity), "件", "number")}</td>
    <td>${dynamicAttributeInputs(sku, row.dynamic_attributes)}</td>
    <td><label class="sku-mini-field">¥<input type="number" min="0.01" step="0.01" data-sku-price="${escapeHtml(sku.sku_id)}" value="${typeof sku.selling_price_cny === "number" ? sku.selling_price_cny : ""}"></label><small>₽${number(sku.selling_price_rub, 0)}</small></td>
    <td>${escapeHtml(display(sku.variant_decision))}<br><small>${escapeHtml(display(sku.aspect_basis))}</small></td>
  </tr>`;
}

function skuFactValue(field) {
  if (!field || typeof field !== "object") return "";
  const value = field.canonical_value ?? field.target_value ?? field.value;
  if (value && typeof value === "object") return "";
  return value ?? "";
}

function skuFactDimensionValue(field, key) {
  const value = field?.canonical_value || field?.target_value || field?.value || {};
  return value && typeof value === "object" ? (value[key] ?? "") : "";
}

function skuFactInput(sku, field, value, unit, type = "text") {
  const step = type === "number" ? ` step="0.001" min="0"` : "";
  return `<label class="sku-mini-field"><input type="${type}" data-sku-fact-sku="${escapeHtml(sku.sku_id)}" data-sku-field="${field}" value="${escapeHtml(value)}"${step}><small>${escapeHtml(unit)}</small></label>`;
}

function dimensionInputs(sku, field, prefix) {
  return `<div class="sku-dims">
    ${skuFactInput(sku, `${prefix}_length_mm`, skuFactDimensionValue(field, "length_mm"), "长mm", "number")}
    ${skuFactInput(sku, `${prefix}_width_mm`, skuFactDimensionValue(field, "width_mm"), "宽mm", "number")}
    ${skuFactInput(sku, `${prefix}_height_mm`, skuFactDimensionValue(field, "height_mm"), "高mm", "number")}
  </div>`;
}

function dynamicAttributeInputs(sku, attributes) {
  const entries = Object.entries(attributes || {}).filter(([, value]) => value && typeof value === "object");
  if (!entries.length) return `<small class="muted">按类目字段自动生成</small>`;
  return `<div class="sku-dynamic-attrs">${entries.map(([attributeId, field]) => {
    const name = field.attribute_name || field.name || attributeId;
    const unit = field.canonical_unit || field.target_unit || "属性";
    return `<label class="sku-mini-field"><span>${escapeHtml(name)}</span><input data-sku-fact-sku="${escapeHtml(sku.sku_id)}" data-sku-field="attribute:${escapeHtml(attributeId)}" value="${escapeHtml(skuFactValue(field))}"><small>${escapeHtml(unit)}</small></label>`;
  }).join("")}</div>`;
}

function pricingRows(item) {
  const shipping = item.shipping || {};
  return `<div class="summary-row"><span>SKU</span><strong>${escapeHtml(item.sku_id)}</strong></div><div class="summary-row"><span>采购价 / 运费</span><strong>¥${number(item.purchase_cost_cny, 2)} / ¥${number(shipping.shipping_cost_cny, 2)}</strong></div><div class="summary-row"><span>总成本</span><strong>¥${number(item.base_cost_cny, 2)}</strong></div><div class="summary-row"><span>建议售价</span><strong class="locked">₽${number(item.selling_price_rub, 0)}</strong></div><div class="summary-row"><span>预计利润</span><strong>¥${number(item.estimated_profit_cny, 2)}</strong></div>`;
}

function collectDraftField(field, rawValue) {
  if (field === "bullets_ru") return rawValue.split("\n").map((value) => value.trim()).filter(Boolean);
  return rawValue;
}

function mergeDraftObject(field, existing, value) {
  if (field !== "sku_overrides") {
    return {...(existing || {}), ...value};
  }
  const merged = {...(existing || {})};
  for (const [skuId, skuValues] of Object.entries(value || {})) {
    merged[skuId] = {...(merged[skuId] || {}), ...(skuValues || {})};
  }
  return merged;
}

function scheduleDraftSave(patch) {
  const saveState = document.getElementById("save-state");
  if (saveState) { saveState.textContent = "正在保存"; saveState.classList.remove("error"); }
  for (const [field, value] of Object.entries(patch)) {
    if (["attributes", "sku_overrides", "image_prompts"].includes(field) && value && typeof value === "object") {
      state.pendingDraftPatch[field] = mergeDraftObject(field, state.pendingDraftPatch[field], value);
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
        state.pendingDraftPatch[field] = mergeDraftObject(field, value, state.pendingDraftPatch[field]);
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

async function saveSkuImageBinding(skuId, selectedImagePath) {
  if (!selectedImagePath) return;
  const result = await api(`/api/workbench/products/${state.currentProductId}/sku-image-bindings`, {
    method: "POST",
    body: JSON.stringify({sku_id: skuId, selected_image_path: selectedImagePath}),
  });
  toast(result.message || "SKU参考图已绑定，点击继续即可恢复任务", "success");
  await refreshCurrentProduct();
}

function imageSignature(product) {
  return JSON.stringify({
    status: product.status.status,
    current_step: product.status.current_step,
    ai_service_state: product.status.ai_service_state,
    ai_service_retry_after: product.status.ai_service_retry_after,
    error_message: product.status.error_message,
    progress: product.progress,
    pipeline_progress: product.pipeline_progress,
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
  const meta = document.querySelector("[data-progress-meta]");
  if (step) step.textContent = liveProgressText(product);
  if (value) value.textContent = `${progress}%`;
  if (bar) bar.style.width = `${progress}%`;
  if (status) status.textContent = aiServiceWaiting(product)
    ? "已暂停当前步骤，系统会自动重试；不会使用本地备用分析。"
    : product.status?.error_message && product.status.error_message !== "unknown" ? product.status.error_message : (product.pipeline_progress?.status_note || "后台正在处理，页面会自动更新");
  if (meta) meta.innerHTML = renderPipelineProgressMeta(product.pipeline_progress || {});
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
      updateLiveProgress(product);
      const signature = imageSignature(product);
      if (signature !== state.lastImageSignature) {
        state.currentProduct = product;
        state.lastImageSignature = signature;
        const imageWorkspace = document.getElementById("image-workspace");
        if (imageWorkspace && document.querySelector(".future-review-shell")) imageWorkspace.outerHTML = renderFutureImageStage(product);
        else if (imageWorkspace) imageWorkspace.innerHTML = document.querySelector(".product-preview-page") ? renderPreviewGallery(product) : renderImageWorkspace(product);
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

const INBOX_FILTERS = ["全部", "运行中", "需处理", "待运行", "待上传", "已提交"];

function inboxStatusMeta(product) {
  const raw = String(product.raw_status || "").toUpperCase();
  const priorityUpload = (state.queueSummary?.priority_product_ids || []).includes(product.product_id);
  if (isTerminalStatus(raw)) {
    return {label:"已提交", className:"completed", group:"completed", icon:"ph-check-circle"};
  }
  if (raw === "PARTIAL") return {label:"待继续", className:"warning", group:"attention", icon:"ph-warning-circle"};
  if (priorityUpload) return {label:"优先上传", className:"priority", group:"running", icon:"ph-upload-simple"};
  if (isNeedsAttentionStatus(raw)) return {label:"需要处理", className:"failed", group:"attention", icon:"ph-warning-circle"};
  if (raw === "UPLOADING") return {label:"上传中", className:"processing", group:"running", icon:"ph-upload-simple"};
  if (raw === "QUEUED") return {label:"排队中", className:"waiting", group:"waiting", icon:"ph-clock"};
  if (raw === "PROCESSING" || isPipelineRunningLike(product)) {
    return {label:"制作中", className:"processing", group:"running", icon:"ph-spinner-gap"};
  }
  if (product.attention_required) return {label:"待确认", className:"warning", group:"attention", icon:"ph-hand"};
  return {label:"待运行", className:"waiting", group:"waiting", icon:"ph-clock"};
}

function inboxMatchesFilter(product, filter = state.inboxFilter) {
  if (!filter || filter === "全部") return true;
  const group = inboxStatusMeta(product).group;
  return (filter === "运行中" && group === "running")
    || (filter === "需处理" && productNeedsCauseReview(product))
    || (filter === "待运行" && group === "waiting")
    || (filter === "待上传" && product.primary_action?.key === "review_upload")
    || (filter === "已提交" && group === "completed");
}

function inboxSnapshotSignature(products = state.products, queue = state.queueSummary) {
  return JSON.stringify({
    products: products.map((product) => [
      product.product_id, product.raw_status, product.current_step, product.progress,
      product.primary_action?.key, product.primary_action?.label, product.attention_required,
      product.image_count, product.error?.technical, product.handoff_message,
    ]),
    queue: queue ? [
      queue.active_batch_id, queue.active_product_count, queue.queued_batch_count,
      queue.queued_product_count, queue.priority_preemption_pending,
      queue.active_priority_upload, queue.message,
    ] : [],
  });
}

function inboxProductDecision(product) {
  const action = product.primary_action || {key:"status", label:"查看进度"};
  const raw = String(product.raw_status || "").toUpperCase();
  const failed = isNeedsAttentionStatus(raw);
  const priorityUpload = (state.queueSummary?.priority_product_ids || []).includes(product.product_id);
  const error = failed ? friendlyErrorInfo(product) : null;
  if (priorityUpload) {
    return {tone:"priority", title:"正在优先上传", body:state.queueSummary?.message || "上传任务已置顶，系统会避免重复提交。", action:"等待后台"};
  }
  if (product.pending_question?.question) {
    return {tone:"attention", title:"需要人工回答", body:product.pending_question.question, action:"回答问题"};
  }
  if (error) {
    return {tone:"danger", title:error.title, body:error.message, action:error.action || "处理问题"};
  }
  if (raw === "WAITING_MANUAL_REVIEW") {
    return {tone:"upload", title:"等待最终上传确认", body:"资料已经生成，进入预览检查后选择店铺并提交 Ozon。", action:"检查并上传"};
  }
  if (raw === "PARTIAL") {
    return {tone:"upload", title:"部分店铺未提交", body:"已提交过的店铺不会重复提交；继续时只处理未完成店铺。", action:"继续上传"};
  }
  if (["PENDING_REMOTE", "HANDED_OFF_TO_OZON", "OZON_MODERATION"].includes(raw)) {
    return {tone:"running", title:"等待 Ozon 处理", body:product.handoff_message || "任务号已保存，本地可只读查询结果，不能重复提交。", action:"查询结果"};
  }
  if (isTerminalStatus(raw)) {
    return {tone:"done", title:"已上架", body:product.handoff_message || "Ozon 已返回商品卡结果，本地只读。", action:"查看记录"};
  }
  if (raw === "PROCESSING" || raw === "UPLOADING" || isPipelineRunningLike(product)) {
    return {tone:"running", title:"后台正在执行", body:`当前步骤：${stepLabel(product.current_step)}，页面会自动同步进度。`, action:"查看进度"};
  }
  if (action.key === "run") {
    return {tone:"ready", title:"可以开始生产", body:"SKU、类目和店铺确认后，可以加入本次批量任务。", action:action.label};
  }
  return {tone:"neutral", title:"等待下一步", body:`当前停在 ${stepLabel(product.current_step)}。`, action:action.label};
}

function inboxChecklist(product) {
  const raw = String(product.raw_status || "").toUpperCase();
  const skuOk = Number(product.sku_count || 0) > 0;
  const storeOk = Number(product.selected_store_count || 0) > 0;
  const imagesOk = Number(product.image_count || 0) >= Math.max(1, Math.min(Number(product.sku_count || 0), 1));
  const uploadReady = ["WAITING_MANUAL_REVIEW", "PARTIAL"].includes(raw);
  const contentReady = !["COLLECTED", "STOPPED", "UNKNOWN"].includes(raw) || Number(product.progress || 0) >= 35;
  return [
    {label:"SKU", value:`${product.sku_count || 0}`, ok:skuOk},
    {label:"店铺", value:`${product.selected_store_count || 0}`, ok:storeOk},
    {label:"资料", value:contentReady ? "已生成" : "待生成", ok:contentReady},
    {label:"图片", value:`${product.image_count || 0}`, ok:imagesOk},
    {label:"上传", value:uploadReady ? "就绪" : "未到", ok:uploadReady},
  ];
}

function actionableProductIds(products = state.products) {
  return products
    .filter((product) => ["run", "fix", "review_upload"].includes(product.primary_action?.key))
    .filter((product) => !isTerminalStatus(product.raw_status))
    .map((product) => product.product_id);
}

function productNeedsCauseReview(product) {
  const raw = String(product.raw_status || "").toUpperCase();
  if (product.primary_action?.key === "review_upload" || raw === "PARTIAL") return false;
  return Boolean(
    product.attention_required
    || isNeedsAttentionStatus(raw)
    || product.pending_question?.question
  );
}

function renderNextActionStrip(products) {
  const upload = products.filter((product) => product.primary_action?.key === "review_upload");
  const generate = products.filter((product) => ["run", "fix"].includes(product.primary_action?.key) && !isTerminalStatus(product.raw_status));
  const attention = products.filter(productNeedsCauseReview);
  const items = [
    ["继续上传", upload, "待提交的店铺不会重复提交", "待上传", "ph-storefront"],
    ["继续生成", generate, "从当前断点继续跑生产线", "待运行", "ph-play"],
    ["查看原因", attention, "先看中文原因和恢复建议", "需处理", "ph-warning-circle"],
  ].filter(([, list]) => list.length);
  if (!items.length) {
    return `<section class="fusion-next-actions is-clear"><div><span class="ph ph-check-circle" aria-hidden="true"></span><strong>当前没有需要继续的商品</strong><small>新采集商品会出现在这里。</small></div></section>`;
  }
  const uniqueCount = new Set(items.flatMap(([, list]) => list.map((product) => product.product_id))).size;
  return `<section class="fusion-next-actions" aria-label="下一步任务">
    <header><span>下一步任务</span><strong>${uniqueCount} 个商品待处理</strong></header>
    <div>${items.map(([title, list, note, filter, icon]) => `<button type="button" data-inbox-filter="${filter}"><span class="ph ${icon}" aria-hidden="true"></span><strong>${title} ${list.length}</strong><small>${escapeHtml(note)}</small></button>`).join("")}</div>
  </section>`;
}

function inboxLauncherSummary(products, queue, plan) {
  const attention = products.filter(productNeedsCauseReview);
  const uploadReady = products.filter((product) => ["review_upload"].includes(product.primary_action?.key));
  const runnable = products.filter((product) => ["run", "fix"].includes(product.primary_action?.key));
  const running = products.filter((product) => inboxStatusMeta(product).group === "running");
  if (attention.length) {
    return {
      tone:"attention",
      label:"优先处理",
      title:`${attention.length} 个商品需要处理`,
      body:"先看需要继续的商品。能自动补全的会直接继续，真正失败的卡片会显示中文原因。",
      action:"查看下一步",
      filter:"需处理",
    };
  }
  if (uploadReady.length) {
    return {
      tone:"upload",
      label:"继续上传",
      title:`${uploadReady.length} 个商品还有店铺未提交`,
      body:"继续时只处理未完成店铺，已提交过的店铺不会重复提交。",
      action:"查看待上传",
      filter:"待上传",
    };
  }
  if (running.length) {
    return {
      tone:"running",
      label:"正在运行",
      title:`${running.length} 个商品正在后台执行`,
      body:queue.message || plan.summary || "页面会自动同步真实队列、步骤和进度。",
      action:"查看运行中",
      filter:"运行中",
    };
  }
  if (runnable.length) {
    return {
      tone:"ready",
      label:"可以开始",
      title:`${runnable.length} 个商品可加入生产`,
      body:"选择可处理商品后点击运行。资料、生图和上传按系统上限受控运行。",
      action:"运行可处理商品",
      filter:"待运行",
    };
  }
  return {
    tone:"quiet",
    label:"暂无动作",
    title:"当前没有需要启动的商品",
    body:queue.message || "采集新商品后会出现在这里。",
    action:"查看全部",
    filter:"全部",
  };
}

function inboxNavigationAttrs(filter) {
  if (filter === "需处理") return 'data-inbox-filter="需处理"';
  if (filter === "已提交") return 'data-go="listed"';
  return `data-inbox-filter="${escapeHtml(filter || "全部")}"`;
}

function fusionInboxCard(product) {
  const action = product.primary_action || {key:"status", label:"查看进度"};
  const failed = isNeedsAttentionStatus(product.raw_status);
  const priorityUpload = (state.queueSummary?.priority_product_ids || []).includes(product.product_id);
  const status = inboxStatusMeta(product);
  const progress = Math.max(0, Math.min(100, Number(product.progress) || 0));
  const running = isPipelineRunningLike(product) && !priorityUpload;
  const visibleAction = running ? {key:"status", label:"查看进度"} : action;
  const decision = inboxProductDecision(product);
  const actionLabel = priorityUpload ? "优先上传处理中" : failed ? "查看原因并继续" : running ? `查看进度（${progress}%）` : visibleAction.label;
  return `<article class="fusion-task-card ${status.className} ${product.attention_required ? "needs-attention" : ""}" data-card-product="${escapeHtml(product.product_id)}">
    <div class="fusion-task-thumb">${thumbnail(product)}</div>
    <div class="fusion-task-main">
      <div class="fusion-task-kicker"><strong>${escapeHtml(product.product_id)}</strong><span>${escapeHtml(product.workflow_bucket)}</span></div>
      <h3>${escapeHtml(product.title_cn)}</h3>
      <div class="fusion-task-facts"><span><i class="ph ph-stack"></i>${product.sku_count} SKU</span><span><i class="ph ph-image"></i>${product.image_count || 0} 图</span><span><i class="ph ph-storefront"></i>${product.selected_store_count || 0} 店</span></div>
      <p class="fusion-task-plain">${escapeHtml(decision.title)} · ${escapeHtml(decision.body)}</p>
    </div>
    <div class="fusion-task-progress">
      <div class="fusion-status ${status.className}"><i class="ph ${status.icon}"></i>${status.label}</div>
      <div class="fusion-progress-row"><span><i style="width:${progress}%"></i></span><b>${progress}%</b></div>
      <small>${escapeHtml(stepLabel(product.current_step))}</small>
    </div>
    <div class="fusion-task-actions">
      <label class="fusion-card-select"><input class="batch-select" type="checkbox" aria-label="选择 ${escapeHtml(product.product_id)}" data-select-batch-product="${escapeHtml(product.product_id)}" ${state.selectedBatchProducts.has(product.product_id) ? "checked" : ""}> 选择</label>
      ${failed ? `<button class="secondary-button" data-error-edit="${escapeHtml(product.product_id)}">立即修改</button>` : ""}
      <button class="primary-button" data-primary-action="${escapeHtml(visibleAction.key)}" data-product-id="${escapeHtml(product.product_id)}" ${priorityUpload ? "disabled" : ""}>${escapeHtml(actionLabel)}</button>
    </div>
    ${productMenu(product.product_id)}
  </article>`;
}

function inboxBackedViewActive() {
  return ["inbox", "attention", "waiting"].includes(state.view);
}

function scheduleInboxPolling() {
  clearTimeout(state.inboxPollTimer);
  if (!inboxBackedViewActive()) return;
  state.inboxPollTimer = setTimeout(async () => {
    if (!inboxBackedViewActive()) return;
    if (state.inboxPollBusy || document.hidden || document.querySelector("dialog[open]") || document.activeElement === searchInput) {
      scheduleInboxPolling();
      return;
    }
    state.inboxPollBusy = true;
    const before = inboxSnapshotSignature();
    try {
      const data = await loadProducts(searchInput.value, {force:true});
      state.inboxLastSyncedAt = new Date();
      if (before !== inboxSnapshotSignature()) await renderInbox({data, reuseShops:true});
    } catch (_) {
      // 保留当前可操作页面；顶部主机状态会单独报告连接问题。
    } finally {
      state.inboxPollBusy = false;
      scheduleInboxPolling();
    }
  }, 12000);
}

async function renderInbox(options = {}) {
  const data = options.data || await loadProducts(searchInput.value, {force:Boolean(options.force || options.forceProducts)});
  const actionableIds = new Set(actionableProductIds(state.products));
  state.selectedBatchProducts = new Set([...state.selectedBatchProducts].filter((id) => actionableIds.has(id)));
  await loadWorkbenchSettings();
  if (!state.shops.length || options.forceShops) {
    try {
      state.shops = (await api("/api/workbench/shops")).items || [];
    } catch (_) {
      state.shops = state.shops || [];
    }
  }
  const runnable = state.products.filter((item) => ["run", "fix"].includes(item.primary_action?.key));
  const plan = state.executionPlan || {};
  const queue = state.queueSummary || {};
  const filteredProducts = state.products.filter((product) => inboxMatchesFilter(product));
  const statusGroups = state.products.map((product) => inboxStatusMeta(product).group);
  const runningCount = Math.max(statusGroups.filter((group) => group === "running").length, Number(queue.active_product_count) || 0, queue.waiting_for_active ? 1 : 0);
  const waitingCount = Math.max(statusGroups.filter((group) => group === "waiting").length, Number(queue.queued_product_count) || 0);
  const attentionCount = state.products.filter(productNeedsCauseReview).length;
  const submittedCount = statusGroups.filter((group) => group === "completed").length;
  const uploadReadyCount = state.products.filter((product) => product.primary_action?.key === "review_upload").length;
  const launcher = inboxLauncherSummary(state.products, queue, plan);
  const actionableCount = actionableIds.size;
  const launcherPrimary = launcher.tone === "ready"
    ? `<button type="button" class="primary-button task-primary" data-action="open-batch" ${runnable.length ? "" : "disabled"}><span class="ph ph-play"></span>运行可处理商品</button>`
    : `<button type="button" class="primary-button task-primary" ${inboxNavigationAttrs(launcher.filter)}><span class="ph ph-arrow-right"></span>${escapeHtml(launcher.action)}</button>`;
  state.inboxLastSyncedAt = state.inboxLastSyncedAt || new Date();
  root.innerHTML = `<div class="fusion-inbox">
    <section class="fusion-command-hero fusion-launcher">
      <div class="fusion-launch-primary ${launcher.tone}">
        <span class="fusion-eyebrow">JLC GLOBAL COMMAND</span>
        <small>${escapeHtml(launcher.label)}</small>
        <h2>${escapeHtml(launcher.title)}</h2>
        <p>${escapeHtml(launcher.body)}</p>
        <div class="fusion-launch-actions">
          ${launcherPrimary}
        </div>
      </div>
      <aside class="fusion-launch-console">
        <div class="fusion-live-chip"><i></i>后台在线 · 自动同步</div>
        <dl>
          <div><dt>运行</dt><dd>${runningCount}</dd></div>
          <div><dt>待处理</dt><dd>${attentionCount}</dd></div>
          <div><dt>已提交</dt><dd>${submittedCount}</dd></div>
        </dl>
        <small>${escapeHtml(queue.message || plan.summary || "生产线空闲，等待新商品。")}</small>
      </aside>
      <div class="fusion-lanes">
        <button type="button" data-inbox-filter="待运行"><span>${runnable.length}</span><strong>可运行</strong><small>可加入批次</small></button>
        <button type="button" data-inbox-filter="需处理"><span>${attentionCount}</span><strong>需处理</strong><small>失败或确认</small></button>
        <button type="button" data-inbox-filter="待上传"><span>${uploadReadyCount}</span><strong>待上传</strong><small>点击上传</small></button>
      </div>
    </section>
    <section class="fusion-task-toolbar">
      <div class="fusion-filter-group" aria-label="商品状态筛选">${INBOX_FILTERS.map((filter) => `<button type="button" data-inbox-filter="${filter}" class="${state.inboxFilter === filter ? "active" : ""}">${filter === "全部" ? "全部商品" : filter}</button>`).join("")}</div>
      <label class="fusion-select-all"><input type="checkbox" data-select-all-products ${actionableCount && state.selectedBatchProducts.size === actionableCount ? "checked" : ""}> 全选可处理</label>
      <span class="spacer"></span>
      <small>最近同步 ${state.inboxLastSyncedAt.toLocaleTimeString("zh-CN", {hour:"2-digit", minute:"2-digit", second:"2-digit"})}</small>
      <button class="primary-button" data-action="open-batch" ${state.selectedBatchProducts.size ? "" : "disabled"}>${state.selectedBatchProducts.size ? `${batchProductsAreUploadReady([...state.selectedBatchProducts]) ? "继续上传所选" : "运行所选"} ${state.selectedBatchProducts.size}` : "选择任务"}</button>
    </section>
    <section class="fusion-list-heading"><div><span>PRODUCT TASKS</span><h3>任务列表</h3></div><strong>${filteredProducts.length} / ${state.products.length}</strong></section>
    <div class="fusion-task-list">${filteredProducts.map(fusionInboxCard).join("") || empty(state.products.length ? "当前筛选条件下没有商品" : "工作室还没有商品。请先在1688选择SKU和最终Ozon类目，再完成采集。")}</div>
  </div>`;
  scheduleInboxPolling();
}

async function renderAttention() {
  state.inboxFilter = "需处理";
  document.body.dataset.activeView = "inbox";
  return renderInbox();
}

async function renderWaitingUpload() {
  state.inboxFilter = "待上传";
  document.body.dataset.activeView = "inbox";
  return renderInbox();
}

async function renderListed() {
  await loadProducts(searchInput.value);
  const items = state.products.filter((item) => isTerminalStatus(item.raw_status));
  root.innerHTML = `<section class="task-summary"><div><h2>${items.length} 个已提交商品</h2><p>只显示本地已交接给 Ozon 的商品；Ozon审核、商品卡和库存状态请在Ozon后台查看。</p></div><div class="secondary-button disabled-control">远端回查已停用</div></section><div class="list-layout inbox-list">${items.map((product) => inboxCard(product, false)).join("") || empty("还没有已提交商品")}</div>`;
}

function localIsoDate(value = new Date()) {
  const offset = value.getTimezoneOffset();
  return new Date(value.getTime() - offset * 60000).toISOString().slice(0, 10);
}

function financePeriodDates(period) {
  const today = new Date();
  const end = localIsoDate(today);
  if (period === "today") return [end, end];
  if (period === "yesterday") {
    const day = new Date(today); day.setDate(day.getDate() - 1);
    const value = localIsoDate(day); return [value, value];
  }
  if (period === "7d" || period === "30d") {
    const start = new Date(today); start.setDate(start.getDate() - (period === "7d" ? 6 : 29));
    return [localIsoDate(start), end];
  }
  if (period === "last_month") {
    const first = new Date(today.getFullYear(), today.getMonth() - 1, 1);
    const last = new Date(today.getFullYear(), today.getMonth(), 0);
    return [localIsoDate(first), localIsoDate(last)];
  }
  if (period === "all") return ["2020-01-01", end];
  return [localIsoDate(new Date(today.getFullYear(), today.getMonth(), 1)), end];
}

function financeMoney(value, overview) {
  const amount = Number(value || 0);
  if (!Number.isFinite(amount)) return "未确认";
  const currency = overview.currency || "CNY";
  const converted = currency === "RUB" ? amount * Number(overview.rub_per_cny || 12) : amount;
  return `${converted.toLocaleString("zh-CN", {minimumFractionDigits:2, maximumFractionDigits:2})} ${currency === "RUB" ? "₽" : "元"}`;
}

function financePercent(value) {
  const numberValue = Number(value || 0) * 100;
  return `${numberValue.toLocaleString("zh-CN", {minimumFractionDigits:1, maximumFractionDigits:1})}%`;
}

function financeImage(item) {
  return item.image_url
    ? `<img class="finance-product-image" src="${escapeHtml(item.image_url)}" alt="${escapeHtml(item.product_name || item.sku || "商品主图")}" loading="lazy">`
    : `<span class="finance-image-missing">暂无主图</span>`;
}

function financeTabs() {
  const tabs = [
    ["overview", "总览"], ["products", "商品"], ["orders", "订单"],
    ["reconciliation", "待核对"], ["other", "其他收支"],
  ];
  if (state.session?.can_manage_settings) tabs.push(["manage", "同步与导入"]);
  return `<nav class="finance-tabs">${tabs.map(([key, label]) => `<button type="button" data-finance-tab="${key}" class="${state.financeTab === key ? "active" : ""}">${label}</button>`).join("")}</nav>`;
}

function financeToolbar(overview) {
  const currencyLabel = state.financeCurrency === "RUB" ? "卢布 RUB" : "人民币 CNY";
  const currencyOther = state.financeCurrency === "RUB" ? ["CNY", "人民币 CNY"] : ["RUB", "卢布 RUB"];
  const currencySelect = `<label class="finance-currency-select">币种<select data-finance-currency-select><option value="${state.financeCurrency}" selected>${currencyLabel}</option><option value="${currencyOther[0]}">${currencyOther[1]}</option></select></label>`;
  const storeOptions = [{id:"all", name:"全部店铺"}, ...(overview.stores || [])];
  return `<section class="finance-toolbar panel">
    <div class="finance-filter-group"><label>店铺<select data-finance-store>${storeOptions.map((item) => `<option value="${escapeHtml(item.id)}" ${state.financeStoreId === item.id ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select></label>
    <label>时间<select data-finance-period>
      <option value="current_month" ${state.financePeriod === "current_month" ? "selected" : ""}>本月至今</option><option value="today" ${state.financePeriod === "today" ? "selected" : ""}>今天</option><option value="yesterday" ${state.financePeriod === "yesterday" ? "selected" : ""}>昨天</option><option value="7d" ${state.financePeriod === "7d" ? "selected" : ""}>近7天</option><option value="30d" ${state.financePeriod === "30d" ? "selected" : ""}>近30天</option><option value="last_month" ${state.financePeriod === "last_month" ? "selected" : ""}>上月</option><option value="custom" ${state.financePeriod === "custom" ? "selected" : ""}>自定义</option><option value="all" ${state.financePeriod === "all" ? "selected" : ""}>全部</option>
    </select></label>
    <label>开始<input type="date" data-finance-date-from value="${escapeHtml(state.financeDateFrom)}"></label><label>结束<input type="date" data-finance-date-to value="${escapeHtml(state.financeDateTo)}"></label></div>
    <div class="finance-actions"><div class="finance-currency-toggle"><button type="button" data-finance-currency="CNY" class="${state.financeCurrency === "CNY" ? "active" : ""}>人民币</button><button type="button" data-finance-currency="RUB" class="${state.financeCurrency === "RUB" ? "active" : ""}>卢布</button></div>
    <button type="button" class="secondary-button" data-finance-export="${state.financeTab === "products" ? "products" : state.financeTab === "reconciliation" ? "reconciliation" : "orders"}">导出明细</button>
    ${state.session?.can_manage_settings ? `<button type="button" class="primary-button" data-finance-sync>立即同步</button>` : ""}</div>
  </section>`.replace(/<div class="finance-currency-toggle">.*?<\/div>/, currencySelect);
}

function financeOverviewMarkup(overview) {
  const summary = overview.summary;
  const coverage = overview.coverage;
  const expectedAvailable = summary.expected_profit_available !== false && summary.expected_profit != null;
  const missingLabels = {purchase: "采购价", finance: "Finance", logistics: "物流", ads: "广告"};
  const missingSources = (summary.expected_profit_missing_sources || []).map((key) => missingLabels[key] || key).join("、");
  const gapValue = (value) => value == null ? "暂无可用样本" : `${escapeHtml(value)} ${overview.currency === "RUB" ? "₽" : "元"}`;
  return `<section class="finance-metrics">
    <article><span>销售额</span><strong>${escapeHtml(summary.sales)} ${overview.currency === "RUB" ? "₽" : "元"}</strong><small>${summary.effective_order_lines} 条有效商品行</small></article>
    <article class="confirmed"><span>已完整核算利润</span><strong>${summary.fully_covered_order_lines ? `${escapeHtml(summary.confirmed_profit)} ${overview.currency === "RUB" ? "₽" : "元"}` : "暂无"}</strong><small>${summary.fully_covered_order_lines ? `${summary.fully_covered_order_lines} 条四项成本完整 · ${financePercent(summary.confirmed_margin)}` : "没有同时具备采购、Finance、物流和订单级广告的商品行"}</small></article>
    <article class="expected"><span>全店预计利润</span><strong>${expectedAvailable ? `${escapeHtml(summary.expected_profit)} ${overview.currency === "RUB" ? "₽" : "元"}` : "暂不可计算"}</strong><small>${expectedAvailable ? `${financePercent(summary.expected_margin)} · 缺口按已覆盖成本率外推` : `缺少${escapeHtml(missingSources || "关键成本")}数据，不能把销售额当利润`}</small></article>
    <article><span>其他收支净额</span><strong>${financeMoney(Number(summary.other_income) - Number(summary.other_expense), overview)}</strong><small>收入 ${summary.other_income} · 支出 ${summary.other_expense}</small></article>
  </section>
  <section class="finance-overview-grid">
    <article class="panel finance-coverage"><div class="panel-head"><h3>数据覆盖</h3><span>销售额口径</span></div><div class="panel-body">
      ${[["采购价",coverage.purchase],["Finance",coverage.finance],["物流",coverage.logistics],["广告匹配",coverage.ads]].map(([label,value]) => `<div class="coverage-row"><span>${label}</span><div><i style="width:${Math.min(100, Number(value || 0) * 100)}%"></i></div><strong>${financePercent(value)}</strong></div>`).join("")}
    </div></article>
    <article class="panel finance-gap"><div class="panel-head"><h3>预计利润中的缺口处理</h3><span>不使用未匹配金额</span></div><div class="panel-body">
      <div><span>缺采购价外推</span><strong>${gapValue(overview.gap_estimates.missing_purchase)}</strong></div><div><span>Finance 缺口外推</span><strong>${gapValue(overview.gap_estimates.missing_finance)}</strong></div><div><span>物流缺口外推</span><strong>${gapValue(overview.gap_estimates.missing_logistics)}</strong></div><div><span>期间级广告</span><strong>${gapValue(overview.gap_estimates.period_level_unallocated_ads)}</strong></div>
      <p>${escapeHtml(overview.gap_estimates.method)}</p></div></article>
  </section>
  <section class="finance-warning"><strong>当前口径说明</strong><span>${overview.warnings.map(escapeHtml).join(" ")}</span><button type="button" data-finance-tab="reconciliation">查看 ${overview.reconciliation.unmatched_finance_rows + overview.reconciliation.unmatched_ads_rows} 条待核对记录</button></section>`;
}

function financeProductsMarkup(data, overview) {
  return `<section class="finance-list-head"><div><strong>${data.total} 个商品</strong><span>按销售额从高到低</span></div></section><div class="table-wrap"><table class="data-table finance-table"><thead><tr><th>商品</th><th>商业编号</th><th>店铺</th><th>销售额</th><th>当前试算利润</th><th>利润率</th><th>缺采购价</th></tr></thead><tbody>${data.items.map((item) => `<tr><td><div class="finance-product-cell">${financeImage(item)}<strong>${escapeHtml(item.product_name || "商品名未记录")}</strong></div></td><td><strong>${escapeHtml(item.offer_id || item.sku)}</strong><small>SKU ${escapeHtml(item.sku)}</small></td><td>${escapeHtml(item.store_id)}</td><td>${financeMoney(item.sales_cny, overview)}</td><td>${financeMoney(item.profit_cny, overview)}</td><td>${financePercent(item.profit_margin)}</td><td>${item.missing_purchase_lines ? `<span class="status-pill medium">${item.missing_purchase_lines} 条</span>` : `<span class="status-pill completed">完整</span>`}</td></tr>`).join("") || `<tr><td colspan="7">当前时间范围没有商品</td></tr>`}</tbody></table></div>`;
}

function financeOrdersMarkup(data, overview) {
  return `<section class="finance-list-head"><div><strong>${data.total} 条订单商品行</strong><span>订单编号、Posting 编号和商业编号同时保留</span></div></section><div class="table-wrap"><table class="data-table finance-table finance-order-table"><thead><tr><th>商品</th><th>订单</th><th>商业编号</th><th>日期</th><th>销售额</th><th>采购 / Finance / 物流 / 广告</th><th>当前试算利润</th><th>覆盖</th></tr></thead><tbody>${data.items.map((item) => `<tr><td><div class="finance-product-cell">${financeImage(item)}<strong>${escapeHtml(item.product_name || "商品名未记录")}</strong></div></td><td><strong>订单 ${escapeHtml(item.order_number || "未记录")}</strong><small>Posting ${escapeHtml(item.posting_number)}</small></td><td><strong>${escapeHtml(item.offer_id || item.sku)}</strong><small>SKU ${escapeHtml(item.sku)}</small></td><td>${escapeHtml(item.order_date || "未记录")}</td><td>${financeMoney(item.buyer_paid_cny, overview)}<small>原始 ${escapeHtml(item.buyer_paid_rub)} ₽</small></td><td><small>采购 ${financeMoney(item.purchase_cost_cny, overview)}<br>Finance ${financeMoney(item.finance_fee_cny, overview)}<br>物流 ${financeMoney(item.logistics_cny, overview)}<br>广告 ${financeMoney(item.ad_spend_cny, overview)}</small></td><td>${financeMoney(item.profit_cny, overview)}<small>${financePercent(item.profit_margin)}</small></td><td>${item.fully_covered ? `<span class="status-pill completed">完整</span>` : `<span class="status-pill medium">有缺口</span>`}</td></tr>`).join("") || `<tr><td colspan="8">当前时间范围没有订单</td></tr>`}</tbody></table></div>`;
}

function financeReconciliationMarkup(data) {
  const financeCount = data.counts.finance || 0;
  const adsCount = data.counts.ads || 0;
  return `<section class="finance-reconcile-head"><div><span>Finance 待核对</span><strong>${financeCount}</strong></div><div><span>广告待核对</span><strong>${adsCount}</strong></div><p>${escapeHtml(data.notice)}</p></section><div class="table-wrap"><table class="data-table finance-table"><thead><tr><th>类型</th><th>日期</th><th>订单 / Posting</th><th>商业编号</th><th>金额（仅供核对）</th><th>原因</th></tr></thead><tbody>${data.items.map((item) => `<tr><td>${item.file_type === "finance" ? "Finance" : "广告"}</td><td>${escapeHtml(item.occurred_at || "未记录")}</td><td><strong>${escapeHtml(item.order_number || "未记录")}</strong><small>${escapeHtml(item.posting_number || "无 Posting")}</small></td><td>${escapeHtml(item.offer_id || item.sku || "未记录")}</td><td>${escapeHtml(item.amount_cny)} 元</td><td>${escapeHtml(item.reason)}</td></tr>`).join("") || `<tr><td colspan="6">没有待核对记录</td></tr>`}</tbody></table></div>`;
}

function financeOtherMarkup(data, overview) {
  const ownerForm = state.session?.can_manage_settings ? `<form class="finance-other-form" data-finance-other-form><label>类型<select name="entry_type"><option value="expense">支出</option><option value="income">收入</option></select></label><label>金额<input name="amount" type="number" min="0.01" step="0.01" required></label><label>币种<select name="currency"><option>CNY</option><option>RUB</option></select></label><label>日期<input name="occurred_on" type="date" value="${localIsoDate()}" required></label><label>范围<select name="store_id"><option value="">全部店铺</option>${overview.stores.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("")}</select></label><label class="wide">备注<input name="note" maxlength="240" placeholder="例如：退款补偿、办公费用"></label><button class="primary-button" type="submit">加入其他收支</button></form>` : "";
  return `${ownerForm}<div class="table-wrap"><table class="data-table finance-table"><thead><tr><th>日期</th><th>类型</th><th>金额</th><th>范围</th><th>备注</th>${state.session?.can_manage_settings ? "<th>操作</th>" : ""}</tr></thead><tbody>${data.items.map((item) => `<tr><td>${escapeHtml(item.occurred_on)}</td><td>${item.entry_type === "income" ? "收入" : "支出"}</td><td class="${item.entry_type === "income" ? "finance-income" : "finance-expense"}">${item.entry_type === "income" ? "+" : "-"}${financeMoney(item.amount_cny, overview)}</td><td>${escapeHtml(item.store_id || "全部店铺")}</td><td>${escapeHtml(item.note || "无备注")}</td>${state.session?.can_manage_settings ? `<td><button type="button" class="danger-text-button" data-finance-delete-other="${escapeHtml(item.id)}">删除</button></td>` : ""}</tr>`).join("") || `<tr><td colspan="6">当前时间范围没有其他收支</td></tr>`}</tbody></table></div>`;
}

function financeManageMarkup(syncStatus, batches) {
  const preview = state.financeImportPreview;
  const mapping = preview ? `<div class="finance-import-preview"><div><strong>${escapeHtml(preview.file_name)}</strong><span>${preview.row_count} 行 · 判断为 ${escapeHtml(preview.file_kind)}</span></div><table class="data-table"><thead><tr><th>文件列</th><th>对应字段</th><th>置信度</th></tr></thead><tbody>${preview.mapping_candidates.map((item) => `<tr><td>${escapeHtml(item.source_header)}</td><td><select data-finance-map-source="${escapeHtml(item.source_header)}"><option value="">不导入</option>${[item.target_field, ...(item.alternatives || [])].filter(Boolean).filter((value,index,array) => array.indexOf(value) === index).map((field) => `<option value="${escapeHtml(field)}" ${field === item.target_field ? "selected" : ""}>${escapeHtml(field)}</option>`).join("")}</select></td><td>${financePercent(item.confidence)} ${item.requires_manual_confirmation ? "· 需确认" : "· 已识别"}</td></tr>`).join("")}</tbody></table><label class="finance-confirm-check"><input type="checkbox" data-finance-import-confirm> 我已核对金额列和字段对应关系</label><button type="button" class="primary-button" data-finance-import-commit>确认导入并保留回滚点</button></div>` : "";
  return `<section class="finance-manage-grid"><article class="panel"><div class="panel-head"><h3>自动同步</h3><span>每天 15:00</span></div><div class="panel-body"><p>${escapeHtml(syncStatus.schedule)}</p><p>每次重扫最近 ${syncStatus.rescan_days} 天，发现变化会保留同步记录。</p><strong>最近成功：${escapeHtml(syncStatus.last_successful_sync_date || "尚未执行")}</strong><p>广告数据需要单独的广告来源；未配置时明确显示未知，不会当作 0。</p><button type="button" class="primary-button" data-finance-sync>立即同步全部已启用店铺</button></div></article>
  <article class="panel"><div class="panel-head"><h3>导入财务文件</h3><span>先预览，后确认</span></div><div class="panel-body"><label class="finance-file-drop"><input type="file" accept=".xlsx,.csv,.tsv" data-finance-import-file><strong>选择 Excel 或 CSV</strong><span>自动识别字段；金额列必须人工确认</span></label>${preview ? "" : `<p class="form-help">支持采购价、订单、Finance、广告文件。其他收支请在“其他收支”页面手工加入。</p>`}</div></article></section>${mapping}
  <section class="panel finance-import-history"><div class="panel-head"><h3>导入与回滚记录</h3><span>${batches.items.length} 个批次</span></div><div class="table-wrap"><table class="data-table"><thead><tr><th>时间</th><th>文件</th><th>类型</th><th>结果</th><th>操作</th></tr></thead><tbody>${batches.items.map((item) => `<tr><td>${escapeHtml(dateText(item.applied_at || item.created_at))}</td><td>${escapeHtml(item.file_name)}</td><td>${escapeHtml(item.file_kind)}</td><td>${item.status === "applied" ? `已处理 ${item.inserted_count + item.updated_count} 行` : "已回滚"}</td><td>${item.rollback_allowed ? `<button type="button" class="danger-text-button" data-finance-rollback="${escapeHtml(item.id)}">回滚此批次</button>` : item.file_kind === "system_ad_match_repair" ? "数据库恢复点" : "—"}</td></tr>`).join("") || `<tr><td colspan="5">暂无导入记录</td></tr>`}</tbody></table></div></section>`;
}

async function renderFinance() {
  searchInput.placeholder = "搜索订单编号、Posting、商业编号或商品";
  if (!state.financeDateFrom || !state.financeDateTo) [state.financeDateFrom, state.financeDateTo] = financePeriodDates(state.financePeriod);
  const query = new URLSearchParams({store_id:state.financeStoreId, date_from:state.financeDateFrom, date_to:state.financeDateTo, currency:state.financeCurrency});
  const overview = await api(`/api/workbench/finance/overview?${query}`);
  let content = "";
  if (state.financeTab === "overview") content = financeOverviewMarkup(overview);
  if (state.financeTab === "products") {
    const params = new URLSearchParams({store_id:state.financeStoreId, date_from:state.financeDateFrom, date_to:state.financeDateTo, q:searchInput.value, limit:"500"});
    content = financeProductsMarkup(await api(`/api/workbench/finance/products?${params}`), overview);
  }
  if (state.financeTab === "orders") {
    const params = new URLSearchParams({store_id:state.financeStoreId, date_from:state.financeDateFrom, date_to:state.financeDateTo, q:searchInput.value, limit:"500"});
    content = financeOrdersMarkup(await api(`/api/workbench/finance/orders?${params}`), overview);
  }
  if (state.financeTab === "reconciliation") content = financeReconciliationMarkup(await api(`/api/workbench/finance/reconciliation?store_id=${encodeURIComponent(state.financeStoreId)}&limit=500`));
  if (state.financeTab === "other") content = financeOtherMarkup(await api(`/api/workbench/finance/other-entries?${query}`), overview);
  if (state.financeTab === "manage" && state.session?.can_manage_settings) {
    const [syncStatus, batches] = await Promise.all([api("/api/workbench/finance/sync-status"), api("/api/workbench/finance/imports")]);
    content = financeManageMarkup(syncStatus, batches);
  }
  root.innerHTML = `${financeToolbar(overview)}${financeTabs()}<section class="finance-content">${content}</section>`;
}

const fbsRecommendationLabels = {
  recommended: "适合优先评估",
  caution: "利润与运费需核算",
  review_required: "存在跨境风险，需复核",
  insufficient_data: "数据不足",
};

const fbsRiskLabels = {
  liquid_or_oil: "液体或油类",
  battery_or_power: "电池或电源",
  large_volume: "大体积",
};

const marketCategoryLabels = {home:"家居", electronics:"电子产品", bathroom:"卫浴", kitchen:"厨房", outdoor:"户外", auto:"汽车配件"};

function marketFact(value, suffix = "") {
  if (value === null || value === undefined || value === "" || value === "unknown") return "暂无";
  return `${number(Number(value), 1)}${suffix}`;
}

function marketCategoryButtons(categories) {
  return categories.filter((item) => item.enabled !== false).map((item) => {
    const active = item.key === state.marketCategory;
    return `<button type="button" class="${active ? "active" : ""}" data-market-category="${escapeHtml(item.key)}" aria-pressed="${active}"><strong>${escapeHtml(item.name_zh)}</strong><small>查看类目数据</small></button>`;
  }).join("");
}

function marketProductCard(product) {
  const facts = product.facts || {};
  const fbs = product.fbs_assessment || {};
  const growth = Number(facts.ordered_amount_growth_percent);
  const growthText = Number.isFinite(growth) ? `${growth >= 0 ? "+" : ""}${number(growth, 1)}%` : "暂无";
  const fbsScore = fbs.score === "unknown" ? "暂无" : `${number(Number(fbs.score), 0)}分`;
  const hasRealImage = product.image_url && product.image_url !== "unknown";
  const visual = hasRealImage
    ? `<span class="market-card-visual"><img src="${escapeHtml(product.image_url)}" alt="${escapeHtml(product.title_ru)}" loading="lazy"></span>`
    : `<span class="market-card-signal"><small>公开真实主图</small><strong>主图同步中</strong><em>点开商品时自动补齐</em></span>`;
  return `<article class="market-product-card">
    <button type="button" class="market-card-open" data-market-product="${escapeHtml(product.source_product_id)}" aria-label="查看${escapeHtml(product.title_ru)}">
      <span class="market-rank">#${number(product.ranking_position)}</span>
      ${visual}
      <span class="market-card-copy"><small>${escapeHtml(facts.category_level_3 || facts.category_level_1 || "类目待确认")}</small><strong>${escapeHtml(product.title_ru)}</strong><em>${product.title_zh === "unknown" ? `${escapeHtml(marketCategoryLabels[state.marketCategory] || "商品")} · ${escapeHtml(fbsRecommendationLabels[fbs.recommendation] || "待判断")}` : escapeHtml(product.title_zh)}</em></span>
    </button>
    <div class="market-card-metrics"><span><small>近30天销量</small><strong>${marketFact(facts.ordered_units, "件")}</strong></span><span><small>销售额增长</small><strong class="${growth >= 0 ? "up" : "down"}">${growthText}</strong></span><span><small>成交均价</small><strong>${marketFact(facts.average_purchase_price_rub, "₽")}</strong></span></div>
    <div class="market-card-foot"><span class="market-model">${escapeHtml(facts.fulfillment_model || "履约未知")}</span><span class="market-fbs ${escapeHtml(fbs.recommendation || "")}">FBS ${fbsScore} · ${escapeHtml(fbsRecommendationLabels[fbs.recommendation] || "待判断")}</span></div>
  </article>`;
}

function marketKeywordCard(keyword) {
  const metrics = keyword.metrics || {};
  return `<article class="market-keyword-card"><span>${escapeHtml(keyword.keyword_type === "hot" ? "热词" : keyword.keyword_type === "long_tail" ? "长尾词" : keyword.keyword_type === "scenario" ? "场景词" : "属性词")}</span><strong>${escapeHtml(keyword.keyword_ru)}</strong><small>${keyword.keyword_zh === "unknown" ? "中文待生成" : escapeHtml(keyword.keyword_zh)}</small><div><em>热度 ${marketFact(metrics.popularity)}</em><em>加购率 ${marketFact(metrics.add_to_cart_conversion_percent, "%")}</em><em>均价 ${marketFact(metrics.average_buyer_price_rub, "₽")}</em></div></article>`;
}

async function renderMarket() {
  searchInput.placeholder = "搜索俄文商品名或Ozon商品编号";
  searchInput.value = state.marketQuery;
  const params = new URLSearchParams({
    ranking: state.marketRanking,
    category: state.marketCategory,
    period: String(state.marketPeriod),
    page: String(state.marketPage),
    page_size: "24",
    q: state.marketQuery,
  });
  const [status, data, keywordData] = await Promise.all([
    api("/api/workbench/market-intelligence/status"),
    api(`/api/workbench/market-intelligence/products?${params}`),
    api(`/api/workbench/market-intelligence/keywords?category=${encodeURIComponent(state.marketCategory)}&limit=12`),
  ]);
  const pageCount = Math.max(1, Math.ceil((data.total || 0) / (data.page_size || 24)));
  const unavailableMarkup = `<section class="market-unavailable" aria-live="polite"><strong>近7天榜单正在积累数据</strong><p>${escapeHtml(data.notice)}</p><button type="button" class="primary-button" data-market-period="30">查看近30天真实榜单</button></section>`;
  const resultsMarkup = `<section class="market-result-head" aria-live="polite"><div><strong>${number(data.total)} 个符合当前条件的商品</strong><span>${state.marketRanking === "hot" ? "按官方销售额排序" : "按官方销售额增长率排序"}</span></div><span>第 ${number(data.page)} / ${number(pageCount)} 页</span></section><section class="market-product-grid">${data.items.map(marketProductCard).join("") || empty("当前类目没有匹配商品")}</section><nav class="market-pagination"><button type="button" class="secondary-button" data-market-page="${Math.max(1, data.page - 1)}" ${data.page <= 1 ? "disabled" : ""}>上一页</button><span>${number(data.page)} / ${number(pageCount)}</span><button type="button" class="secondary-button" data-market-page="${Math.min(pageCount, data.page + 1)}" ${data.page >= pageCount ? "disabled" : ""}>下一页</button></nav>`;
  root.innerHTML = `<section class="market-hero">
    <div><span>Ozon市场数据 · 测试版</span><h2>${state.marketRanking === "hot" ? "近期热销商品" : "近期增长商品"}</h2><p>已接入 ${number(status.counts.products)} 条 Ozon 官方市场商品。商品指标保留来源，本地只负责筛选、排序和FBS规则判断。</p></div>
    <div class="market-source-state"><i></i><span><strong>官方公开数据已连接</strong><small>最近更新：${escapeHtml(dateText(status.last_updated_at))} · ${escapeHtml(status.trend?.notice || data.notice || status.notice)}</small></span></div>
  </section>
  <section class="market-controls panel">
    <label class="market-mobile-search"><span>搜索商品</span><input type="search" data-market-search aria-label="搜索市场商品" value="${escapeHtml(state.marketQuery)}" placeholder="俄文商品名或Ozon商品编号"><small>输入后自动筛选，也可按回车立即搜索</small></label>
    <div class="market-control-row"><span>榜单</span><div class="segmented"><button type="button" class="${state.marketRanking === "hot" ? "active" : ""}" data-market-ranking="hot" aria-pressed="${state.marketRanking === "hot"}">热销榜</button><button type="button" class="${state.marketRanking === "rising" ? "active" : ""}" data-market-ranking="rising" aria-pressed="${state.marketRanking === "rising"}">飙升榜</button></div><span>周期</span><div class="segmented"><button type="button" class="${state.marketPeriod === 7 ? "active" : ""}" data-market-period="7" aria-pressed="${state.marketPeriod === 7}">近7天</button><button type="button" class="${state.marketPeriod === 30 ? "active" : ""}" data-market-period="30" aria-pressed="${state.marketPeriod === 30}">近30天</button></div></div>
    <div class="market-category-tabs">${marketCategoryButtons(status.categories)}</div>
  </section>
  ${data.available ? "" : unavailableMarkup}
  <section class="market-keyword-radar"><div class="market-result-head"><div><strong>近7天类目热词</strong><span>俄文原词 + 中文翻译 · 点击商品后查看匹配关系</span></div><span>${number(keywordData.total)} 个已接入词</span></div><div class="market-keyword-strip">${keywordData.items.map(marketKeywordCard).join("") || `<p class="market-no-keyword">当前类目还没有可追溯的官方搜索词</p>`}</div></section>
  ${data.available ? resultsMarkup : ""}`;
}

function scheduleMarketSearch(value, immediate = false) {
  clearTimeout(state.marketSearchTimer);
  const query = String(value ?? "").trim();
  const apply = async () => {
    state.marketSearchTimer = null;
    state.marketQuery = query;
    state.marketPage = 1;
    searchInput.value = query;
    window.scrollTo({top:0, left:0, behavior:"auto"});
    await renderMarket();
  };
  if (immediate) return apply();
  state.marketSearchTimer = setTimeout(() => apply().catch((error) => toast(error.message, "error")), 280);
}

function marketDetailMetric(label, value, hint = "") {
  return `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong>${hint ? `<small>${escapeHtml(hint)}</small>` : ""}</div>`;
}

async function renderMarketDetail(sourceProductId) {
  window.scrollTo({top:0, left:0, behavior:"auto"});
  root.innerHTML = `<section class="market-detail-loading" aria-live="polite"><span></span><strong>正在补齐商品主图与关键词</strong><small>只会保存可确认的公开真实主图</small></section>`;
  const product = await api(`/api/workbench/market-intelligence/products/${encodeURIComponent(sourceProductId)}`);
  const facts = product.facts || {};
  const fbs = product.fbs_assessment || {};
  const keywords = product.keywords || [];
  const flags = (fbs.risk_flags || []).map((item) => fbsRiskLabels[item] || item);
  const matchedChineseName = keywords.find((item) => item.keyword_zh && item.keyword_zh !== "unknown")?.keyword_zh || marketCategoryLabels[state.marketCategory] || "商品";
  const chineseOverview = `中文概览：${matchedChineseName} · 近30天销量 ${marketFact(facts.ordered_units, "件")} · 成交均价 ${marketFact(facts.average_purchase_price_rub, "₽")}`;
  const hasRealImage = product.image_url && product.image_url !== "unknown";
  const imageMarkup = hasRealImage
    ? `<div class="market-detail-image"><img src="${escapeHtml(product.image_url)}" alt="${escapeHtml(product.title_ru)}"></div>`
    : `<div class="market-detail-image market-detail-image-syncing"><span></span><strong>主图同步中</strong><small>公开页面暂时无法确认主图时，不会使用相似图代替</small></div>`;
  const keywordMarkup = keywords.map((item, index) => `<span class="market-keyword-pill${index >= 15 ? " market-keyword-extra" : ""}" ${index >= 15 ? "hidden" : ""}><strong>${escapeHtml(item.keyword_ru)}</strong>${item.keyword_zh && item.keyword_zh !== "unknown" ? `<small>${escapeHtml(item.keyword_zh)}</small>` : ""}</span>`).join("");
  root.innerHTML = `<section class="market-detail-head"><button class="secondary-button" data-market-back>← 返回榜单</button><div><span>Ozon商品编号 ${escapeHtml(product.source_product_id)}</span><h2>${escapeHtml(product.title_ru)}</h2><p>${product.title_zh === "unknown" ? escapeHtml(chineseOverview) : escapeHtml(product.title_zh)}</p></div><a class="primary-button market-ozon-link" href="${escapeHtml(product.product_url)}" target="_blank" rel="noreferrer">打开Ozon商品卡</a></section>
  <section class="market-detail-layout"><div class="market-detail-main">
    <article class="panel market-detail-media-card">${imageMarkup}<div><span>商品主图</span><strong>${hasRealImage ? "公开真实主图已缓存" : "正在等待公开主图"}</strong><small>${hasRealImage ? "榜单卡片和详情页共用同一张缓存图片" : "点开商品会尝试同步，每日后台也会逐步补齐"}</small></div></article>
    <article class="panel market-detail-section"><div class="panel-head"><h3>市场表现</h3><span>官方榜单字段</span></div><div class="market-detail-metrics">
      ${marketDetailMetric("近30天销售额", marketFact(facts.ordered_amount_rub, "₽"))}
      ${marketDetailMetric("近30天销量", marketFact(facts.ordered_units, "件"))}
      ${marketDetailMetric("销售额增长", marketFact(facts.ordered_amount_growth_percent, "%"))}
      ${marketDetailMetric("成交均价", marketFact(facts.average_purchase_price_rub, "₽"))}
      ${marketDetailMetric("搜索与类目曝光", marketFact(facts.search_catalog_impressions, "次"))}
      ${marketDetailMetric("商品卡访问", marketFact(facts.product_card_visits, "次"))}
      ${marketDetailMetric("搜索加购率", marketFact(facts.search_to_cart_percent, "%"))}
      ${marketDetailMetric("签收率", marketFact(facts.buyout_share_percent, "%"))}
    </div></article>
    <article class="panel market-detail-section"><div class="panel-head"><h3>关键词</h3><span>${keywords.length} 个</span></div><div class="market-keyword-list">${keywordMarkup || `<p class="market-keyword-empty">关键词整理中，请稍后重新打开</p>`}</div>${keywords.length > 15 ? `<button type="button" class="market-keyword-more" data-market-keywords-more aria-expanded="false">展开更多</button>` : ""}<p class="market-keyword-notice">关键词根据公开数据和商品信息整理</p></article>
  </div><aside class="market-detail-side">
    <article class="panel market-fbs-panel"><span>FBS适配度</span><strong>${fbs.score === "unknown" ? "暂无" : `${number(Number(fbs.score), 0)}分`}</strong><p>${escapeHtml(fbsRecommendationLabels[fbs.recommendation] || "待判断")}</p><small>数据完整度 ${number(fbs.data_completeness || 0)}%</small>${flags.length ? `<div class="market-risk-list">${flags.map((item) => `<em>${escapeHtml(item)}</em>`).join("")}</div>` : ""}<footer>${escapeHtml(fbs.notice || "")}</footer></article>
    <article class="panel market-fact-list"><h3>商品事实</h3><div><span>一级类目</span><strong>${escapeHtml(facts.category_level_1 || "暂无")}</strong></div><div><span>三级类目</span><strong>${escapeHtml(facts.category_level_3 || "暂无")}</strong></div><div><span>品牌</span><strong>${escapeHtml(facts.brand || "暂无")}</strong></div><div><span>履约方式</span><strong>${escapeHtml(facts.fulfillment_model || "暂无")}</strong></div><div><span>商品体积</span><strong>${marketFact(facts.product_volume_liters, "L")}</strong></div><div><span>FBS Ozon成本占比</span><strong>${marketFact(facts.ozon_cost_share_fbs_percent, "%")}</strong></div></article>
  </aside></section>`;
  requestAnimationFrame(() => {
    window.scrollTo({top:0, left:0, behavior:"auto"});
    root.focus({preventScroll:true});
  });
}

function inboxCard(product, selectable = false) {
  const action = {...(product.primary_action || {key:"status", label:"查看进度"})};
  const failed = isNeedsAttentionStatus(product.raw_status);
  const priorityUpload = (state.queueSummary?.priority_product_ids || []).includes(product.product_id);
  const running = isPipelineRunningLike(product) && !priorityUpload;
  if (running) {
    action.key = "status";
    action.label = "查看进度";
  }
  const error = failed ? friendlyErrorInfo(product) : null;
  const failureEvents = (failed || product.attention_required) ? pipelineFailureEvents(product).slice(0, 3) : [];
  const failureList = failureEvents.length ? `<div class="card-failure-list">${failureEvents.map((event) => `<button type="button" data-error-edit="${escapeHtml(product.product_id)}" data-error-step="${escapeHtml(event.step)}"><strong>${escapeHtml(event.title)}</strong><span>${escapeHtml(event.reason)}</span></button>`).join("")}</div>` : "";
  const alert = priorityUpload
    ? `<div class="card-info-message upload-priority"><strong>优先上传</strong><span>${escapeHtml(state.queueSummary?.message || "已进入优先上传队列")}</span></div>`
    : product.pending_question?.question
    ? `<p class="card-alert">需要确认：${escapeHtml(product.pending_question.question)}</p>`
      : error
      ? `<div class="card-error-message"><strong>${escapeHtml(error.title)}</strong><span>${escapeHtml(error.message)}</span><small>停在：${escapeHtml(stepLabel(error.step || product.current_step))}</small></div>`
      : product.handoff_message
        ? `<div class="card-info-message"><strong>等待Ozon生成商品卡</strong><span>${escapeHtml(product.handoff_message)}</span></div>`
      : "";
  const actionLabel = priorityUpload ? "优先上传处理中" : failed ? "查看原因并继续" : running ? `查看进度（${product.progress || 0}%）` : action.label;
  return `<article class="list-card task-card ${product.attention_required ? "task-card-attention" : ""} ${priorityUpload ? "task-card-priority-upload" : ""}" data-card-product="${product.product_id}">${selectable ? `<input class="batch-select" type="checkbox" data-select-batch-product="${product.product_id}" ${state.selectedBatchProducts.has(product.product_id) ? "checked" : ""}>` : `<span></span>`}${thumbnail(product)}<div><h3>${escapeHtml(product.title_cn)}</h3><p>${product.product_id} · ${product.sku_count} SKU · ${dateText(product.captured_at)}</p><p><span class="workflow-bucket">${escapeHtml(product.workflow_bucket)}</span> · ${escapeHtml(stepLabel(product.current_step))} · ${product.progress}%</p>${alert}${failureList}</div><div class="list-actions">${failed ? `<button class="secondary-button" data-error-edit="${product.product_id}">立即修改</button>` : ""}<button class="primary-button" data-primary-action="${escapeHtml(action.key)}" data-product-id="${product.product_id}" ${priorityUpload ? "disabled" : ""}>${escapeHtml(actionLabel)}</button><a class="source-link" href="${escapeHtml(product.source_url)}" target="_blank" rel="noreferrer">查看1688来源</a></div>${productMenu(product.product_id)}</article>`;
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
  confirmButton.innerHTML = `<span class="ph ph-trash" aria-hidden="true"></span>确认彻底删除`;
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
    button.innerHTML = `<span class="ph ph-trash" aria-hidden="true"></span>重新执行清理`;
  }
}

function thumbnail(product) {
  return product.thumbnail_url
    ? `<img src="${product.thumbnail_url}" alt="" loading="lazy">`
    : `<span class="thumb-placeholder">无图</span>`;
}

async function renderBatches() {
  const data = await api("/api/workbench/batches");
  const plan = data.execution_plan || {};
  root.innerHTML = `<section class="section-head"><div><h2>任务状态</h2><p>${data.running_pid ? "当前有任务正在运行，会持续到上架或明确失败；可手动停止并保留断点" : "当前没有运行中的任务"} · 排队中兼容商品会自动合并</p></div><div class="toolbar"><span class="bounded-parallel-summary"><strong>${escapeHtml(plan.label || "受控并行")}</strong>${escapeHtml(plan.summary || "资料并行，生图和上传限流")}</span><button class="secondary-button" data-action="open-batch">运行新任务</button><button class="danger-button" data-batch-action="stop" data-batch-action-source="manual_toolbar_v2" ${data.running_pid ? "" : "disabled"}><span class="ph ph-stop-circle" aria-hidden="true"></span>手动停止</button></div></section><div class="table-wrap"><table class="data-table"><thead><tr><th>批次</th><th>状态</th><th>目标店铺</th><th>模式</th><th>商品</th><th>成功/失败/未完成</th><th>进度</th><th>操作</th></tr></thead><tbody>${data.items.map((batch) => {
    const waitingUpload = batch.status === "AWAITING_MANUAL_UPLOAD";
    const readyProductId = batch.ready_product_ids?.[0];
    const statusMarkup = batch.status === "AWAITING_CONFIRMATION"
      ? `<span class="status-pill medium">等待批量确认</span>`
      : waitingUpload
        ? `<span class="status-pill medium">待确认上传</span>`
        : escapeHtml(display(batch.display_status || batch.status));
    const actionMarkup = batch.status === "AWAITING_CONFIRMATION"
      ? `<span class="batch-confirm-actions"><button class="primary-button" data-open-confirmation="${escapeHtml(batch.batch_id)}">继续确认</button><button class="danger-button" data-cancel-confirmation="${escapeHtml(batch.batch_id)}">取消任务</button></span>`
      : waitingUpload && readyProductId
        ? `<button class="primary-button" data-open-product="${escapeHtml(readyProductId)}">检查并上传</button>`
        : dateText(batch.created_at || batch.started_at);
    return `<tr><td>${escapeHtml(batch.batch_id)}</td><td>${statusMarkup}</td><td>${escapeHtml((batch.target_store_ids || []).join("、") || "未选择")}</td><td><span class="status-pill ${batch.auto_upload ? "auto-badge" : "manual-badge"}">${batch.auto_upload ? "自动处理并上传" : "手动检查后上传"}</span><small class="batch-parallel-plan">${escapeHtml(batch.execution_plan?.summary || "受控并行")}</small></td><td>${batch.product_count || 0}</td><td>${batch.success_count || 0} / ${batch.failed_count || 0} / ${batch.incomplete_count || 0}</td><td>${batch.progress || 0}%</td><td>${actionMarkup}</td></tr>`;
  }).join("")}</tbody></table></div>`;
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
  root.innerHTML = `<section class="section-head"><div><h2>问题中心</h2><p>${data.items.length} 条商品问题 · ${data.rules.length} 条管理规则</p></div></section><div class="settings-grid">${data.rules.map((rule) => `<article class="setting-block"><div class="store-card-head"><div><h3>${escapeHtml(rule.name)}</h3><span class="status-pill ${rule.action === "block" ? "failed" : rule.action === "review" ? "medium" : "low"}">${actionLabel[rule.action]}</span></div>${rule.immutable ? `<span>硬规则</span>` : `<select data-risk-rule="${rule.id}"><option value="allow" ${rule.action === "allow" ? "selected" : ""}>自动通过</option><option value="review" ${rule.action === "review" ? "selected" : ""}>人工确认</option><option value="block" ${rule.action === "block" ? "selected" : ""}>禁止跳过</option></select>`}</div></article>`).join("")}</div><section class="section-head" style="margin-top:16px"><div><h2>需要处理的商品</h2><p>修复后只重新执行受影响步骤</p></div></section><div class="risk-list">${data.items.map((item) => `<article class="risk-row ${item.level}"><strong>${escapeHtml(item.product_id)}</strong>${riskPill(item)}<p>${escapeHtml(item.message)}</p><button class="secondary-button" data-open-product="${item.product_id}">修复</button></article>`).join("") || empty("当前没有需要处理的问题")}</div>`;
}

async function renderShops() {
  const data = await api("/api/workbench/shops");
  state.shops = data.items;
  root.innerHTML = `<section class="section-head"><div><h2>Ozon店铺</h2><p>凭证只显示配置状态，不在页面、日志或浏览器存储暴露</p></div><button class="primary-button" data-action="add-shop"><span class="ph ph-plus" aria-hidden="true"></span>添加店铺</button></section><div class="settings-grid">${data.items.map((shop) => `<article class="setting-block"><div class="store-card-head"><div><h3>${escapeHtml(shop.display_name)}</h3><span class="status-pill ${shop.connection_status === "connected" ? "low" : shop.connection_status === "failed" ? "failed" : "medium"}">${storeStatusLabel(shop.connection_status)}</span></div><span>${shop.enabled ? "已启用" : "已禁用"}</span></div><div class="summary-row"><span>凭证</span><strong>${shop.credentials_display}</strong></div><div class="summary-row"><span>采购币种</span><strong>${escapeHtml(shop.currency)}</strong></div><div class="summary-row"><span>最近验证</span><strong>${dateText(shop.last_validated_at)}</strong></div><div class="summary-row"><span>关联商品 / 待处理</span><strong>${shop.associated_product_count} / ${shop.pending_task_count}</strong></div>${shop.last_validation_error ? `<p class="form-help">连接失败：${escapeHtml(shop.last_validation_error)}</p>` : ""}<div class="store-card-actions"><button class="primary-button" data-store-action="validate" data-store-id="${shop.id}">只读测试</button><button class="secondary-button" data-store-action="edit" data-store-id="${shop.id}">编辑</button><button class="secondary-button" data-store-action="toggle" data-store-id="${shop.id}" data-enabled="${shop.enabled ? "false" : "true"}">${shop.enabled ? "禁用" : "启用"}</button><button class="danger-button" data-store-action="delete" data-store-id="${shop.id}">删除本地配置</button></div></article>`).join("") || empty("没有店铺配置")}</div>`;
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
  state.batchProducts = state.selectedBatchProducts.size ? [...state.selectedBatchProducts] : actionableProductIds(state.products);
  const uploadBatch = batchProductsAreUploadReady(state.batchProducts);
  document.getElementById("batch-product-summary").textContent = uploadBatch
    ? `本次选择 ${state.batchProducts.length} 个待上传商品。确认后会进入优先上传队列，不会重跑文字和图片。`
    : `本批次包含 ${state.batchProducts.length} 个商品。默认选择全部已验证店铺，也可以取消其中的店铺。`;
  document.getElementById("batch-store-options").innerHTML = shops.map((shop) => { const available = shop.enabled && shop.connection_status === "connected"; return `<label class="store-option ${available ? "" : "unavailable"}"><input type="checkbox" data-batch-store="${escapeHtml(shop.id)}" ${available ? "checked" : "disabled"}><span><strong>${escapeHtml(shop.display_name)}</strong><small>${storeStatusLabel(shop.connection_status)} · ${shop.credentials_display}</small></span></label>`; }).join("") || `<p class="form-help">没有已配置店铺，请先到店铺中心添加并完成只读验证。</p>`;
  document.getElementById("batch-mode-summary").textContent = uploadBatch
    ? "上传模式：只提交已经等待人工检查的商品；如果本地上传包缺失，后台会先补齐，补不齐会显示具体原因。"
    : state.workbenchSettings.auto_mode_enabled
    ? "自动模式：确认本批次后会连续完成文字、图片和多店提交；失败商品隔离，其他商品继续。"
    : "手动模式：先确认类目、SKU、店铺和价格，再生成文字与图片；最后由你确认后才上传。";
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

function batchProductsAreUploadReady(ids = state.batchProducts) {
  return Boolean(ids?.length) && ids.every((id) => {
    const product = state.products.find((item) => item.product_id === id);
    return product?.primary_action?.key === "review_upload";
  });
}

function updateInboxBatchButtonLabel() {
  const button = document.querySelector(".fusion-task-toolbar [data-action='open-batch']");
  if (!button) return;
  button.textContent = batchProductsAreUploadReady([...state.selectedBatchProducts]) ? "继续上传所选" : "运行所选";
}

function updateBatchDialogButton() {
  const button = document.getElementById("confirm-create-batch");
  const count = selectedBatchStores().length;
  button.disabled = !count || !state.batchProducts.length;
  button.textContent = count
    ? batchProductsAreUploadReady(state.batchProducts)
      ? `确认上传 · ${count} 家店铺`
      : `创建批次 · ${count} 家店铺`
    : "请选择目标店铺";
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
    await api(`/api/workbench/products/${state.currentProductId}/images/${encodeURIComponent(slot)}`, {method:"PATCH", body:JSON.stringify({action:"accept"})});
    toast("图片已加入历史确认区", "success");
    return refreshCurrentProduct();
  }
  if (action === "delete") {
    if (!confirm(`拒绝图片 ${slot}？候选图会移入“已拒绝”，不会删除原始素材，也不影响Ozon后台。`)) return;
    await api(`/api/workbench/products/${state.currentProductId}/images/${encodeURIComponent(slot)}`, {method:"DELETE"});
    toast("图片已移入已拒绝");
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
  root.innerHTML = `<section class="section-head"><div><h2>已安装能力</h2><p>${data.items.length} 个本地能力</p></div></section><div class="table-wrap"><table class="data-table"><thead><tr><th>名称</th><th>来源</th><th>状态</th><th>说明</th></tr></thead><tbody>${data.items.map((skill) => `<tr><td>${escapeHtml(skill.name)}</td><td>${escapeHtml(skill.source)}</td><td><span class="status-pill low">已启用</span></td><td>${escapeHtml(skill.summary)}</td></tr>`).join("")}</tbody></table></div>`;
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
  if (!state.session?.can_manage_settings) throw new Error("只有主电脑可以打开系统设置");
  await loadWorkbenchSettings();
  const operator = state.session.operator || {};
  root.innerHTML = `<section class="section-head"><div><h2>主电脑设置</h2><p>工作室电脑自动识别、商品任务全部共享；店铺凭证与系统设置只在主电脑管理。</p></div></section><div class="settings-grid">
    <article class="setting-block primary-setting"><h3>处理模式</h3><label class="toggle-row"><span><strong>${state.workbenchSettings.auto_mode_enabled ? "自动连续流程" : "手动检查模式"}</strong><small>${state.workbenchSettings.auto_mode_enabled ? "运行后连续完成文字、图片和上传；失败商品会隔离" : "运行前确认商品信息，生成完成后由你确认才上传"}</small></span><input type="checkbox" data-global-auto-mode ${state.workbenchSettings.auto_mode_enabled ? "checked" : ""}></label><div class="summary-row"><span>经验学习</span><strong>同类目同类修改出现2次后才启用</strong></div></article>
    <article class="setting-block"><div class="store-card-head"><div><h3>设备连接</h3><span class="status-pill success">自动识别</span></div></div><p class="form-help">局域网电脑打开主电脑工作台即可使用，不再填写访问码。商品、批次、失败处理和预览窗口对所有电脑一致。</p><div class="summary-row"><span>当前设备</span><strong>${escapeHtml(operator.device_name || "主电脑")}</strong></div><div class="summary-row"><span>操作记录</span><strong>按电脑自动留痕</strong></div><div class="summary-row"><span>设置权限</span><strong>仅主电脑</strong></div></article>
    <article class="setting-block"><h3>永久安全规则</h3><div class="summary-row"><span>Ozon</span><strong>禁止重复创建商品，处理中禁止重复提交</strong></div><div class="summary-row"><span>库存</span><strong>不提交库存字段，库存接口永久禁用</strong></div><div class="summary-row"><span>商品</span><strong>真实性失败需要处理，单商品最多10个SKU</strong></div><div class="summary-row"><span>图片</span><strong>失败只重做单图，保留真实SKU差异</strong></div></article>
    <article class="setting-block"><h3>工作室数据导出</h3><div class="store-card-actions"><a class="secondary-button" href="/api/workbench/export/csv">CSV</a><a class="secondary-button" href="/api/workbench/export/xlsx">Excel</a><a class="secondary-button" href="/api/workbench/export/json">JSON</a><a class="secondary-button" href="/api/workbench/export/backup">工作室备份</a></div><p class="form-help">导出包含工作室共享商品，自动排除所有明文店铺密钥。</p></article>
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

document.getElementById("refresh-all").addEventListener("click", () => {
  invalidateProductsCache();
  navigate(state.view, {force:true});
});
document.getElementById("safe-exit-workbench").addEventListener("click", async () => {
  const status = await pollSystemStatus({notify:false});
  if (!status) return toast("暂时无法读取后台状态，请稍后再试", "error");
  if (status.batch_running && !confirm("当前还有任务运行。安全退出会在最近图片或文件断点停止，完成内容会保留。确认退出？")) return;
  try {
    const result = await api("/api/workbench/system/safe-exit", {
      method:"POST",
      body:JSON.stringify({confirm_active_tasks:status.batch_running}),
    });
    toast(result.message, "success", 10000);
  } catch (error) { toast(error.message, "error"); }
});
document.getElementById("global-auto-mode").addEventListener("change", (event) => {
  updateGlobalAutoMode(event.target.checked).then(() => navigate(state.view)).catch((error) => {
    event.target.checked = !event.target.checked;
    toast(error.message, "error");
  });
});
searchInput.addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  if (state.view === "market") {
    event.preventDefault();
    scheduleMarketSearch(searchInput.value, true).catch((error) => toast(error.message, "error"));
  } else if (state.view === "finance") {
    event.preventDefault();
    if (!["products", "orders"].includes(state.financeTab)) state.financeTab = "orders";
    renderFinance().catch((error) => toast(error.message, "error"));
  } else navigate("review", {query: searchInput.value});
});

searchInput.addEventListener("input", () => {
  if (state.view === "market") scheduleMarketSearch(searchInput.value);
});

searchInput.addEventListener("search", () => {
  if (state.view === "market") scheduleMarketSearch(searchInput.value, true).catch((error) => toast(error.message, "error"));
});

root.addEventListener("input", (event) => {
  if (event.target.matches("[data-market-search]")) {
    scheduleMarketSearch(event.target.value);
    return;
  }
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
  if (field) {
    if (blockTerminalProductMutation()) return;
    scheduleDraftSave({[field]: collectDraftField(field, event.target.value)});
  }
  const attributeId = event.target.dataset.attributeId;
  if (attributeId) {
    if (blockTerminalProductMutation()) return;
    scheduleDraftSave({attributes: {[attributeId]: event.target.value || "unknown"}});
  }
  const factSkuId = event.target.dataset.skuFactSku;
  const skuField = event.target.dataset.skuField;
  if (factSkuId && skuField) {
    if (blockTerminalProductMutation()) return;
    const rawValue = event.target.value;
    const value = event.target.type === "number" ? (rawValue.trim() === "" ? "" : Number(rawValue)) : rawValue;
    if (event.target.type === "number" && value !== "" && !(Number.isFinite(value) && value > 0)) return;
    scheduleDraftSave({sku_overrides: {[factSkuId]: {[skuField]: value}}});
  }
  const skuId = event.target.dataset.skuPrice;
  if (skuId && Number(event.target.value) > 0) {
    if (blockTerminalProductMutation()) return;
    scheduleDraftSave({sku_overrides: {[skuId]: {selling_price_cny:Number(event.target.value)}}});
  }
});

root.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || !event.target.matches("[data-market-search]")) return;
  event.preventDefault();
  scheduleMarketSearch(event.target.value, true).catch((error) => toast(error.message, "error"));
});

root.addEventListener("search", (event) => {
  if (!event.target.matches("[data-market-search]")) return;
  scheduleMarketSearch(event.target.value, true).catch((error) => toast(error.message, "error"));
});

root.addEventListener("change", (event) => {
  if (event.target.matches("[data-finance-currency-select]")) {
    state.financeCurrency = event.target.value;
    renderFinance().catch((error) => toast(error.message, "error"));
    return;
  }
  if (event.target.matches("[data-finance-store]")) {
    state.financeStoreId = event.target.value;
    renderFinance().catch((error) => toast(error.message, "error"));
    return;
  }
  if (event.target.matches("[data-finance-period]")) {
    state.financePeriod = event.target.value;
    if (state.financePeriod !== "custom") [state.financeDateFrom, state.financeDateTo] = financePeriodDates(state.financePeriod);
    renderFinance().catch((error) => toast(error.message, "error"));
    return;
  }
  if (event.target.matches("[data-finance-date-from], [data-finance-date-to]")) {
    state.financePeriod = "custom";
    state.financeDateFrom = root.querySelector("[data-finance-date-from]")?.value || state.financeDateFrom;
    state.financeDateTo = root.querySelector("[data-finance-date-to]")?.value || state.financeDateTo;
    renderFinance().catch((error) => toast(error.message, "error"));
    return;
  }
  if (event.target.matches("[data-finance-import-file]")) {
    const file = event.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        state.financeImportFileName = file.name;
        state.financeImportContent = String(reader.result).split(",", 2)[1] || "";
        state.financeImportPreview = await api("/api/workbench/finance/imports/preview", {method:"POST", body:JSON.stringify({file_name:file.name, content_base64:state.financeImportContent})});
        toast(`已识别 ${state.financeImportPreview.row_count} 行，请核对字段`, "success");
        await renderFinance();
      } catch (error) { toast(error.message, "error"); }
    };
    reader.readAsDataURL(file);
    return;
  }
  if (event.target.id === "auto-advance") {
    if (blockTerminalProductMutation()) return;
    state.autoAdvance = event.target.checked;
    scheduleDraftSave({auto_advance: state.autoAdvance});
  }
  if (event.target.matches("[data-product-store]")) {
    if (blockTerminalProductMutation()) return;
    const storeId = event.target.dataset.productStore;
    if (event.target.checked) state.selectedStoreIds.add(storeId); else state.selectedStoreIds.delete(storeId);
    refreshStoreSelectionUi();
  }
  if (event.target.matches("[data-select-batch-product]")) {
    const productId = event.target.dataset.selectBatchProduct;
    if (event.target.checked) state.selectedBatchProducts.add(productId); else state.selectedBatchProducts.delete(productId);
    renderInbox();
  }
  if (event.target.matches("[data-select-all-products]")) {
    state.selectedBatchProducts = event.target.checked ? new Set(actionableProductIds(state.products)) : new Set();
    renderInbox();
  }
  if (event.target.matches("[data-replace-file]")) {
    if (blockTerminalProductMutation()) return;
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
      .then(() => toast("问题规则已保存", "success"))
      .catch((error) => { toast(error.message, "error"); renderRisks(); });
  }
  if (event.target.matches("[data-global-auto-mode]")) {
    updateGlobalAutoMode(event.target.checked).then(() => renderSettings()).catch((error) => toast(error.message, "error"));
  }
});

root.addEventListener("submit", async (event) => {
  if (!event.target.matches("[data-finance-other-form]")) return;
  event.preventDefault();
  const form = new FormData(event.target);
  const payload = Object.fromEntries(form.entries());
  try {
    await api("/api/workbench/finance/other-entries", {method:"POST", body:JSON.stringify(payload)});
    toast("其他收支已加入本期利润", "success");
    await renderFinance();
  } catch (error) { toast(error.message, "error"); }
});

async function updateGlobalAutoMode(enabled) {
  state.workbenchSettings = await api("/api/workbench/settings", {method:"PATCH", body:JSON.stringify({auto_mode_enabled:Boolean(enabled)})});
  await loadWorkbenchSettings();
  toast(enabled ? "自动模式已开启：运行后会自动生成并上传" : "手动模式已开启：你点击上传才会提交", "success");
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
  const financeTab = event.target.closest("[data-finance-tab]");
  if (financeTab) { state.financeTab = financeTab.dataset.financeTab; return renderFinance(); }
  const financeCurrency = event.target.closest("[data-finance-currency]");
  if (financeCurrency) { state.financeCurrency = financeCurrency.dataset.financeCurrency; return renderFinance(); }
  const financeExport = event.target.closest("[data-finance-export]");
  if (financeExport) {
    const params = new URLSearchParams({store_id:state.financeStoreId, date_from:state.financeDateFrom, date_to:state.financeDateTo});
    window.location.href = `/api/workbench/finance/export/${encodeURIComponent(financeExport.dataset.financeExport)}?${params}`;
    return;
  }
  const financeSync = event.target.closest("[data-finance-sync]");
  if (financeSync) {
    if (!confirm("只读同步全部已启用店铺最近90天的订单和Finance数据？不会修改Ozon商品或库存。")) return;
    financeSync.disabled = true; financeSync.textContent = "正在只读同步";
    try {
      const result = await api("/api/workbench/finance/sync", {method:"POST"});
      const success = (result.stores || []).filter((item) => item.status === "success").length;
      if (!result.complete) {
        const failed = (result.stores || []).find((item) => item.status === "failed");
        toast(`同步已安全停止：${failed?.error || "部分店铺失败，剩余请求已取消"}`, "error", 9000);
        return renderFinance();
      }
      toast(`同步完成：${success} 家店铺成功`, "success", 7000);
      return renderFinance();
    } catch (error) { financeSync.disabled = false; financeSync.textContent = "立即同步"; return toast(error.message, "error"); }
  }
  if (event.target.closest("[data-finance-import-commit]")) {
    if (!root.querySelector("[data-finance-import-confirm]")?.checked) return toast("请先确认金额列和字段对应关系", "error");
    const mapping = {};
    root.querySelectorAll("[data-finance-map-source]").forEach((select) => { if (select.value) mapping[select.dataset.financeMapSource] = select.value; });
    try {
      const result = await api("/api/workbench/finance/imports/commit", {method:"POST", body:JSON.stringify({file_name:state.financeImportFileName, content_base64:state.financeImportContent, file_kind:state.financeImportPreview.file_kind, mapping})});
      state.financeImportPreview = null; state.financeImportContent = null; state.financeImportFileName = null;
      toast(`已导入 ${result.inserted_count + result.updated_count} 行，可按批次回滚`, "success", 7000);
      return renderFinance();
    } catch (error) { return toast(error.message, "error"); }
  }
  const financeRollback = event.target.closest("[data-finance-rollback]");
  if (financeRollback) {
    if (!confirm("只撤销这个导入批次，其他批次和之后新增的数据会保留。确认回滚？")) return;
    try {
      const result = await api(`/api/workbench/finance/imports/${encodeURIComponent(financeRollback.dataset.financeRollback)}/rollback`, {method:"POST"});
      toast(`已回滚 ${result.changes_reversed} 项变更`, "success");
      return renderFinance();
    } catch (error) { return toast(error.message, "error"); }
  }
  const financeDeleteOther = event.target.closest("[data-finance-delete-other]");
  if (financeDeleteOther) {
    if (!confirm("删除这条其他收支记录？")) return;
    try {
      await api(`/api/workbench/finance/other-entries/${encodeURIComponent(financeDeleteOther.dataset.financeDeleteOther)}`, {method:"DELETE"});
      toast("其他收支记录已删除", "success");
      return renderFinance();
    } catch (error) { return toast(error.message, "error"); }
  }
  const marketRanking = event.target.closest("[data-market-ranking]");
  if (marketRanking) { state.marketRanking = marketRanking.dataset.marketRanking; state.marketPage = 1; window.scrollTo({top:0, left:0, behavior:"auto"}); return renderMarket(); }
  const marketCategory = event.target.closest("[data-market-category]");
  if (marketCategory) { state.marketCategory = marketCategory.dataset.marketCategory; state.marketPage = 1; window.scrollTo({top:0, left:0, behavior:"auto"}); return renderMarket(); }
  const marketPeriod = event.target.closest("[data-market-period]");
  if (marketPeriod) { state.marketPeriod = Number(marketPeriod.dataset.marketPeriod); state.marketPage = 1; window.scrollTo({top:0, left:0, behavior:"auto"}); return renderMarket(); }
  const marketPage = event.target.closest("[data-market-page]");
  if (marketPage && !marketPage.disabled) { state.marketPage = Number(marketPage.dataset.marketPage); window.scrollTo({top:0, behavior:"smooth"}); return renderMarket(); }
  const marketProduct = event.target.closest("[data-market-product]");
  if (marketProduct) { window.scrollTo({top:0, left:0, behavior:"auto"}); return renderMarketDetail(marketProduct.dataset.marketProduct); }
  if (event.target.closest("[data-market-back]")) { window.scrollTo({top:0, left:0, behavior:"auto"}); return renderMarket(); }
  const skuBindingChoice = event.target.closest("[data-sku-image-binding-choice]");
  if (skuBindingChoice) {
    event.preventDefault();
    if (skuBindingChoice.disabled || blockTerminalProductMutation()) return;
    skuBindingChoice.disabled = true;
    try {
      await saveSkuImageBinding(skuBindingChoice.dataset.skuImageBindingChoice, skuBindingChoice.dataset.selectedImagePath || "");
    } catch (error) {
      skuBindingChoice.disabled = false;
      toast(error.message, "error");
    }
    return;
  }
  const marketKeywordMore = event.target.closest("[data-market-keywords-more]");
  if (marketKeywordMore) {
    const expanded = marketKeywordMore.getAttribute("aria-expanded") === "true";
    marketKeywordMore.setAttribute("aria-expanded", String(!expanded));
    root.querySelectorAll(".market-keyword-extra").forEach((item) => { item.hidden = expanded; });
    marketKeywordMore.textContent = expanded ? "展开更多" : "收起";
    return;
  }
  const menuTrigger = event.target.closest("[data-menu-product]");
  if (menuTrigger) {
    const menu = menuTrigger.closest(".product-menu");
    document.querySelectorAll(".product-menu.open").forEach((item) => { if (item !== menu) item.classList.remove("open"); });
    menu.classList.toggle("open");
    return;
  }
  const deleteProduct = event.target.closest("[data-delete-product]");
  if (deleteProduct) return openDeleteDialog(deleteProduct.dataset.deleteProduct);
  const go = event.target.closest("[data-go]");
  if (go) return navigate(go.dataset.go);
  const inboxFilter = event.target.closest("[data-inbox-filter]");
  if (inboxFilter) { state.inboxFilter = inboxFilter.dataset.inboxFilter; return renderInbox(); }
  const errorEdit = event.target.closest("[data-error-edit]");
  if (errorEdit) {
    event.stopPropagation();
    try { return await openProductErrorEditor(errorEdit.dataset.errorEdit, errorEdit.dataset.errorStep || ""); }
    catch (error) { return toast(`打开修改页面失败：${error.message}`, "error"); }
  }
  const passiveProductCard = event.target.closest("[data-card-product]");
  if (passiveProductCard && !event.target.closest("button, a, input, select, textarea")) {
    state.currentProductId = passiveProductCard.dataset.cardProduct;
    state.currentImageSlot = null;
    return navigate("review", {productId: state.currentProductId});
  }
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
    if (["fix", "review_upload"].includes(actionKey)) {
      return startProductRun(productId, primaryAction);
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
    if (blockTerminalProductMutation()) return;
    state.reviewMode = mode.dataset.reviewMode;
    await saveDraft({review_mode: state.reviewMode});
    document.querySelectorAll("[data-review-mode]").forEach((button) => button.classList.toggle("active", button.dataset.reviewMode === state.reviewMode));
    if (state.reviewMode === "auto") toast("AI自动审核仍受真实性、平台硬规则和防重复规则限制");
    return;
  }
  const depth = event.target.closest("[data-review-depth]");
  if (depth) {
    if (blockTerminalProductMutation()) return;
    state.reviewDepth = depth.dataset.reviewDepth;
    await saveDraft({review_depth: state.reviewDepth});
    return renderReview({productId: state.currentProductId});
  }
  const futureFlowStep = event.target.closest("[data-future-flow-step]");
  if (futureFlowStep) {
    const step = futureFlowStep.dataset.futureFlowStep;
    if (futureFlowStep.classList.contains("failed")) {
      try { return await openProductErrorEditor(state.currentProductId, step); }
      catch (error) { return toast(`刷新失败状态失败：${error.message}`, "error"); }
    }
    const map = {collect_source:"content", product_analysis:"content", measurements:"price", image_qc:"risk", ozon_upload:"store"};
    const targetTab = map[step] || (["image_plan", "image_generation"].includes(step) ? "images" : ["category_match", "variant_rules", "field_completion"].includes(step) ? "category" : "content");
    root.querySelector(`[data-future-review-tab="${targetTab}"]`)?.click();
    return;
  }
  const futureReviewTab = event.target.closest("[data-future-review-tab]");
  if (futureReviewTab) {
    const tab = futureReviewTab.dataset.futureReviewTab;
    root.querySelectorAll("[data-future-review-tab]").forEach((button) => {
      const active = button.dataset.futureReviewTab === tab;
      button.classList.toggle("active", active);
      if (button.getAttribute("role") === "tab") button.setAttribute("aria-selected", String(active));
    });
    root.querySelectorAll("[data-future-review-pane]").forEach((pane) => {
      const active = pane.dataset.futureReviewPane === tab;
      pane.classList.toggle("active", active);
      pane.classList.toggle("hidden", !active);
    });
    return;
  }
  const imageCycle = event.target.closest("[data-image-cycle]");
  if (imageCycle && state.currentProduct?.images?.length) {
    const images = state.currentProduct.images;
    const currentIndex = Math.max(0, images.findIndex((item) => item.slot === state.currentImageSlot));
    const nextIndex = imageCycle.dataset.imageCycle === "prev" ? (currentIndex - 1 + images.length) % images.length : (currentIndex + 1) % images.length;
    state.currentImageSlot = images[nextIndex].slot;
    const workspace = document.getElementById("image-workspace");
    if (workspace) workspace.outerHTML = renderFutureImageStage(state.currentProduct);
    return;
  }
  const imageSelect = event.target.closest("[data-image-select]");
  if (imageSelect) {
    state.currentImageSlot = imageSelect.dataset.imageSelect;
    const workspace = document.getElementById("image-workspace");
    if (workspace) workspace.outerHTML = renderFutureImageStage(state.currentProduct);
    return;
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
    if (imageAction.dataset.imageAction !== "copy-url" && blockTerminalProductMutation()) return;
    const tile = imageAction.closest("[data-image-slot]");
    const slot = tile.dataset.imageSlot;
    state.currentImageSlot = slot;
    try { await handleImageAction(imageAction.dataset.imageAction, slot, tile); } catch (error) { toast(error.message, "error"); }
    return;
  }
  const removeTag = event.target.closest("[data-remove-tag]");
  if (removeTag) {
    if (blockTerminalProductMutation()) return;
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
  const retryFailedStores = event.target.closest("[data-retry-failed-stores]");
  if (retryFailedStores) {
    const storeIds = retryFailedStores.dataset.retryFailedStores.split(",").filter(Boolean);
    if (!confirm(`同时重试 ${storeIds.length} 家明确失败的店铺？已成功或处理中店铺不会重传。`)) return;
    retryFailedStores.disabled = true;
    try {
      const result = await api(`/api/workbench/products/${state.currentProductId}/stores/retry-failed`, {method:"POST", body:JSON.stringify({store_ids: storeIds})});
      toast(`已并行启动 ${result.store_ids.length} 家失败店铺：${result.batch_id}`, "success");
      return refreshCurrentProduct();
    } catch (error) { retryFailedStores.disabled = false; return toast(error.message, "error"); }
  }
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "toggle-queue") {
    state.queueCollapsed = !state.queueCollapsed;
    document.querySelector(".review-shell")?.classList.toggle("queue-collapsed", state.queueCollapsed);
    return;
  }
  if (action === "select-store-group") {
    if (blockTerminalProductMutation()) return;
    const preset = STORE_GROUP_PRESETS.find((item) => item.id === event.target.closest("[data-store-group]")?.dataset.storeGroup);
    if (!preset) return;
    const unavailable = preset.storeIds.filter((storeId) => {
      const shop = (state.currentProduct.stores || []).find((item) => item.id === storeId);
      return !shop || !shop.enabled || shop.connection_status !== "connected";
    });
    if (unavailable.length) return toast(`${preset.label} 有不可用店铺：${unavailable.join("、")}`, "error");
    state.selectedStoreIds = new Set(preset.storeIds);
    refreshStoreSelectionUi();
    return toast(`已选择 ${preset.label}（${preset.storeIds.length} 家跨主体店铺）`, "success");
  }
  if (action === "clear-store-selection") {
    if (blockTerminalProductMutation()) return;
    state.selectedStoreIds.clear();
    refreshStoreSelectionUi();
    return;
  }
  if (action === "save-product-stores") {
    if (blockTerminalProductMutation()) return;
    try {
      await api(`/api/workbench/products/${state.currentProductId}/stores`, {method:"PUT", body:JSON.stringify({store_ids:[...state.selectedStoreIds], overrides:collectStoreOverrides()})});
      toast(`已保存 ${state.selectedStoreIds.size} 家目标店铺`, "success");
      return refreshCurrentProduct();
    } catch (error) { return toast(error.message, "error"); }
  }
  if (action === "add-shop") return openStoreDialog();
  if (action === "change-category") {
    if (blockTerminalProductMutation()) return;
    return openCategoryDialog();
  }
  if (action === "edit-error") {
    if (skusNeedingReferenceBinding(state.currentProduct).some((sku) => sku.binding_required)) {
      root.querySelector('[data-future-review-tab="sku"]')?.click();
      root.querySelector("[data-sku-reference-binding-board]")?.scrollIntoView({block:"center", behavior:"smooth"});
      toast("请先给缺图SKU选择一张本商品采集图，绑定后再点继续生成。", "info", 5200);
      return;
    }
    try { return await openProductErrorEditor(state.currentProductId); }
    catch (error) { return toast(`打开修改页面失败：${error.message}`, "error"); }
  }
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
    if (blockTerminalProductMutation()) return;
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
    if (blockTerminalProductMutation()) return;
    const hint = document.getElementById("visual-set-hint")?.value.trim() || "";
    try {
      const result = await api(`/api/workbench/products/${state.currentProductId}/visual-preference`, {method:"PUT", body:JSON.stringify({set_hint:hint})});
      toast(result.invalidated_steps.length ? "风格意见已保存；旧图片方案已失效，重新运行后生效" : "风格意见已保存", "success");
      return refreshCurrentProduct();
    } catch (error) { return toast(error.message, "error"); }
  }
  if (action === "run-product") {
    const button = event.target.closest("button");
    return startProductRun(state.currentProductId, button);
  }
  if (action === "refresh-ozon") {
    try { const result = await api("/api/inbox/refresh-ozon-status", {method: "POST"}); toast(`已只读刷新 ${result.synced_product_ids.length} 个任务`); await navigate(state.view); } catch (error) { toast(error.message, "error"); }
  }
  const batchAction = event.target.closest("[data-batch-action]");
  if (batchAction) {
    try { const result = await api("/api/workbench/batches/control", {method: "POST", body: JSON.stringify({action: batchAction.dataset.batchAction, source: batchAction.dataset.batchActionSource || ""})}); toast(result.message || `批次操作完成：${result.status}`, "success"); await (state.view === "review" ? refreshCurrentProduct() : renderBatches()); } catch (error) { toast(error.message, "error"); }
  }
  const suggestion = event.target.closest("[data-suggestion]");
  if (suggestion) {
    if (blockTerminalProductMutation()) return;
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
  if (blockTerminalProductMutation()) return;
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
    toast("回答已保存。商品现在可以继续运行。", "success");
    await pollNotifications({showDesktop:false});
    state.inboxFilter = "需处理";
    await loadProducts(searchInput.value, {force:true});
    return navigate("inbox", {force:true});
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
  await pollSystemStatus({notify:false});
  clearInterval(state.systemStatusTimer);
  state.systemStatusTimer = setInterval(() => pollSystemStatus(), 10000);
  await pollNotifications({showDesktop:false});
  clearInterval(state.notificationTimer);
  state.notificationTimer = setInterval(() => pollNotifications(), 15000);
  await navigate("inbox");
}

bootstrapWorkbench();
