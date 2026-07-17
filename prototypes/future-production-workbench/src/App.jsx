import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowClockwise, ArrowLeft, ArrowRight, Bell, Check, CheckCircle, ClipboardText,
  CloudCheck, Copy, DownloadSimple, DotsThree, Eye, FileCsv, FloppyDisk, GearSix,
  ImageSquare, ListChecks, LockKey, MagnifyingGlass, Package, PencilSimple, Robot,
  ShieldCheck, ShoppingBagOpen, SlidersHorizontal, Sparkle, SquaresFour, Storefront,
  Tag, Trash, TrendUp, UploadSimple, UsersThree, Wallet, WarningCircle, X,
} from "@phosphor-icons/react";

const navItems = [
  { id: "inbox", label: "我的采集箱", icon: Package },
  { id: "market", label: "选品与关键词", icon: TrendUp },
  { id: "attention", label: "需要我处理", icon: WarningCircle, count: 0 },
  { id: "listed", label: "已上架商品", icon: ShoppingBagOpen, count: 1 },
  { id: "finance", label: "财务中心", icon: Wallet },
  { id: "shops", label: "店铺设置", icon: Storefront },
  { id: "settings", label: "系统设置", icon: GearSix },
];

const stages = [
  { id: "source", index: "01", title: "采集与确认", meta: "1688资料 · 2 SKU" },
  { id: "content", index: "02", title: "商品资料", meta: "分析、俄文、属性" },
  { id: "pricing", index: "03", title: "定价计算", meta: "2 个独立售价" },
  { id: "images", index: "04", title: "图片制作", meta: "2 主图 · 8 详情" },
  { id: "review", index: "05", title: "质检与快照", meta: "真实性检查通过" },
  { id: "upload", index: "06", title: "Ozon 上架", meta: "zhonglian1 · 已完成" },
];

const assets = [
  { id: "variant-350", src: "/assets/variant-350.png", label: "350ml 主图", role: "SKU 主图", version: "v1" },
  { id: "variant-400", src: "/assets/variant-400.png", label: "400ml 主图", role: "SKU 主图", version: "v1" },
  { id: "detail-01", src: "/assets/detail-01.png", label: "便携场景", role: "详情图 01", version: "v1" },
  { id: "detail-02", src: "/assets/detail-02.png", label: "结构展示", role: "详情图 02", version: "v1" },
  { id: "detail-03", src: "/assets/detail-03.png", label: "容量说明", role: "详情图 03", version: "v1" },
];

const inspectorTabs = ["资料", "图片", "SKU", "价格", "类目", "店铺", "风险"];

const capabilityGroups = [
  { title: "商品任务", items: ["采集箱与任务通知", "批次唯一一次确认", "手动 / 自动模式", "失败步骤单独重试", "安全停止与断点继续"] },
  { title: "审核与内容", items: ["长页预览与上下商品", "快速 / 完整审核", "草稿自动保存", "人工字段锁与 AI 建议", "最终提交快照"] },
  { title: "图片制作", items: ["逐图状态", "查看与提示词", "单图重做与替换", "下载 / 复制 URL", "排序、角色与版本"] },
  { title: "商品数据", items: ["SKU 差异摘要", "类目属性检查", "SKU 独立定价", "价值评分与定价建议", "unknown 与真实性风险"] },
  { title: "多店与安全", items: ["一份主档多店发布", "店铺独立状态", "只重试失败店铺", "pending 防重复提交", "不提交库存"] },
  { title: "管理能力", items: ["全字段搜索", "商品 / 批次 / AI 日志", "导出与备份", "成员与负责人权限", "Skill 与风险中心"] },
];

function IconButton({ label, children, className = "", onClick, pressed }) {
  return <button type="button" className={`icon-button ${className}`} aria-label={label} aria-pressed={pressed} title={label} onClick={onClick}>{children}</button>;
}

function FieldRow({ label, value, meta, locked = false }) {
  return (
    <div className="field-row">
      <div className="field-copy"><span>{label}</span><strong>{value}</strong>{meta ? <small>{meta}</small> : null}</div>
      <IconButton label={locked ? `${label}已锁定` : `编辑${label}`} className="field-action">{locked ? <LockKey size={15} /> : <PencilSimple size={15} />}</IconButton>
    </div>
  );
}

