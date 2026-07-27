"use client";

import { useCallback, useEffect, useState } from "react";

type Page = "overview" | "research" | "backtest" | "execution" | "tasks" | "reconcile";
type Run = { id: number; trade_date: string; source: string; status: string; started_at: string; finished_at?: string; error?: string | null };
type Overview = {
  mode: string; symbol: string; symbol_name: string; latest_data_date: string; latest_price: number;
  decision: { action: string; reason_code: string; target_weight: number; fast_ma: number; slow_ma: number };
  portfolio: { cash: number; equity: number; position_quantity: number; available_quantity: number };
  reconciliation: Reconciliation;
  recent_runs: Run[];
  scheduler: TaskConfig;
};
type Reconciliation = {
  matched: boolean; expected_cash: number; actual_cash: number; expected_quantity: number;
  actual_quantity: number; cash_difference: number; quantity_difference: number;
  filled_order_count: number; fill_count: number; missing_fill_count: number; orphan_fill_count: number;
};
type Research = {
  symbol: string; symbol_name: string;
  strategy: { id: string; fast: number; slow: number; account_weight: number; research_sleeve_weight: number; execution: string; status: string };
  fundamental_context: { summary: string; strengths: string[]; risks: string[]; source: string; source_url: string } | null;
  primary: Record<string, string | number>[];
  robustness: Record<string, string | number>[];
  weighted_strategies: Record<string, string | number>[];
  allocations: Record<string, number>[];
  annual: Record<string, number>[];
  trades: Record<string, string | number>[];
  equity_curve: { date: string; equity: number; drawdown: number }[];
};
type Execution = {
  orders: Record<string, string | number>[];
  fills: Record<string, string | number>[];
  attempts: Record<string, string | number>[];
  positions: Record<string, string | number>[];
  reconciliation: Reconciliation;
};
type TaskConfig = { enabled: boolean; hour: number; minute: number; schedule: string; timezone: string };
type Candidate = {
  symbol: string; name: string; enabled: boolean; target_weight: number; research_status: string;
  latest_date: string; latest_price: number; action: string; reason_code: string;
  signal_weight: number; position_quantity: number; market_value: number;
};
type CandidatePool = { items: Candidate[]; active_count: number; target_gross_weight: number; max_gross_weight: number };

const API = "";
const money = new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 2 });
const pct = (value: number) => `${(value * 100).toFixed(2)}%`;
const num = (value: number) => Number(value).toFixed(2);

const pages: { id: Page; icon: string; label: string }[] = [
  { id: "overview", icon: "◈", label: "总览" },
  { id: "research", icon: "⌁", label: "策略研究" },
  { id: "backtest", icon: "◒", label: "回测分析" },
  { id: "execution", icon: "⇄", label: "订单成交" },
  { id: "tasks", icon: "◎", label: "任务配置" },
  { id: "reconcile", icon: "✓", label: "资金对账" },
];

const titles: Record<Page, [string, string]> = {
  overview: ["PERSONAL QUANT COMMAND", "量化运行总览"],
  research: ["M3 RESEARCH LAB", "策略研究"],
  backtest: ["EVIDENCE BEFORE CAPITAL", "回测分析"],
  execution: ["PAPER EXECUTION LEDGER", "订单与成交"],
  tasks: ["SERVICE AUTOMATION", "任务配置"],
  reconcile: ["THREE-WAY RECONCILIATION", "资金与持仓对账"],
};

