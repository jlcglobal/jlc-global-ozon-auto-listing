import { useEffect, useMemo, useState } from "react";
import {
  Archive,
  ArrowRight,
  CaretDown,
  Check,
  CheckCircle,
  ClipboardText,
  CloudArrowUp,
  FunnelSimple,
  GearSix,
  ImageSquare,
  Info,
  MagnifyingGlass,
  Package,
  ShieldCheck,
  SpinnerGap,
  Storefront,
  Tag,
  WarningCircle,
  X,
} from "@phosphor-icons/react";

const product = {
  id: "P000001",
  title: "便携式厨房搅拌机",
  skuCount: 2,
  progress: 94,
  status: "OZON_READY",
  store: "zhonglian1",
  updatedAt: "昨日 20:34",
  image: "/p000001-main.webp",
};

const navigation = [
  { label: "我的采集箱", icon: Archive, active: true },
  { label: "选品与关键词", icon: MagnifyingGlass },
  { label: "需要我处理", icon: WarningCircle },
  { label: "已上架商品", icon: Package },
  { separator: true },
  { label: "店铺设置", icon: Storefront },
  { label: "系统设置", icon: GearSix },
];

const completedWork = [
  { label: "资料完成", icon: ClipboardText },
  { label: "价格与属性完成", icon: Tag },
  { label: "10张图片完成", icon: ImageSquare },
  { label: "质检通过", icon: ShieldCheck },
];

const activities = [
  "图片处理完成（10张）",
  "价格与属性生成完成",
  "资料生成完成",
  "质检通过",
  "上传前检查完成",
  "商品状态更新为 OZON_READY",
];