function InspectorContent({ tab, selectedAsset, setSelectedAsset, toast }) {
  if (tab === "资料") return (
    <div className="inspector-section">
      <div className="section-heading"><div><span className="eyebrow">Product copy</span><h3>商品资料</h3></div><span className="save-state"><CloudCheck size={14} /> 已保存</span></div>
      <FieldRow label="中文来源标题" value="ceool总裁小姐 新款多功能榨汁机便携式充电榨汁杯保温电动果汁杯" locked />
      <FieldRow label="俄文标题" value="Миксер кухонный портативный электрический, стакан 350/400 мл, черный" />
      <FieldRow label="品牌" value="Нет бренда" meta="按项目规则保存为无品牌" locked />
      <div className="compact-note"><Sparkle size={17} weight="fill" /><div><strong>AI 建议已检查</strong><span>本商品没有待处理建议；人工锁定字段不会被覆盖。</span></div></div>
    </div>
  );
  if (tab === "图片") return (
    <div className="inspector-section">
      <div className="section-heading"><div><span className="eyebrow">Image package</span><h3>图片包</h3></div><span className="soft-badge">10 张已完成</span></div>
      <div className="asset-list">{assets.map((asset, index) => (
        <button type="button" className={`asset-row ${selectedAsset === index ? "active" : ""}`} onClick={() => setSelectedAsset(index)} key={asset.id}>
          <img src={asset.src} alt="" /><span><strong>{asset.label}</strong><small>{asset.role} · {asset.version}</small></span><CheckCircle size={18} weight="fill" />
        </button>
      ))}</div>
      <button className="text-button full-width" type="button" onClick={() => toast("已展开图片角色、顺序与版本记录")}>查看全部图片与版本 <ArrowRight size={15} /></button>
    </div>
  );
  if (tab === "SKU") return (
    <div className="inspector-section">
      <div className="section-heading"><div><span className="eyebrow">Variants</span><h3>2 个 SKU</h3></div><span className="soft-badge">差异清晰</span></div>
      <article className="sku-card"><div className="sku-top"><strong>350ml · 黑色</strong><span>5931524204563</span></div><p>双层不锈钢 316S</p><div className="sku-facts"><span>采购价 ¥81</span><span>售价 ¥285</span><span>约 ₽3,420</span></div></article>
      <article className="sku-card"><div className="sku-top"><strong>400ml · 双拼黑</strong><span>5931524204569</span></div><p>双层不锈钢 304S · 配盖</p><div className="sku-facts"><span>采购价 ¥82</span><span>售价 ¥287</span><span>约 ₽3,444</span></div></article>
      <div className="compact-note neutral"><ListChecks size={17} /><div><strong>变体依据已保留</strong><span>材质与配件描述只作用于对应 SKU。</span></div></div>
    </div>
  );
  if (tab === "价格") return (
    <div className="inspector-section">
      <div className="section-heading"><div><span className="eyebrow">Pricing</span><h3>独立定价</h3></div><span className="soft-badge">人民币编辑</span></div>
      <div className="price-focus"><span>建议售价区间</span><strong>¥285–287</strong><small>约 ₽3,420–3,444 · 汇率 12.0</small></div>
      <FieldRow label="350ml 售价" value="¥285" meta="预期利润 ¥73.38" /><FieldRow label="400ml 售价" value="¥287" meta="预期利润 ¥73.92" />
      <div className="compact-note neutral"><ShieldCheck size={17} /><div><strong>运费只使用包装数据</strong><span>31.5 × 21 × 16 cm · 1,150 g · 人工已确认</span></div></div>
    </div>
  );
  if (tab === "类目") return (
    <div className="inspector-section">
      <div className="section-heading"><div><span className="eyebrow">Category</span><h3>最终 Ozon 类目</h3></div><span className="save-state"><Check size={14} /> 已锁定</span></div>
      <div className="category-card"><Tag size={20} /><div><strong>厨房搅拌机</strong><span>Миксер кухонный</span></div></div>
      <FieldRow label="类目属性" value="必填项已完成" meta="类目规则快照已保存" locked /><FieldRow label="产地" value="中国" locked /><FieldRow label="包装数量" value="1 件" locked />
      <div className="compact-note neutral"><LockKey size={17} /><div><strong>运行任务不会重新猜类目</strong><span>修改类目会先使旧结果失效，再重新生成。</span></div></div>
    </div>
  );
  if (tab === "店铺") return (
    <div className="inspector-section">
      <div className="section-heading"><div><span className="eyebrow">Publication</span><h3>店铺状态</h3></div><span className="success-badge">已上架</span></div>
      <div className="shop-card"><div className="shop-mark"><Storefront size={20} /></div><div><strong>zhonglian1</strong><span>凭证已配置 · 独立发布记录</span></div><CheckCircle size={19} weight="fill" /></div>
      <FieldRow label="Ozon product_id" value="5482520711" locked /><FieldRow label="task_id" value="5081018919" locked /><FieldRow label="offer_id" value="P000001-5931524204563" locked />
      <div className="compact-note neutral"><ShieldCheck size={17} /><div><strong>重复提交保护正常</strong><span>库存字段未提交；当前预览不会调用任何接口。</span></div></div>
    </div>
  );
  return (
    <div className="inspector-section">
      <div className="section-heading"><div><span className="eyebrow">Truth guard</span><h3>真实性风险</h3></div><span className="soft-badge">3 项已隔离</span></div>
      <div className="risk-card warning"><WarningCircle size={19} /><div><strong>认证信息 unknown</strong><span>原始资料没有可靠依据，不写入卖点。</span></div></div>
      <div className="risk-card"><ShieldCheck size={19} /><div><strong>材质按 SKU 分开</strong><span>316S 与 304S 不互相套用。</span></div></div>
      <div className="risk-card"><ShieldCheck size={19} /><div><strong>包装大于商品</strong><span>包装尺寸和重量规则检查通过。</span></div></div>
      <div className="rule-grid"><span>禁止虚构承重</span><span>禁止虚构配件</span><span>禁止提交库存</span><span>失败可追溯</span></div>
    </div>
  );
}