export default function Dashboard() {
  const [page, setPage] = useState<Page>("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [candidatePool, setCandidatePool] = useState<CandidatePool | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState("600036.SH");
  const [research, setResearch] = useState<Research | null>(null);
  const [execution, setExecution] = useState<Execution | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [taskConfig, setTaskConfig] = useState<TaskConfig | null>(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [updatedAt, setUpdatedAt] = useState("");

  const getJson = useCallback(async <T,>(path: string): Promise<T> => {
    const response = await fetch(`${API}${path}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`${path}: ${response.status}`);
    return response.json();
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [base, pool] = await Promise.all([
        getJson<Overview>(`/api/overview?symbol=${selectedSymbol}`),
        getJson<CandidatePool>("/api/candidates"),
      ]);
      setOverview(base);
      setCandidatePool(pool);
      setTaskConfig(base.scheduler);
      if (page === "research" || page === "backtest") setResearch(await getJson<Research>(`/api/research?symbol=${selectedSymbol}`));
      if (page === "execution" || page === "reconcile") setExecution(await getJson<Execution>("/api/execution"));
      if (page === "tasks") setRuns(await getJson<Run[]>("/api/tasks"));
      setError("");
      setUpdatedAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
    } catch {
      setError("服务端暂时不可用，正在重试");
    }
  }, [getJson, page, selectedSymbol]);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  async function runNow() {
    setRunning(true);
    try {
      await fetch("/api/tasks/daily-run", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      window.setTimeout(refresh, 500);
    } finally {
      setRunning(false);
    }
  }

  async function saveTaskConfig(next: TaskConfig) {
    const response = await fetch("/api/task-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: next.enabled, hour: next.hour, minute: next.minute }),
    });
    if (response.ok) setTaskConfig(await response.json());
  }

  async function addCandidate(symbol: string, name: string) {
    const response = await fetch("/api/candidates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol, name }),
    });
    if (!response.ok) {
      const detail = await response.json();
      throw new Error(detail.detail ?? "添加失败");
    }
    setSelectedSymbol(symbol.toUpperCase());
  }

  if (!overview) return <Loading error={error} />;
  const [eyebrow, title] = titles[page];

  return (
    <main className="app-shell">
      <Sidebar page={page} onChange={setPage} />
      <section className="workspace">
        <header>
          <div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1></div>
          <div className="header-actions">
            <span className="last-update">更新于 {updatedAt}</span>
            <button className="ghost" onClick={refresh}>刷新</button>
            <button className="primary" onClick={runNow} disabled={running}>{running ? "提交中…" : "立即运行"}</button>
          </div>
        </header>
        {error && <div className="warning">{error}</div>}
        <StatusStrip scheduler={taskConfig ?? overview.scheduler} />
        {page === "overview" && candidatePool && <OverviewPage data={overview} pool={candidatePool} selectedSymbol={selectedSymbol} onSelect={setSelectedSymbol} onAdd={addCandidate} onNavigate={setPage} />}
        {page === "research" && (research ? <ResearchPage data={research} /> : <SectionLoading />)}
        {page === "backtest" && (research ? <BacktestPage data={research} /> : <SectionLoading />)}
        {page === "execution" && (execution ? <ExecutionPage data={execution} /> : <SectionLoading />)}
        {page === "tasks" && taskConfig && <TasksPage config={taskConfig} runs={runs} onSave={saveTaskConfig} onRun={runNow} running={running} />}
        {page === "reconcile" && (execution ? <ReconciliationPage data={execution.reconciliation} positions={execution.positions} /> : <SectionLoading />)}
      </section>
    </main>
  );
}

function Sidebar({ page, onChange }: { page: Page; onChange: (page: Page) => void }) {
  return (
    <aside className="sidebar">
      <div className="identity"><div className="brand-mark">M</div><div><strong>MoneyMore</strong><span>QUANT OS</span></div></div>
      <nav>
        {pages.map((item) => (
          <button key={item.id} className={`nav-item ${page === item.id ? "active" : ""}`} onClick={() => onChange(item.id)}>
            <span>{item.icon}</span>{item.label}
          </button>
        ))}
      </nav>
      <div className="safety-card"><span className="pulse" /><div><strong>纸面交易模式</strong><small>真实委托保持锁定</small></div></div>
      <div className="sidebar-foot"><span>服务状态</span><strong><i />在线</strong></div>
    </aside>
  );
}

function StatusStrip({ scheduler }: { scheduler: TaskConfig }) {
  return (
    <div className="status-strip">
      <div><span className="live-dot" />服务端持续运行</div>
      <span>下一次任务 · {scheduler.enabled ? scheduler.schedule : "已暂停"}</span>
      <span>{scheduler.timezone}</span><b>只读实盘权限</b>
    </div>
  );
}

function OverviewPage({ data, pool, selectedSymbol, onSelect, onAdd, onNavigate }: {
  data: Overview; pool: CandidatePool; selectedSymbol: string;
  onSelect: (symbol: string) => void; onAdd: (symbol: string, name: string) => Promise<void>;
  onNavigate: (page: Page) => void;
}) {
  const active = data.decision.target_weight > 0;
  const bars = [36, 43, 39, 56, 49, 62, 58, 71, 68, 76, 73, 82, 79, 88, 84, 92];
  return (
    <>
      <CandidatePortfolio pool={pool} selectedSymbol={selectedSymbol} onSelect={onSelect} onAdd={onAdd} />
      <section className="hero-grid">
        <article className="market-card">
          <div className="card-head"><div><span className="code">{data.symbol}</span><h2>{data.symbol_name}</h2></div><span className="date-chip">数据 {data.latest_data_date}</span></div>
          <div className="quote-row"><strong>¥{data.latest_price.toFixed(2)}</strong><span className={active ? "positive" : "neutral"}>{active ? "趋势开启" : "趋势观察"}</span></div>
          <div className="mini-chart" aria-label="策略趋势示意">{bars.map((height, index) => <i key={index} style={{ height: `${height}%` }} />)}<div className="chart-line fast" /><div className="chart-line slow" /></div>
          <div className="legend"><span><i className="fast-key" />MA120 {data.decision.fast_ma.toFixed(2)}</span><span><i className="slow-key" />MA250 {data.decision.slow_ma.toFixed(2)}</span></div>
        </article>
        <article className="signal-card">
          <div className="card-head"><span className="section-label">M3 STRATEGY SIGNAL</span><span className={`signal-pill ${active ? "on" : ""}`}>{active ? "持有" : "空仓"}</span></div>
          <div className="signal-orbit"><div><small>目标仓位</small><strong>{(data.decision.target_weight * 100).toFixed(0)}%</strong><span>{data.decision.action}</span></div></div>
          <div className="logic-row"><span>策略判断</span><strong>{data.decision.reason_code}</strong></div><div className="logic-row"><span>执行规则</span><strong>次交易日开盘</strong></div>
        </article>
      </section>
      <section className="metric-grid">
        <Metric label="账户权益" value={money.format(data.portfolio.equity)} note="纸面账户" />
        <Metric label="可用现金" value={money.format(data.portfolio.cash)} note="实时账本" />
        <Metric label="当前持仓" value={`${data.portfolio.position_quantity} 股`} note={`可卖 ${data.portfolio.available_quantity}`} />
        <Metric label="三方对账" value={data.reconciliation.matched ? "账实相符" : "发现差异"} note={`现金差 ¥${data.reconciliation.cash_difference.toFixed(2)}`} good={data.reconciliation.matched} />
      </section>
      <section className="lower-grid">
        <article className="runs-card"><div className="card-head"><div><span className="section-label">AUTOMATION</span><h3>最近任务</h3></div><button className="text-button" onClick={() => onNavigate("tasks")}>查看全部 →</button></div><RunList runs={data.recent_runs} /></article>
        <article className="guard-card"><div><span className="section-label">RISK GUARD</span><h3>风险护栏</h3></div><Guard label="单标的仓位" value="≤ 10%" /><Guard label="账户回撤熔断" value="15%" /><Guard label="A股 T+1" value="已启用" /><Guard label="真实委托" value="已锁定" locked /></article>
      </section>
    </>
  );
}

function CandidatePortfolio({ pool, selectedSymbol, onSelect, onAdd }: {
  pool: CandidatePool; selectedSymbol: string; onSelect: (symbol: string) => void;
  onAdd: (symbol: string, name: string) => Promise<void>;
}) {
  const [adding, setAdding] = useState(false);
  const [symbol, setSymbol] = useState("");
  const [name, setName] = useState("");
  const [message, setMessage] = useState("");
  async function submit() {
    try {
      await onAdd(symbol, name);
      setAdding(false); setSymbol(""); setName(""); setMessage("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "添加失败");
    }
  }
  return (
    <article className="portfolio-card">
      <div className="card-head">
        <div><span className="section-label">CANDIDATE PORTFOLIO</span><h3>候选股组合</h3></div>
        <div className="pool-summary"><span>{pool.active_count} 个候选</span><strong>目标上限 {pct(pool.target_gross_weight)}</strong><small>组合护栏 {pct(pool.max_gross_weight)}</small></div>
      </div>
      <div className="candidate-list">
        {pool.items.map((item) => (
          <button key={item.symbol} className={`candidate-item ${selectedSymbol === item.symbol ? "selected" : ""}`} onClick={() => onSelect(item.symbol)}>
            <div className="candidate-top"><span>{item.symbol}</span><i className={item.research_status.toLowerCase()}>{item.research_status}</i></div>
            <strong>{item.name}</strong>
            <div className="candidate-quote"><b>¥{item.latest_price.toFixed(2)}</b><span>{item.action}</span></div>
            <div className="candidate-foot"><span>目标 {pct(item.target_weight)}</span><span>持仓 {item.position_quantity}</span></div>
          </button>
        ))}
        <button className="candidate-add" onClick={() => setAdding(!adding)}><b>＋</b><span>增加候选股</span></button>
      </div>
      {adding && <div className="add-candidate-form"><input aria-label="证券代码" placeholder="例如 600900.SH" value={symbol} onChange={(event) => setSymbol(event.target.value)} /><input aria-label="股票名称" placeholder="股票名称" value={name} onChange={(event) => setName(event.target.value)} /><button className="primary" onClick={submit}>加入候选池</button>{message && <span>{message}</span>}</div>}
    </article>
  );
}

function ResearchPage({ data }: { data: Research }) {
  const oos = data.primary.find((row) => row.strategy === "trend_120_250" && row.period === "out_of_sample");
  const watch = data.strategy.status === "WATCH";
  return (
    <>
      <section className="research-hero">
        <article className="strategy-identity">
          <span className="section-label">FIXED CANDIDATE · {data.symbol}</span><h2>{data.symbol_name} · MA120 / MA250</h2>
          <p>价格站上慢线且快线高于慢线时持有；信号收盘确认，下一交易日开盘成交。</p>
          <div className="strategy-tags"><span>{data.symbol_name}</span><span>账户仓位 10%</span><span>样本外 2022—2026</span></div>
        </article>
        <article className={`verdict-card ${watch ? "watch" : ""}`}><small>研究结论</small><strong>{watch ? "观察候选" : "稳健候选"}</strong><p>{watch ? "趋势过滤降低波动，但样本外收益与夏普偏弱，暂不升级为正式候选。" : "降低单股深度回撤，但收益能力有限，适合作为低权重策略组件。"}</p></article>
      </section>
      <section className="metric-grid research-metrics">
        <Metric label="样本外 CAGR" value={pct(Number(oos?.cagr ?? 0))} note="80% 研究袖套" />
        <Metric label="最大回撤" value={pct(Number(oos?.max_drawdown ?? 0))} note="样本外" good />
        <Metric label="夏普比率" value={num(Number(oos?.sharpe ?? 0))} note="无风险利率 0" />
        <Metric label="成交次数" value={`${Number(oos?.fills_full ?? 0)} 笔`} note="完整历史" />
      </section>
      <article className="table-card">
        <div className="card-head"><div><span className="section-label">PRE-REGISTERED TESTS</span><h3>预注册策略对比</h3></div><span className="date-chip">训练 / 样本外严格分离</span></div>
        <DataTable columns={["strategy", "period", "cagr", "max_drawdown", "sharpe", "average_exposure"]} rows={data.primary} percent={["cagr", "max_drawdown", "average_exposure"]} />
      </article>
      <article className="table-card weighted-card">
        <div className="card-head"><div><span className="section-label">WEIGHTED STRATEGIES · GEN 2</span><h3>基础持仓 + 战术权重</h3></div><span className="date-chip">研究中 · 尚未用于纸面交易</span></div>
        <p className="table-intro">不再把股票简化为全仓或清仓。基础仓位负责长期暴露，趋势或波动率模块只决定增减仓；所有信号仍在下一交易日开盘执行。</p>
        <DataTable columns={["strategy", "period", "cagr", "volatility", "max_drawdown", "sharpe", "fills_full", "average_target"]} rows={data.weighted_strategies} percent={["cagr", "volatility", "max_drawdown", "average_target"]} />
      </article>
      {data.fundamental_context && <article className="context-card">
        <div><span className="section-label">BUSINESS CONTEXT</span><h3>业务背景与非量化风险</h3><p>{data.fundamental_context.summary}</p><a href={data.fundamental_context.source_url} target="_blank" rel="noreferrer">{data.fundamental_context.source} →</a></div>
        <div><strong>观察优势</strong>{data.fundamental_context.strengths.map((item) => <span key={item}>＋ {item}</span>)}</div>
        <div><strong>核心风险</strong>{data.fundamental_context.risks.map((item) => <span key={item}>— {item}</span>)}</div>
      </article>}
      <article className="table-card">
        <div className="card-head"><div><span className="section-label">ROBUSTNESS</span><h3>参数与成本压力测试</h3></div></div>
        <DataTable columns={["strategy", "scenario", "cagr", "max_drawdown", "sharpe"]} rows={data.robustness} percent={["cagr", "max_drawdown"]} />
      </article>
    </>
  );
}

function BacktestPage({ data }: { data: Research }) {
  const values = data.equity_curve.map((point) => point.equity);
  const min = Math.min(...values), max = Math.max(...values);
  return (
    <>
      <article className="equity-card">
        <div className="card-head"><div><span className="section-label">EQUITY CURVE</span><h3>策略净值路径</h3></div><span className="date-chip">2015—2026 · 80%研究袖套</span></div>
        <div className="equity-chart">
          {data.equity_curve.map((point) => <i key={point.date} title={`${point.date} ${money.format(point.equity)}`} style={{ height: `${18 + ((point.equity - min) / Math.max(max - min, 1)) * 76}%` }} />)}
        </div>
        <div className="chart-axis"><span>{data.equity_curve[0]?.date}</span><span>{data.equity_curve.at(-1)?.date}</span></div>
      </article>
      <section className="split-grid">
        <article className="table-card compact"><div className="card-head"><div><span className="section-label">ALLOCATION</span><h3>账户权重敏感性</h3></div></div><DataTable columns={["weight", "cagr", "volatility", "max_drawdown", "sharpe"]} rows={data.allocations} percent={["weight", "cagr", "volatility", "max_drawdown"]} /></article>
        <article className="table-card compact"><div className="card-head"><div><span className="section-label">ANNUAL</span><h3>年度收益</h3></div></div><DataTable columns={["year", "trend_120_250", "cmb_buy_hold_80", "csi300_price", "csi_bank_price"]} rows={data.annual} percent={["trend_120_250", "cmb_buy_hold_80", "csi300_price", "csi_bank_price"]} /></article>
      </section>
      <article className="table-card"><div className="card-head"><div><span className="section-label">CLOSED TRADES</span><h3>历史闭合交易</h3></div><span className="date-chip">{data.trades.length} 笔</span></div><DataTable columns={["entry_date", "exit_date", "holding_days", "entry_price", "exit_price", "net_return", "fees"]} rows={data.trades} percent={["net_return"]} /></article>
    </>
  );
}

function ExecutionPage({ data }: { data: Execution }) {
  return (
    <>
      <section className="metric-grid">
        <Metric label="订单总数" value={`${data.orders.length}`} note="最近 100 条" />
        <Metric label="成交总数" value={`${data.fills.length}`} note="纸面成交" />
        <Metric label="执行尝试" value={`${data.attempts.length}`} note="含延迟与拒绝" />
        <Metric label="账本状态" value={data.reconciliation.matched ? "正常" : "异常"} note="三方对账" good={data.reconciliation.matched} />
      </section>
      <article className="table-card"><div className="card-head"><div><span className="section-label">ORDER INTENTS</span><h3>订单意图</h3></div></div><DataTable columns={["signal_date", "symbol", "side", "quantity", "status", "reason_code"]} rows={data.orders} /></article>
      <section className="split-grid">
        <article className="table-card compact"><div className="card-head"><div><span className="section-label">FILLS</span><h3>成交回报</h3></div></div><DataTable columns={["trade_date", "side", "quantity", "price", "fee"]} rows={data.fills} /></article>
        <article className="table-card compact"><div className="card-head"><div><span className="section-label">ATTEMPTS</span><h3>撮合尝试</h3></div></div><DataTable columns={["trade_date", "outcome", "reason_code"]} rows={data.attempts} /></article>
      </section>
    </>
  );
}

function TasksPage({ config, runs, onSave, onRun, running }: { config: TaskConfig; runs: Run[]; onSave: (next: TaskConfig) => void; onRun: () => void; running: boolean }) {
  const [draft, setDraft] = useState(config);
  useEffect(() => setDraft(config), [config]);
  return (
    <>
      <section className="task-layout">
        <article className="settings-card">
          <div className="card-head"><div><span className="section-label">DAILY PIPELINE</span><h3>日终任务</h3></div><label className="switch"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} /><span /></label></div>
          <div className="setting-row"><div><strong>运行时间</strong><small>Asia/Shanghai，本机服务端调度</small></div><div className="time-fields"><input aria-label="小时" type="number" min="0" max="23" value={draft.hour} onChange={(event) => setDraft({ ...draft, hour: Number(event.target.value) })} /><b>:</b><input aria-label="分钟" type="number" min="0" max="59" value={draft.minute} onChange={(event) => setDraft({ ...draft, minute: Number(event.target.value) })} /></div></div>
          <div className="pipeline-steps">{["交易日校验", "增量入库", "数据门禁", "撮合旧单", "生成信号", "三方对账"].map((step, index) => <div key={step}><i>{index + 1}</i><span>{step}</span></div>)}</div>
          <div className="form-actions"><button className="ghost" onClick={onRun} disabled={running}>立即运行</button><button className="primary" onClick={() => onSave(draft)}>保存配置</button></div>
        </article>
        <article className="guard-card"><div><span className="section-label">IMMUTABLE CORE</span><h3>组合锁定项</h3></div><Guard label="候选池" value="逐股独立信号" /><Guard label="均线参数" value="120 / 250" /><Guard label="单股目标" value="10%" /><Guard label="执行模式" value="PAPER" locked /></article>
      </section>
      <article className="runs-card full"><div className="card-head"><div><span className="section-label">RUN HISTORY</span><h3>任务运行历史</h3></div><span className="date-chip">{runs.length} 条</span></div><RunList runs={runs} /></article>
    </>
  );
}

function ReconciliationPage({ data, positions }: { data: Reconciliation; positions: Record<string, string | number>[] }) {
  return (
    <>
      <article className={`reconcile-hero ${data.matched ? "matched" : "broken"}`}><div className="reconcile-seal">{data.matched ? "✓" : "!"}</div><div><span className="section-label">RECONCILIATION RESULT</span><h2>{data.matched ? "账实完全相符" : "发现账本差异"}</h2><p>从成交流水独立重建现金与净持仓，并核对 FILLED 订单和成交回报。</p></div></article>
      <section className="metric-grid">
        <Metric label="账面现金" value={money.format(data.actual_cash)} note={`应有 ${money.format(data.expected_cash)}`} good={Math.abs(data.cash_difference) < 0.01} />
        <Metric label="账面持仓" value={`${data.actual_quantity} 股`} note={`应有 ${data.expected_quantity} 股`} good={data.quantity_difference === 0} />
        <Metric label="FILLED 订单" value={`${data.filled_order_count}`} note={`${data.fill_count} 条成交`} good={data.missing_fill_count === 0} />
        <Metric label="孤儿成交" value={`${data.orphan_fill_count}`} note={`缺失成交 ${data.missing_fill_count}`} good={data.orphan_fill_count === 0} />
      </section>
      <article className="table-card"><div className="card-head"><div><span className="section-label">POSITIONS</span><h3>持仓账本</h3></div></div><DataTable columns={["symbol", "quantity", "available_quantity", "avg_cost", "last_buy_date"]} rows={positions} /></article>
    </>
  );
}

function DataTable({ columns, rows, percent = [] }: { columns: string[]; rows: Record<string, string | number>[]; percent?: string[] }) {
  return (
    <div className="table-scroll"><table><thead><tr>{columns.map((column) => <th key={column}>{column.replaceAll("_", " ")}</th>)}</tr></thead><tbody>
      {rows.length === 0 && <tr><td colSpan={columns.length} className="empty-cell">暂无记录</td></tr>}
      {rows.map((row, index) => <tr key={index}>{columns.map((column) => {
        const value = row[column];
        const shown = typeof value === "number" ? (percent.includes(column) ? pct(value) : Number.isInteger(value) ? value : num(value)) : value ?? "—";
        return <td key={column}>{String(shown)}</td>;
      })}</tr>)}
    </tbody></table></div>
  );
}

function RunList({ runs }: { runs: Run[] }) {
  return <div className="run-list">{runs.length === 0 && <p className="empty">服务已就绪，等待首次任务。</p>}{runs.map((run) => <div className="run-row" key={run.id}><div className={`run-icon ${run.status.toLowerCase()}`}>{run.status === "FAILED" ? "!" : "✓"}</div><div className="run-main"><strong>日终量化流水线</strong><span>{run.trade_date} · {run.source === "MANUAL" ? "手工触发" : "定时执行"}{run.error ? ` · ${run.error}` : ""}</span></div><span className={`run-status ${run.status.toLowerCase()}`}>{statusLabel(run.status)}</span></div>)}</div>;
}

function statusLabel(status: string) {
  return ({ COMPLETED: "已完成", RUNNING: "运行中", QUEUED: "排队中", FAILED: "失败", SKIPPED_MARKET_CLOSED: "休市跳过", SKIPPED_BUSY: "任务繁忙" } as Record<string, string>)[status] ?? status;
}
function Metric({ label, value, note, good }: { label: string; value: string; note: string; good?: boolean }) { return <article className="metric-card"><span>{label}</span><strong className={good ? "good" : ""}>{value}</strong><small>{note}</small></article>; }
function Guard({ label, value, locked }: { label: string; value: string; locked?: boolean }) { return <div className="guard-row"><span>{label}</span><strong className={locked ? "locked" : ""}>{value}</strong></div>; }
function Loading({ error }: { error: string }) { return <main className="loading-shell"><div className="brand-mark">M</div><p>{error || "正在连接 MoneyMore 服务…"}</p></main>; }
function SectionLoading() { return <div className="section-loading"><i /><span>正在计算并载入真实研究数据…</span></div>; }
