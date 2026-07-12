const grid = document.getElementById("product-grid");
const runButton = document.getElementById("run-task");
const refreshOzonButton = document.getElementById("refresh-ozon-status");
const viewResultButton = document.getElementById("view-result");
const resultDialog = document.getElementById("result-dialog");
const resultContent = document.getElementById("result-content");
let latestResult = null;

const text = (value) => value === null || value === undefined || value === "" ? "unknown" : String(value);
const escapeHtml = (value) => text(value).replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));

async function apiFetch(url, options = {}, allowAccessRetry = true) {
  const accessCode = localStorage.getItem("cafAccessCode") || "";
  const headers = { ...(options.headers || {}) };
  if (accessCode) headers["X-Factory-Access-Code"] = accessCode;
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401 && allowAccessRetry) {
    const data = await response.clone().json().catch(() => ({}));
    if (data.detail?.code === "ACCESS_CODE_REQUIRED") {
      const entered = window.prompt("请输入工作室访问码");
      if (entered?.trim()) {
        localStorage.setItem("cafAccessCode", entered.trim());
        return apiFetch(url, options, false);
      }
    }
  }
  return response;
}

function card(product) {
  const messages = [...product.errors, ...product.warnings].slice(0, 3);
  const statusClass = product.status === "FAILED_HARD_BLOCKER" ? "failed" : ["UPLOADED", "OZON_MODERATION", "ACTIVE"].includes(product.status) ? "success" : "";
  const statusText = product.status === "PENDING_REMOTE" ? "已提交Ozon，平台处理中（图片通道后台保持）" : product.status === "COLLECTED" && !product.in_current_inbox ? "COLLECTED · 已有新版本" : product.status;
  return `<article class="product-card" data-product-id="${product.product_id}">
    <img src="${product.thumbnail_url}" alt="${escapeHtml(product.title_cn)}" onerror="this.style.visibility='hidden'">
    <div><h2>${escapeHtml(product.title_cn)}</h2><span class="status ${statusClass}">${escapeHtml(statusText)}</span>
      <dl class="meta"><dt>商品编号</dt><dd>${product.product_id}</dd><dt>SKU</dt><dd>${product.selected_sku_count} / 10</dd><dt>采集时间</dt><dd>${escapeHtml(product.captured_at)}</dd><dt>当前步骤</dt><dd>${escapeHtml(product.current_step)}</dd><dt>来源</dt><dd><a href="${escapeHtml(product.source_url)}" target="_blank" rel="noreferrer">打开1688商品</a></dd></dl>
    </div>
    <div class="messages">${messages.length ? messages.map(escapeHtml).join("<br>") : "无错误或警告"}</div>
    <div class="actions"><button data-action="open">打开商品目录</button><a href="${escapeHtml(product.source_url)}" target="_blank" rel="noreferrer">重新采集</a><button class="danger" data-action="delete">删除商品</button></div>
  </article>`;
}

async function loadInbox() {
  const response = await apiFetch("/api/inbox/products");
  const data = await response.json();
  document.getElementById("pending-products").textContent = data.pending_product_count;
  document.getElementById("pending-skus").textContent = data.pending_sku_count;
  grid.innerHTML = data.products.map(card).join("") || "<p>采集箱为空</p>";
}

async function loadBatch() {
  const response = await apiFetch("/api/tasks/status");
  const data = await response.json();
  const batch = data.current_batch;
  latestResult = data.last_result;
  runButton.disabled = data.running;
  if (batch) {
    document.getElementById("batch-name").textContent = batch.batch_id;
    document.getElementById("batch-status").textContent = batch.status;
    document.getElementById("processing").textContent = batch.processing_count;
    document.getElementById("success").textContent = batch.success_count;
    document.getElementById("failed").textContent = batch.failed_count;
    document.getElementById("progress").textContent = `${batch.progress}%`;
  }
  viewResultButton.disabled = !latestResult;
}

runButton.addEventListener("click", async () => {
  runButton.disabled = true;
  const response = await apiFetch("/api/tasks/run", {method: "POST"});
  const result = await response.json();
  if (!response.ok) alert(text(result.detail));
  await loadInbox();
  await loadBatch();
});

refreshOzonButton.addEventListener("click", async () => {
  refreshOzonButton.disabled = true;
  await apiFetch("/api/inbox/refresh-ozon-status", {method: "POST"});
  await Promise.all([loadInbox(), loadBatch()]);
  refreshOzonButton.disabled = false;
});

grid.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const productId = button.closest(".product-card").dataset.productId;
  if (button.dataset.action === "open") {
    await apiFetch(`/api/inbox/products/${productId}/open-directory`, {method: "POST"});
  } else if (button.dataset.action === "delete" && confirm(`确认彻底删除 ${productId} 的全部本地资料？删除后无法恢复；已经提交到Ozon的远端商品不会被删除。`)) {
    const response = await apiFetch(`/api/inbox/products/${productId}`, {
      method: "DELETE", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({confirm_product_id: productId}),
    });
    if (!response.ok) alert(text((await response.json()).detail));
    await loadInbox();
  }
});

viewResultButton.addEventListener("click", () => {
  resultContent.textContent = JSON.stringify(latestResult, null, 2);
  resultDialog.showModal();
});
document.getElementById("close-result").addEventListener("click", () => resultDialog.close());

Promise.all([loadInbox(), loadBatch()]);
setInterval(() => Promise.all([loadInbox(), loadBatch()]), 3000);
