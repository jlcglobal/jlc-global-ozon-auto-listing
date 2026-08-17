const SEERFAR_POLL_INTERVAL_MS = 5000;
let seerfarBusy = false;
function seerfarText(node) {
    return String(node?.textContent || "").replace(/\s+/g, " ").trim();
}
function seerfarNumber(value) {
    const text = String(value || "").replace(/[^0-9,.-]/g, "").replace(/,/g, ".");
    const number = Number(text);
    return Number.isFinite(number) ? number : 0;
}
function isUsableMarketKeyword(value) {
    const keyword = String(value || "").replace(/\s+/g, " ").trim();
    // Ozon search terms may contain Latin product codes, but a Chinese fragment
    // means this is an untranslated or stale Seerfar row, never an upload source.
    return Boolean(keyword) && !/[\u3400-\u9fff\uf900-\ufaff]/.test(keyword);
}
async function factoryRequest(path, options = {}) {
    return new Promise((resolve, reject) => {
        chrome.runtime.sendMessage({ type: "FACTORY_FETCH", path, options }, (result) => {
            const runtimeError = chrome.runtime.lastError;
            if (runtimeError)
                return reject(new Error(runtimeError.message));
            if (!result?.ok)
                return reject(new Error(result?.error || "工作台连接失败"));
            let body = {};
            try {
                body = result.body ? JSON.parse(result.body) : {};
            }
            catch {
                body = {};
            }
            if (result.status < 200 || result.status >= 300) {
                return reject(new Error(body.detail?.message || body.detail || `HTTP ${result.status}`));
            }
            resolve(body);
        });
    });
}
function assignNativeValue(input, value) {
    const prototype = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
    setter?.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
}
function findSearchInput(mode) {
    if (mode === "keyword_miner") {
        return document.querySelector("#magnet-keyword");
    }
    if (mode === "keyword_reverse") {
        const reverseInput = document.querySelector("#reverse-keyword + .select2 input.select2-search__field");
        if (reverseInput)
            return reverseInput;
    }
    const candidates = Array.from(document.querySelectorAll("textarea, input[type='text'], input:not([type])"));
    if (mode === "keyword_reverse") {
        // Seerfar also has a global header search with "SKU" in its placeholder.
        // The reverse form is the only field that explicitly accepts multiple SKUs.
        return candidates.find((node) => /输入多个\s*SKU/i.test(`${node.placeholder} ${node.getAttribute("aria-label") || ""}`))
            || candidates.find((node) => /英文逗号分隔/i.test(`${node.placeholder} ${node.getAttribute("aria-label") || ""}`))
            || candidates.find((node) => node.offsetParent !== null && !node.disabled)
            || null;
    }
    return null;
}
function findSearchButton(mode, input) {
    if (mode === "keyword_miner") {
        return document.querySelector("#tab-keyword-magnet button.quick-search");
    }
    const reverseSelect = document.querySelector("#reverse-keyword");
    return reverseSelect?.closest(".row")?.querySelector("button.quick-search")
        || input?.closest(".row")?.querySelector("button.quick-search")
        || null;
}
function setReverseSku(value) {
    const select = document.querySelector("#reverse-keyword");
    if (!select)
        return false;
    const option = new Option(value, value, true, true);
    select.replaceChildren(option);
    select.dispatchEvent(new Event("change", { bubbles: true }));
    const jquery = window.jQuery || window.$;
    if (jquery)
        jquery(select).trigger("change");
    return select.value === value;
}
function seerfarLoginRequired() {
    const pageText = `${document.title} ${seerfarText(document.body)}`.toLowerCase();
    return location.pathname.includes("login")
        || /请先登录|登录后|账号登录|扫码登录|sign in|log in/.test(pageText);
}
function tableRows() {
    const tables = Array.from(document.querySelectorAll("table"));
    for (const table of tables) {
        const allRows = Array.from(table.querySelectorAll("tr"));
        const header = allRows.find((row) => /月搜热度/.test(seerfarText(row)) && /关键词/.test(seerfarText(row)));
        if (!header)
            continue;
        const headers = Array.from(header.querySelectorAll("th, td")).map((cell) => seerfarText(cell));
        const rows = allRows
            .slice(allRows.indexOf(header) + 1)
            .map((row) => {
            const cells = Array.from(row.querySelectorAll("td"));
            return { cells, values: cells.map((cell) => seerfarText(cell)) };
        })
            .filter((row) => row.values.length >= 2 && row.values.some(Boolean));
        if (rows.length)
            return { headers, rows };
    }
    return null;
}
function parseKeywordRows() {
    const table = tableRows();
    if (!table)
        return [];
    const indexOf = (label) => table.headers.findIndex((header) => header.includes(label));
    const queryIndex = indexOf("关键词");
    const relatedProductIndex = indexOf("关键词相关商品");
    const heatIndex = indexOf("月搜热度");
    if (queryIndex < 0 || heatIndex < 0)
        return [];
    const fields = [
        ["monthly_growth_percent", "月搜增长"], ["relevance", "相关度"], ["cart_add_count", "加购数"],
        ["cart_conversion_percent", "加购转化率"], ["title_density_percent", "标题密度"], ["average_price_rub", "平均价格"],
        ["competitor_count", "竞品数"], ["product_count", "商品数"], ["competitor_seller_count", "竞对数"],
        ["ad_competitor_count", "广告竞品数"], ["product_visibility", "商品可见度"], ["market_space", "市场空间"],
        ["conversion_concentration_percent", "转化集中度"], ["return_cancel_rate_percent", "退货取消率"],
    ];
    const indexes = new Map(fields.map(([key, label]) => [key, indexOf(label)]));
    return table.rows.map(({ cells, values }) => {
        const row = {
            query: values[queryIndex],
            monthly_search_heat: seerfarNumber(values[heatIndex]),
        };
        for (const [key] of fields) {
            const index = indexes.get(key) ?? -1;
            if (index >= 0 && values[index])
                row[key] = seerfarNumber(values[index]);
        }
        if (relatedProductIndex >= 0 && cells[relatedProductIndex]) {
            row.related_product_urls = Array.from(cells[relatedProductIndex].querySelectorAll("a[href*='/product/']"))
                .map((link) => link.href)
                .filter((url, index, values) => /^https:\/\/(?:www\.)?ozon\.ru\/product\/\d+/.test(url) && values.indexOf(url) === index)
                .slice(0, 10);
        }
        return row;
    }).filter((row) => isUsableMarketKeyword(row.query) && Number(row.monthly_search_heat || 0) > 0);
}
function parseReverseRows() {
    const tables = Array.from(document.querySelectorAll("table"));
    for (const table of tables) {
        const allRows = Array.from(table.querySelectorAll("tr"));
        const header = allRows.find((row) => /搜索查询|关键词/.test(seerfarText(row)) && /一直在找|搜索量|搜索人数/.test(seerfarText(row)));
        if (!header)
            continue;
        const headers = Array.from(header.querySelectorAll("th, td")).map((cell) => seerfarText(cell));
        const queryIndex = headers.findIndex((value) => /搜索查询|关键词/.test(value));
        const countIndex = headers.findIndex((value) => /一直在找|搜索量|搜索人数/.test(value));
        if (queryIndex < 0 || countIndex < 0)
            continue;
        const rows = allRows.slice(allRows.indexOf(header) + 1).map((row) => Array.from(row.querySelectorAll("td")).map((cell) => seerfarText(cell)))
            .map((cells) => ({ query: cells[queryIndex], search_count: seerfarNumber(cells[countIndex]), source_mode: "keyword_reverse" }))
            .filter((row) => isUsableMarketKeyword(row.query) && row.search_count > 0);
        if (rows.length)
            return rows;
    }
    return [];
}
function keywordRowsSignature(mode) {
    const rows = mode === "keyword_reverse" ? parseReverseRows() : parseKeywordRows();
    return rows.slice(0, 10)
        .map((row) => `${String(row.query || "").trim()}:${row.monthly_search_heat || row.search_count || 0}`)
        .join("|");
}
async function waitForInitialResultsToSettle(mode, timeoutMs = 6000) {
    const startedAt = Date.now();
    let lastSignature = keywordRowsSignature(mode);
    let stableCount = 0;
    while (Date.now() - startedAt < timeoutMs) {
        await new Promise((resolve) => window.setTimeout(resolve, 600));
        const signature = keywordRowsSignature(mode);
        if (signature === lastSignature)
            stableCount += 1;
        else
            stableCount = 0;
        lastSignature = signature;
        if (stableCount >= 2)
            break;
    }
    return lastSignature;
}
function waitForKeywordRows(mode, previousSignature, timeoutMs = 20000) {
    return new Promise((resolve) => {
        const startedAt = Date.now();
        let stableCount = 0;
        let lastSignature = previousSignature;
        const timer = window.setInterval(() => {
            const rows = mode === "keyword_reverse" ? parseReverseRows() : parseKeywordRows();
            const signature = rows.slice(0, 10)
                .map((row) => `${String(row.query || "").trim()}:${row.monthly_search_heat || row.search_count || 0}`)
                .join("|");
            const resultChanged = Boolean(signature && signature !== previousSignature);
            if (rows.length && resultChanged && signature === lastSignature)
                stableCount += 1;
            else
                stableCount = 0;
            lastSignature = signature || lastSignature;
            if (rows.length && resultChanged && stableCount >= 1) {
                window.clearInterval(timer);
                resolve(rows);
            }
            else if (Date.now() - startedAt >= timeoutMs) {
                window.clearInterval(timer);
                resolve([]);
            }
        }, 1200);
    });
}
async function runSeerfarJob(job) {
    const mode = String(job.mode || "keyword_miner");
    const expectedPath = mode === "keyword_reverse" ? "keyword-reverse" : "keyword-miner";
    if (seerfarLoginRequired()) {
        throw new Error("SEERFAR_LOGIN_REQUIRED: Seerfar 登录已失效，请在 Chrome 中重新登录");
    }
    if (!location.pathname.includes(expectedPath)) {
        location.assign(`https://seerfar.cn/admin/${expectedPath}.html`);
        return;
    }
    const input = findSearchInput(mode);
    const button = findSearchButton(mode, input);
    if (!input || !button)
        throw new Error(`没有找到 Seerfar ${mode === "keyword_reverse" ? "关键词反查" : "关键词挖掘"}的输入框或查询按钮`);
    // Seerfar can hydrate the previous query after a route change.  Establish
    // that settled baseline first, then only accept a later, different table.
    const previousSignature = await waitForInitialResultsToSettle(mode);
    const seedKeyword = String(job.seed_keyword || "");
    if (mode === "keyword_reverse") {
        if (!setReverseSku(seedKeyword))
            throw new Error("没有写入 Seerfar 反查 SKU");
    }
    else {
        assignNativeValue(input, seedKeyword);
        if (input.value !== seedKeyword)
            throw new Error("没有写入 Seerfar 挖掘关键词");
    }
    button.click();
    const rows = await waitForKeywordRows(mode, previousSignature);
    if (!rows.length) {
        if (mode === "keyword_reverse") {
            throw new Error("SEERFAR_REVERSE_EMPTY: 该 Ozon SKU 在 Seerfar 没有可用的反查词");
        }
        throw new Error("Seerfar 页面没有返回关键词挖掘结果");
    }
    const importPath = String(job.import_path || "/api/workbench/market-intelligence/search-visibility/seerfar/import");
    await factoryRequest(importPath, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            job_id: job.job_id,
            product_id: job.product_id,
            store_id: job.shop_id,
            seed_keyword: job.seed_keyword,
            mode,
            rows,
        }),
    });
}
async function pollSeerfarJob() {
    if (seerfarBusy)
        return;
    seerfarBusy = true;
    try {
        const sessionState = seerfarLoginRequired() ? "login_required" : "logged_in";
        const result = await factoryRequest(`/api/workbench/market-intelligence/search-visibility/seerfar/next?session_state=${sessionState}`);
        const job = result?.job;
        if (!job)
            return;
        try {
            await runSeerfarJob(job);
        }
        catch (error) {
            await factoryRequest("/api/workbench/market-intelligence/search-visibility/seerfar/fail", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ job_id: job.job_id, error: String(error?.message || error || "Seerfar 读取失败") }),
            });
        }
    }
    catch {
        // The workbench may be offline temporarily.  Polling retries without touching Seerfar.
    }
    finally {
        seerfarBusy = false;
    }
}
void pollSeerfarJob();
window.setInterval(() => void pollSeerfarJob(), SEERFAR_POLL_INTERVAL_MS);