function IconButton({ label, children, onClick, active = false }) {
  return (
    <button
      className={`icon-button${active ? " is-active" : ""}`}
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function Sidebar({ notify }) {
  return (
    <aside className="sidebar" aria-label="主导航">
      <div className="brand-lockup">
        <div className="brand-mark" aria-hidden="true">CF</div>
        <strong>跨境AI工厂</strong>
      </div>

      <nav className="primary-nav">
        {navigation.map((item, index) => {
          if (item.separator) return <div className="nav-separator" key={`sep-${index}`} />;
          const Icon = item.icon;
          return (
            <button
              className={`nav-link${item.active ? " active" : ""}`}
              type="button"
              key={item.label}
              aria-current={item.active ? "page" : undefined}
              onClick={() => {
                if (!item.active) notify("独立原型当前只演示“我的采集箱”核心页面");
              }}
            >
              <Icon size={20} weight={item.active ? "duotone" : "regular"} />
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>

      <div className="service-state">
        <CheckCircle size={17} weight="fill" />
        <span>本地服务运行中</span>
      </div>
    </aside>
  );
}

function QueuePane({ query, setQuery, status, setStatus, isVisible, notify }) {
  const [filtersOpen, setFiltersOpen] = useState(false);

  return (
    <section className="queue-pane" aria-labelledby="queue-title">
      <div className="queue-heading">
        <div className="queue-title-row">
          <h2 id="queue-title">采集箱</h2>
          <Info size={18} aria-label="当前仅显示我的商品" />
        </div>
        <div className="queue-controls">
          <label className="compact-select">
            <span className="sr-only">状态筛选</span>
            <select value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="all">全部状态</option>
              <option value="ready">等待上传</option>
              <option value="processing">处理中</option>
              <option value="attention">需要处理</option>
            </select>
            <CaretDown size={14} aria-hidden="true" />
          </label>
          <button className="sort-button" type="button" onClick={() => notify("当前只有 1 件商品，已按最新在前排列")}>最新在前</button>
          <IconButton label="搜索和筛选" active={filtersOpen} onClick={() => setFiltersOpen((value) => !value)}>
            <FunnelSimple size={19} />
          </IconButton>
        </div>

        <div className={`queue-search${filtersOpen ? " open" : ""}`} aria-hidden={!filtersOpen}>
          <MagnifyingGlass size={17} aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索商品或任务号"
            tabIndex={filtersOpen ? 0 : -1}
          />
          {query && (
            <IconButton label="清除搜索" onClick={() => setQuery("")}>
              <X size={16} />
            </IconButton>
          )}
        </div>
      </div>

      <div className="queue-list" aria-live="polite">
        {isVisible ? (
          <button className="product-row active" type="button" aria-pressed="true" onClick={() => notify("已选择 P000001") }>
            <img src={product.image} alt="P000001 便携式厨房搅拌机" />
            <span className="product-row-copy">
              <span className="product-row-title">
                <strong>{product.id}</strong>
                <span className="status-chip"><Check size={11} weight="bold" />{product.status}</span>
              </span>
              <span>{product.title}</span>
              <span className="product-row-meta">
                <span>{product.skuCount}个SKU</span>
                <strong>{product.progress}%</strong>
              </span>
            </span>
          </button>
        ) : (
          <div className="empty-queue">
            <MagnifyingGlass size={28} />
            <strong>没有匹配的商品</strong>
            <span>清除搜索或切回“全部状态”</span>
            <button type="button" onClick={() => { setQuery(""); setStatus("all"); }}>重置筛选</button>
          </div>
        )}
      </div>

      <div className="queue-footer"><span>共 {isVisible ? 1 : 0} 项</span></div>
    </section>
  );
}

function EmptyFocus() {
  return (
    <section className="focus-pane empty-focus" aria-live="polite">
      <MagnifyingGlass size={40} />
      <h2>未选择商品</h2>
      <p>调整左侧筛选后即可继续查看当前任务。</p>
    </section>
  );
}

function FocusPane({ isVisible, openUploadCheck }) {
  if (!isVisible) return <EmptyFocus />;

  return (
    <main className="focus-pane" aria-labelledby="product-heading">
      <div className="product-heading">
        <h2 id="product-heading">{product.id} <span>{product.title}</span></h2>
        <span className="sku-badge">{product.skuCount}个SKU</span>
      </div>

      <section className="product-overview" aria-label="商品进度概览">
        <div className="hero-image-wrap">
          <img src={product.image} alt="便携式厨房搅拌机真实商品图" />
        </div>
        <div className="progress-panel">
          <span className="progress-kicker">整体完成度</span>
          <strong className="progress-number">{product.progress}<small>%</small></strong>
          <div className="progress-track" role="progressbar" aria-label="整体完成度" aria-valuemin="0" aria-valuemax="100" aria-valuenow={product.progress}>
            <span className="progress-fill" style={{ "--target-progress": `${product.progress}%` }} />
          </div>
          <span className="ready-chip"><CheckCircle size={16} weight="fill" />{product.status}</span>
          <time>{product.updatedAt} 更新</time>
        </div>
      </section>

      <section className="completed-section" aria-labelledby="completed-heading">
        <h3 id="completed-heading">已完成的制作工作</h3>
        <div className="completed-list">
          {completedWork.map((item, index) => {
            const Icon = item.icon;
            return (
              <div className="completed-item" style={{ "--item-index": index }} key={item.label}>
                <span className="completed-icon"><Icon size={17} weight="duotone" /></span>
                <strong>{item.label}</strong>
                <time>{product.updatedAt}</time>
              </div>
            );
          })}
        </div>
      </section>

      <div className="focus-actions">
        <button className="primary-cta" type="button" onClick={openUploadCheck}>
          <CloudArrowUp size={22} weight="bold" />
          检查上传
        </button>
        <p>确认内容无误后，才进入真实上传步骤</p>
      </div>
    </main>
  );
}

function ContextPane({ isVisible, activityOpen, setActivityOpen, openUploadCheck }) {
  if (!isVisible) {
    return (
      <aside className="context-pane context-empty">
        <h2>下一步</h2>
        <p>当前筛选下没有商品。</p>
      </aside>
    );
  }

  const shownActivities = activityOpen ? activities : activities.slice(0, 4);

  return (
    <aside className="context-pane" aria-labelledby="next-step-heading">
      <h2 id="next-step-heading">下一步</h2>

      <button className="readiness-card" type="button" onClick={openUploadCheck}>
        <span className="card-icon upload"><CloudArrowUp size={24} weight="duotone" /></span>
        <span className="readiness-copy">
          <strong>上传就绪</strong>
          <span>内容已准备就绪，可检查后进入上传步骤。</span>
        </span>
        <span className="field-label">店铺</span>
        <span className="store-field">{product.store}</span>
      </button>

      <section className="safety-card" aria-labelledby="safety-heading">
        <div className="card-heading">
          <span className="card-icon safe"><ShieldCheck size={25} weight="duotone" /></span>
          <h3 id="safety-heading">安全与规则</h3>
        </div>
        <ul>
          <li><CheckCircle size={16} weight="fill" />无阻断问题</li>
          <li><Info size={16} />Ozon写入0次</li>
          <li><ShieldCheck size={16} />不提交库存</li>
        </ul>
      </section>

      <section className="activity-card" aria-labelledby="activity-heading">
        <h3 id="activity-heading">最近活动</h3>
        <div className="activity-list">
          {shownActivities.map((activity, index) => (
            <div className="activity-item" key={activity} style={{ "--item-index": index }}>
              <CheckCircle size={14} weight="fill" />
              <span>{activity}</span>
              <time>{product.updatedAt}</time>
            </div>
          ))}
        </div>
        <button className="activity-toggle" type="button" onClick={() => setActivityOpen((value) => !value)}>
          {activityOpen ? "收起活动" : "查看全部活动"}
          <ArrowRight size={15} className={activityOpen ? "rotated" : ""} />
        </button>
      </section>
    </aside>
  );
}

function UploadCheckDialog({ open, onClose, onComplete, checking }) {
  if (!open) return null;

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="upload-dialog" role="dialog" aria-modal="true" aria-labelledby="upload-dialog-title">
        <div className="dialog-heading">
          <div>
            <span>独立交互原型</span>
            <h2 id="upload-dialog-title">上传前安全检查</h2>
          </div>
          <IconButton label="关闭" onClick={onClose}><X size={19} /></IconButton>
        </div>

        <div className="dialog-product">
          <img src={product.image} alt="P000001 商品缩略图" />
          <div><strong>{product.id} · {product.title}</strong><span>{product.skuCount}个SKU · {product.store}</span></div>
          <span className="ready-chip"><CheckCircle size={16} weight="fill" />{product.status}</span>
        </div>

        <ul className="dialog-checks">
          <li><CheckCircle size={19} weight="fill" /><span><strong>资料与图片已完成</strong><small>10张商品图片与质检结果已准备</small></span></li>
          <li><CheckCircle size={19} weight="fill" /><span><strong>无远端重复写入</strong><small>当前 Ozon 写入次数为 0</small></span></li>
          <li><ShieldCheck size={19} weight="duotone" /><span><strong>库存字段已排除</strong><small>此流程不提交库存，也不调用库存接口</small></span></li>
        </ul>

        <div className="prototype-warning"><Info size={19} /><span>这是独立演示原型。点击完成只展示成功反馈，不会连接 Factory 后端，也不会调用 Ozon。</span></div>

        <div className="dialog-actions">
          <button className="secondary-action" type="button" onClick={onClose}>返回检查</button>
          <button className="primary-cta compact" type="button" onClick={onComplete} disabled={checking}>
            {checking ? <SpinnerGap size={20} className="spin" /> : <CheckCircle size={20} weight="bold" />}
            {checking ? "正在模拟检查" : "模拟检查完成"}
          </button>
        </div>
      </section>
    </div>
  );
}

export function App() {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [toast, setToast] = useState("");
  const [activityOpen, setActivityOpen] = useState(false);
  const [uploadCheckOpen, setUploadCheckOpen] = useState(false);
  const [checking, setChecking] = useState(false);

  const isVisible = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    const matchesQuery = !normalized || product.id.toLowerCase().includes(normalized) || product.title.includes(normalized);
    const matchesStatus = status === "all" || status === "ready";
    return matchesQuery && matchesStatus;
  }, [query, status]);

  const notify = (message) => {
    setToast("");
    window.requestAnimationFrame(() => setToast(message));
  };

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(""), 2600);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    if (!uploadCheckOpen) return undefined;
    const handleKeyDown = (event) => {
      if (event.key === "Escape") setUploadCheckOpen(false);
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [uploadCheckOpen]);

  const completeMockCheck = () => {
    setChecking(true);
    window.setTimeout(() => {
      setChecking(false);
      setUploadCheckOpen(false);
      notify("演示检查已完成：未发送任何 Ozon 请求");
    }, 900);
  };

  return (
    <div className="app-shell">
      <Sidebar notify={notify} />

      <div className="main-shell">
        <header className="top-titlebar">
          <h1>三栏AI工作室</h1>
        </header>

        <div className="workspace-grid">
          <QueuePane query={query} setQuery={setQuery} status={status} setStatus={setStatus} isVisible={isVisible} notify={notify} />
          <FocusPane isVisible={isVisible} openUploadCheck={() => setUploadCheckOpen(true)} />
          <ContextPane isVisible={isVisible} activityOpen={activityOpen} setActivityOpen={setActivityOpen} openUploadCheck={() => setUploadCheckOpen(true)} />
        </div>
      </div>

      {toast && <div className="toast" role="status"><CheckCircle size={19} weight="fill" />{toast}</div>}
      <UploadCheckDialog open={uploadCheckOpen} onClose={() => setUploadCheckOpen(false)} onComplete={completeMockCheck} checking={checking} />
    </div>
  );
}