export function App() {
  const [activeNav, setActiveNav] = useState("inbox");
  const [selectedStage, setSelectedStage] = useState("upload");
  const [activeTab, setActiveTab] = useState("资料");
  const [selectedAsset, setSelectedAsset] = useState(0);
  const [autoMode, setAutoMode] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [modal, setModal] = useState(null);
  const [toastMessage, setToastMessage] = useState("");
  const toastTimer = useRef(null);
  const currentAsset = assets[selectedAsset];
  const filteredCapabilities = useMemo(() => {
    if (!search.trim()) return capabilityGroups;
    const query = search.trim().toLowerCase();
    return capabilityGroups.map((group) => ({ ...group, items: group.items.filter((item) => item.toLowerCase().includes(query)) })).filter((group) => group.items.length);
  }, [search]);
  const toast = (message) => { window.clearTimeout(toastTimer.current); setToastMessage(message); toastTimer.current = window.setTimeout(() => setToastMessage(""), 2800); };

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === "Escape") { setModal(null); setDrawerOpen(false); setSearchOpen(false); setMoreOpen(false); }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); setSearchOpen(true); }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const navigate = (id, label) => { setActiveNav(id); if (id !== "inbox") toast(`${label}是 Factory 现有模块；本次预览聚焦商品制作工作台`); };

  return (
    <div className="page-frame">
      <div className="prototype-banner"><ShieldCheck size={16} /> 独立 UI 预览 · 真实商品快照 · 不连接 Factory / Ozon</div>
      <main className="app-shell">
        <aside className="nav-rail" aria-label="Factory 主导航">
          <button className="brand" type="button" aria-label="跨境 AI 工厂" onClick={() => toast("跨境 AI 工厂 · 商品生产审核台")}>CF</button>
          <nav>{navItems.map((item) => { const Icon = item.icon; return (
            <button key={item.id} type="button" className={`rail-link ${activeNav === item.id ? "active" : ""}`} aria-label={item.label} data-tooltip={item.label} onClick={() => navigate(item.id, item.label)}>
              <Icon size={22} weight={activeNav === item.id ? "fill" : "regular"} />{item.count ? <span className="rail-count">{item.count}</span> : null}
            </button>
          ); })}</nav>
          <div className="rail-bottom"><span className="online-dot" /><UsersThree size={21} /><small>负责人</small></div>
        </aside>

        <section className="workspace">
          <header className="topbar">
            <div className="title-block"><span className="eyebrow">Product production / P000001</span><h1>商品制作工作台</h1></div>
            <div className="top-actions">
              <button type="button" className="search-trigger" onClick={() => setSearchOpen(true)}><MagnifyingGlass size={18} /><span>搜索商品、SKU、任务号</span><kbd>⌘ K</kbd></button>
              <button type="button" className={`mode-toggle ${autoMode ? "on" : ""}`} aria-pressed={autoMode} onClick={() => { setAutoMode(!autoMode); toast(`已切换为${!autoMode ? "自动审核" : "手动检查"}预览状态`); }}><span><Robot size={17} />{autoMode ? "自动审核" : "手动检查"}</span><i /></button>
              <IconButton label="通知" onClick={() => toast("当前没有需要处理的新通知")}><Bell size={20} /></IconButton>
              <button type="button" className="capability-button" onClick={() => setDrawerOpen(true)}><SquaresFour size={18} />功能总览</button>
            </div>
          </header>

          <div className="work-grid">
            <aside className="stage-panel">
              <div className="stage-head"><span>Production flow</span><strong>6 / 6</strong></div><div className="stage-progress" aria-label="商品制作进度 100%"><span /></div>
              <div className="product-mini"><img src="/assets/source-main.webp" alt="P000001 1688 原始主图" /><div><strong>P000001</strong><span>2 SKU · 已上架</span></div><CheckCircle size={18} weight="fill" /></div>
              <nav className="stage-list" aria-label="商品制作步骤">{stages.map((stage, index) => (
                <button type="button" className={selectedStage === stage.id ? "active" : ""} key={stage.id} onClick={() => setSelectedStage(stage.id)}>
                  <span className="stage-index">{stage.index}</span><span className="stage-copy"><strong>{stage.title}</strong><small>{stage.meta}</small></span><Check size={15} weight="bold" />{index < stages.length - 1 ? <i /> : null}
                </button>
              ))}</nav>
              <button type="button" className="timeline-button" onClick={() => toast("已打开商品、批次、AI、修改与上传时间线")}><ClipboardText size={17} />查看完整时间线<ArrowRight size={15} /></button>
            </aside>

            <section className="product-stage" aria-label="商品图片工作区">
              <div className="stage-toolbar">
                <div className="status-cluster"><span className="success-dot" /><strong>制作完成</strong><span>100%</span></div>
                <div className="tool-cluster"><IconButton label="上一张图片" onClick={() => setSelectedAsset((selectedAsset - 1 + assets.length) % assets.length)}><ArrowLeft size={18} /></IconButton><span>{String(selectedAsset + 1).padStart(2, "0")} / {String(assets.length).padStart(2, "0")}</span><IconButton label="下一张图片" onClick={() => setSelectedAsset((selectedAsset + 1) % assets.length)}><ArrowRight size={18} /></IconButton><IconButton label="更多图片操作" onClick={() => setMoreOpen(!moreOpen)} pressed={moreOpen}><DotsThree size={20} weight="bold" /></IconButton></div>
                {moreOpen ? <div className="more-menu"><button type="button" onClick={() => { setMoreOpen(false); toast("已复制当前图片 URL（预览）"); }}><Copy size={17} />复制图片 URL</button><a href={currentAsset.src} download><DownloadSimple size={17} />下载当前图片</a><button type="button" onClick={() => { setMoreOpen(false); toast("图片包导出已进入预览队列"); }}><FileCsv size={17} />导出图片包</button></div> : null}
              </div>
              <div className="visual-canvas"><div className="visual-meta"><span>{currentAsset.role}</span><strong>{currentAsset.label}</strong></div><img key={currentAsset.id} className="hero-product" src={currentAsset.src} alt={`${currentAsset.label} 商品图`} /><button type="button" className="zoom-button" onClick={() => setModal("image")}><Eye size={17} />查看大图</button></div>
              <div className="image-filmstrip" aria-label="图片缩略图">{assets.map((asset, index) => <button type="button" key={asset.id} className={selectedAsset === index ? "active" : ""} onClick={() => setSelectedAsset(index)} aria-label={`查看${asset.label}`}><img src={asset.src} alt="" /><span>{index < 2 ? "主" : String(index - 1).padStart(2, "0")}</span></button>)}<button type="button" className="show-all-images" onClick={() => setActiveTab("图片")}><ImageSquare size={18} /><span>全部 10 张</span></button></div>
              <div className="image-command-bar"><button type="button" onClick={() => toast("已打开当前图片提示词与禁止改变项")}><Sparkle size={17} />提示词</button><button type="button" onClick={() => toast("单图重做只会创建新版本；本预览未执行生成")}><ArrowClockwise size={17} />单图重做</button><button type="button" onClick={() => toast("替换入口已打开；本预览不写入真实商品")}><UploadSimple size={17} />替换</button><button type="button" onClick={() => toast("拖拽排序与角色设置在完整图片面板中")}><SlidersHorizontal size={17} />角色与顺序</button></div>
            </section>

            <aside className="inspector-panel"><div className="inspector-tabs" role="tablist" aria-label="商品数据面板">{inspectorTabs.map((tab) => <button type="button" role="tab" aria-selected={activeTab === tab} className={activeTab === tab ? "active" : ""} onClick={() => setActiveTab(tab)} key={tab}>{tab}</button>)}</div><div className="inspector-scroll"><InspectorContent tab={activeTab} selectedAsset={selectedAsset} setSelectedAsset={setSelectedAsset} toast={toast} /></div></aside>
          </div>

          <footer className="command-dock">
            <div className="dock-status"><span className="dock-check"><Check size={17} weight="bold" /></span><div><strong>全部步骤完成</strong><span>已于 07/14 上架到 zhonglian1 · 库存未提交</span></div></div>
            <div className="dock-summary"><span>商品资料 <b>已保存</b></span><span>图片包 <b>10 / 10</b></span><span>真实性检查 <b>通过</b></span></div>
            <div className="dock-actions"><button type="button" className="ghost-action" onClick={() => toast("当前商品草稿与最终快照都已保存")}><FloppyDisk size={18} />保存状态</button><button type="button" className="primary-action" onClick={() => setModal("publication")}><Storefront size={18} />查看上架结果</button><IconButton label="商品更多操作" className="dark-icon" onClick={() => setModal("actions")}><DotsThree size={21} weight="bold" /></IconButton></div>
          </footer>
        </section>
      </main>

      {searchOpen ? <div className="overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSearchOpen(false); }}><section className="search-panel" role="dialog" aria-modal="true" aria-label="全局搜索"><div className="search-input"><MagnifyingGlass size={21} /><input autoFocus value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索商品、SKU、任务、店铺或现有功能" /><kbd>ESC</kbd></div><div className="search-result-head"><span>{search ? `“${search}”的匹配结果` : "快速访问"}</span><small>全字段搜索</small></div><button type="button" className="search-product" onClick={() => { setSearchOpen(false); setSearch(""); }}><img src="/assets/source-main.webp" alt="" /><span><strong>P000001 · 便携式榨汁杯</strong><small>2 SKU · zhonglian1 · 已上架</small></span><ArrowRight size={18} /></button>{filteredCapabilities.slice(0, 2).map((group) => <div className="search-group" key={group.title}><strong>{group.title}</strong>{group.items.slice(0, 3).map((item) => <button type="button" onClick={() => { toast(`${item}是当前 Factory 已有能力`); setSearchOpen(false); }} key={item}>{item}<ArrowRight size={14} /></button>)}</div>)}{search && filteredCapabilities.length === 0 ? <div className="empty-result">没有匹配的现有功能</div> : null}</section></div> : null}

      {drawerOpen ? <div className="overlay drawer-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setDrawerOpen(false); }}><aside className="capability-drawer" role="dialog" aria-modal="true" aria-label="Factory 现有功能总览"><div className="drawer-head"><div><span className="eyebrow">Existing capabilities only</span><h2>Factory 现有功能总览</h2><p>这里只重新组织当前工作台已存在的能力，没有新增业务功能。</p></div><IconButton label="关闭功能总览" onClick={() => setDrawerOpen(false)}><X size={21} /></IconButton></div><div className="capability-grid">{capabilityGroups.map((group, index) => <article key={group.title}><span>{String(index + 1).padStart(2, "0")}</span><h3>{group.title}</h3>{group.items.map((item) => <p key={item}><Check size={14} weight="bold" />{item}</p>)}</article>)}</div><div className="drawer-foot"><ShieldCheck size={18} /><span>原型不连接后端，不创建商品，不运行批次，不调用 Ozon。</span><button type="button" onClick={() => setDrawerOpen(false)}>返回工作台</button></div></aside></div> : null}

      {modal === "image" ? <div className="overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setModal(null); }}><section className="image-modal" role="dialog" aria-modal="true" aria-label={`${currentAsset.label}大图`}><div className="modal-bar"><div><span>{currentAsset.role}</span><strong>{currentAsset.label} · {currentAsset.version}</strong></div><IconButton label="关闭大图" onClick={() => setModal(null)}><X size={21} /></IconButton></div><img src={currentAsset.src} alt={`${currentAsset.label}大图`} /></section></div> : null}

      {modal === "publication" ? <div className="overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setModal(null); }}><section className="result-modal" role="dialog" aria-modal="true" aria-label="Ozon 上架结果"><div className="result-icon"><CheckCircle size={34} weight="fill" /></div><span className="eyebrow">Publication completed</span><h2>商品已成功上架</h2><p>这是 P000001 当前本地快照中的真实结果。预览没有发起任何远端请求。</p><div className="result-grid"><span><small>店铺</small><strong>zhonglian1</strong></span><span><small>状态</small><strong>uploaded</strong></span><span><small>product_id</small><strong>5482520711</strong></span><span><small>task_id</small><strong>5081018919</strong></span></div><div className="modal-actions"><button type="button" className="ghost-action light" onClick={() => { setModal(null); setActiveTab("店铺"); }}>查看店铺记录</button><button type="button" className="primary-action" onClick={() => setModal(null)}>完成</button></div></section></div> : null}

      {modal === "actions" ? <div className="overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setModal(null); }}><section className="action-sheet" role="dialog" aria-modal="true" aria-label="商品更多操作"><div className="drawer-head"><div><span className="eyebrow">Product actions</span><h2>商品更多操作</h2></div><IconButton label="关闭商品操作" onClick={() => setModal(null)}><X size={21} /></IconButton></div><button type="button" onClick={() => { setModal(null); toast("商品 JSON / CSV / Excel 导出预览已就绪"); }}><DownloadSimple size={19} /><span><strong>导出商品资料</strong><small>JSON、CSV、Excel 与图片包</small></span><ArrowRight size={17} /></button><button type="button" onClick={() => { setModal(null); toast("已打开 1688 原始来源（预览）"); }}><Eye size={19} /><span><strong>查看 1688 来源</strong><small>原始输入保持只读</small></span><ArrowRight size={17} /></button><button type="button" className="danger-action" onClick={() => toast("彻底删除需要再次明确确认；预览不会删除真实商品")}><Trash size={19} /><span><strong>彻底删除本地商品</strong><small>预览中禁用，不影响 Ozon 已上架商品</small></span><ArrowRight size={17} /></button></section></div> : null}
      {toastMessage ? <div className="toast" role="status"><CheckCircle size={18} weight="fill" />{toastMessage}</div> : null}
    </div>
  );
}
