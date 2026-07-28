"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

type Page = "overview" | "factors" | "portfolio" | "execution" | "pipeline";
type Row = Record<string, unknown>;
type Run = { id: number; trade_date: string; source: string; status: string; started_at: string; error?: string | null };
type DashboardData = {
  mode: string;
  model_id: string;
  status: string;
  latest_date: string;
  warning: string;
  report: Row[];
  robustness: Row[];
  promotion: { eligible: boolean; status: string; checks: Record<string, boolean>; reasons: string[] };
  latest_scores: Row[];
  latest_holdings: Row[];
  equity_curve: { date: string; equity: number; drawdown: number }[];
  scheduler: { enabled: boolean; time: string; timezone: string; next_run?: string };
  recent_runs: Run[];
  timing: {
    interface: string; active_strategy: string; status: string; risk_degree: number;
    base_gross_exposure: number; bank_budget_fraction: number; latest_date: string;
    decision: string; evidence_note: string; report: Row[]; current: Row[];
  };
  shadow: {
    status: string; trade_date?: string; model_id?: string; holdings: string[];
    ranking: Row[]; orders: Row[]; executions: Row[];
    portfolio: { cash: number; market_value: number; equity: number; positions: Row[] };
    reconciliation?: Record<string, unknown>;
  };
};
type FactorData = {
  universe: string; latest_date: string; latest_members: number;
  membership_rows: number; ic: Row[]; period_ic: Row[]; quantiles: Row[];
};
type ExecutionData = {
  orders: Row[]; fills: Row[]; positions: Row[];
  portfolio: DashboardData["shadow"]["portfolio"] | null;
  reconciliation: Record<string, unknown>;
};
type SectorData = {
  status: string; latest_date: string; disclosure_date: string;
  evidence_status: string; warning: string; allocation_method: string;
  allocation: Row[]; report: Row[];
  universes: { sector: string; name: string; fund_code: string; style: string; ranking: Row[] }[];
};

const pages: { id: Page; label: string; eyebrow: string }[] = [
  { id: "overview", label: "组合总览", eyebrow: "MONITOR" },
  { id: "factors", label: "因子研究", eyebrow: "RESEARCH" },
  { id: "portfolio", label: "组合构建", eyebrow: "TOP-K" },
  { id: "execution", label: "成交对账", eyebrow: "SHADOW" },
  { id: "pipeline", label: "每日流水线", eyebrow: "OPS" },
];

const factorNames: Record<string, string> = {
  pe_ttm: "市盈率", pb: "市净率", dividend_yield: "股息率", volatility_20d: "20日波动",
  volatility_60d: "60日波动", max_drawdown_120d: "120日回撤", return_20d: "20日动量",
  return_60d: "60日动量", return_120d: "120日动量", roe: "ROE", roa: "ROA",
  net_profit_growth: "净利润增速", revenue_growth: "营收增速",
};

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { cache: "no-store", ...init });
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  return response.json() as Promise<T>;
}
const money = (v: unknown) => Number(v ?? 0).toLocaleString("zh-CN", { maximumFractionDigits: 0 });
const pct = (v: unknown) => `${(Number(v ?? 0) * 100).toFixed(2)}%`;
const num = (v: unknown, digits = 2) => Number(v ?? 0).toFixed(digits);
const text = (v: unknown) => v === null || v === undefined ? "—" : String(v);

export default function Dashboard() {
  const [page, setPage] = useState<Page>("overview");
  const [data, setData] = useState<DashboardData | null>(null);
  const [factors, setFactors] = useState<FactorData | null>(null);
  const [execution, setExecution] = useState<ExecutionData | null>(null);
  const [sectors, setSectors] = useState<SectorData | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [dashboard, factorData, executionData, sectorData] = await Promise.all([
        getJson<DashboardData>("/api/bank-dashboard"),
        getJson<FactorData>("/api/factor-research?universe=bank_cn"),
        getJson<ExecutionData>("/api/bank-execution"),
        getJson<SectorData>("/api/sector-portfolio"),
      ]);
      setData(dashboard); setFactors(factorData); setExecution(executionData); setSectors(sectorData); setError("");
    } catch (e) { setError(e instanceof Error ? e.message : "服务暂不可用"); }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    const interval = window.setInterval(refresh, 30_000);
    return () => { clearTimeout(initial); clearInterval(interval); };
  }, [refresh]);

  const runPipeline = async () => {
    setBusy(true);
    try {
      await getJson("/api/tasks/daily-run", { method: "POST" });
      window.setTimeout(() => void refresh(), 1200);
    } catch (e) { setError(e instanceof Error ? e.message : "触发失败"); }
    finally { setBusy(false); }
  };

  if (!data || !factors || !execution || !sectors) {
    return <main className="loading"><span className="pulse">M</span><p>{error || "正在连接 MoneyMore 组合服务…"}</p><button onClick={() => void refresh()}>重新连接</button></main>;
  }

  const active = pages.find((item) => item.id === page)!;
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><span>M</span><div><strong>MoneyMore</strong><small>QUANT RESEARCH SYSTEM</small></div></div>
        <div className="model-card"><small>ACTIVE RESEARCH</small><b>银行多因子组合</b><span>Top 8 · 月度调仓 · 影子盘</span></div>
        <nav>{pages.map((item, index) => <button className={page === item.id ? "active" : ""} key={item.id} onClick={() => setPage(item.id)}><i>0{index + 1}</i><span>{item.label}<small>{item.eyebrow}</small></span></button>)}</nav>
        <div className="sidebar-foot"><span className="live-dot" /> PAPER ONLY<br/><small>{data.scheduler.time} {data.scheduler.timezone}</small></div>
      </aside>
      <main className="content">
        <header><div><small>{active.eyebrow} / BANK_CN</small><h1>{active.label}</h1></div><div className="actions"><button className="ghost" onClick={() => void refresh()}>刷新数据</button><button className="primary" disabled={busy} onClick={() => void runPipeline()}>{busy ? "启动中…" : "运行今日流水线"}</button></div></header>
        {error && <div className="error">{error}</div>}
        {page === "overview" && <Overview data={data} sectors={sectors} />}
        {page === "factors" && <Factors data={factors} />}
        {page === "portfolio" && <Portfolio data={data} sectors={sectors} />}
        {page === "execution" && <Execution data={execution} />}
        {page === "pipeline" && <Pipeline data={data} />}
      </main>
    </div>
  );
}

function Overview({ data, sectors }: { data: DashboardData; sectors: SectorData }) {
  const sample = data.report.find((r) => r.period === "sample_out") ?? data.report.at(-1) ?? {};
  const portfolio = data.shadow.portfolio;
  return <>
    <section className="advice-banner">
      <div><span className="tag orange">当前建仓建议</span><b>银行模拟仓持股 {pct(data.timing.risk_degree)}</b><p>若银行预算固定为总账户10%，当前目标银行持股为总账户 <strong>{pct(data.timing.risk_degree * 0.1)}</strong>，其余 <strong>{pct((1 - data.timing.risk_degree) * 0.1)}</strong> 保留现金。</p></div>
      <div><small>QLIB RISK DEGREE</small><b>{pct(data.timing.risk_degree)}</b><span>{data.timing.active_strategy} · {data.timing.latest_date}</span></div>
    </section>
    <section className="hero">
      <div><span className="tag orange">PROSPECTIVE CANDIDATE</span><h2>从单股择时，升级为<br/><em>横截面选股 + 组合执行</em></h2><p>价值、防御与动量共同排序；质量因子暂时降权为零。策略只运行影子盘，真实前瞻证据从 2026-07-27 起累计。</p></div>
      <div className="hero-score"><small>MODEL SCORECARD</small><b>{num(sample.sharpe)}</b><span>样本外夏普</span><hr/><label>{pct(sample.cagr)} 年化 · {pct(sample.max_drawdown)} 最大回撤</label></div>
    </section>
    <section className="metrics">
      <Metric label="影子账户权益" value={`¥ ${money(portfolio.equity)}`} note={`${portfolio.positions?.length ?? 0} 个实际持仓`} />
      <Metric label="目标持仓" value={`${data.shadow.holdings?.length ?? 0} / 8`} note={`信号日 ${data.shadow.trade_date ?? "待首次运行"}`} />
      <Metric label="研究股票池" value="39" note={`数据截至 ${data.latest_date}`} />
      <Metric label="银行模拟仓持股" value={pct(data.timing.risk_degree)} note={`总账户10%预算时实际持股 ${pct(data.timing.risk_degree * 0.1)}`} tone="orange" />
    </section>
    <Card title="跨行业动态仓位" subtitle={`银行 + 四类 ETF 权重股 · ${sectors.allocation_method}`}>
      <DataTable rows={sectors.allocation} columns={[["sector","行业袖套"],["budget_weight","风险预算"],["risk_degree","择时仓位"],["target_weight","账户目标"],["cash_weight","保留现金"],["selected","当前候选"]]} format={{ budget_weight: pct, risk_degree: pct, target_weight: pct, cash_weight: pct }} />
      <p className="evidence-note">{sectors.warning}</p>
    </Card>
    <div className="grid two">
      <Card title="组合净值与回撤" subtitle="历史研究曲线，仅用于模型证据"><EquityCurve rows={data.equity_curve} /></Card>
      <Card title="当前目标篮子" subtitle="缓冲区与最多两只替换约束"><HoldingTiles rows={data.latest_holdings} /></Card>
    </div>
    <div className="grid two lower">
      <Card title="建仓建议" subtitle="Qlib risk_degree 动态市场暴露"><TimingAdvice timing={data.timing} /></Card>
      <Card title="最近流水线" subtitle="服务端常驻任务记录"><RunList rows={data.recent_runs.slice(0, 6)} /></Card>
    </div>
    <Card title="模型晋级门槛" subtitle="不因漂亮回测自动进入模拟主策略"><Gate promotion={data.promotion} /></Card>
  </>;
}

function Factors({ data }: { data: FactorData }) {
  const periods = data.period_ic.filter((r) => Number(r.horizon) === 20);
  const train = periods.filter((r) => r.period === "sample_in").sort((a, b) => Math.abs(Number(b.rank_ic_mean)) - Math.abs(Number(a.rank_ic_mean)));
  const out = periods.filter((r) => r.period === "sample_out");
  return <>
    <section className="page-intro"><div><span className="tag">POINT-IN-TIME</span><h2>因子不是指标陈列，<br/>而是一套证据生产线。</h2></div><p>所有基本面字段按可用日期对齐，横截面去极值、标准化；IC 与分层收益分别在样本内外观察。</p></section>
    <section className="metrics">
      <Metric label="因子注册表" value="21" note="价格 / 估值 / 基本面 / 分红" />
      <Metric label="当前成分股" value={text(data.latest_members)} note={`银行股 · ${data.latest_date}`} />
      <Metric label="历史成员记录" value={money(data.membership_rows)} note="避免幸存者偏差" />
      <Metric label="观察窗口" value="20D" note="Rank IC 主视角" />
    </section>
    <div className="grid two">
      <Card title="样本内 Rank IC" subtitle="按绝对均值排序">
        <div className="factor-list">{train.slice(0, 10).map((row) => <FactorBar key={text(row.factor)} row={row} />)}</div>
      </Card>
      <Card title="样本外对照" subtitle="同因子、同口径，不重新调参">
        <DataTable rows={out.slice(0, 12)} columns={[["factor","因子"],["rank_ic_mean","Rank IC"],["rank_ic_ir","ICIR"],["count","样本"]]} format={{ factor: (v) => factorNames[text(v)] ?? text(v), rank_ic_mean: num, rank_ic_ir: num }} />
      </Card>
    </div>
    <Card title="研究纪律" subtitle="当前结论的证据边界">
      <div className="principles"><div><b>01</b><span><strong>不偷看未来</strong>财报按公告可用日进入截面。</span></div><div><b>02</b><span><strong>不把验证集叫测试集</strong>2022 年后已参与开发判断。</span></div><div><b>03</b><span><strong>前瞻才能晋级</strong>2026-07-27 后影子盘是新增证据。</span></div></div>
    </Card>
  </>;
}

function Portfolio({ data, sectors }: { data: DashboardData; sectors: SectorData }) {
  const rows = data.shadow.ranking?.length ? data.shadow.ranking : data.latest_scores;
  return <>
    <section className="page-intro"><div><span className="tag">BANK MULTIFACTOR</span><h2>选最强的一篮子，<br/>不是赌一条均线。</h2></div><p>价值 47.1% · 防御 29.4% · 动量 23.5%。Top 8 入选，Rank 12 缓冲，每期最多替换 2 只，抑制换手。</p></section>
    <div className="grid portfolio-layout">
      <Card title="组合构建规则" subtitle="已冻结的前瞻候选模型">
        <div className="weights"><Weight name="价值" value={47.1}/><Weight name="防御" value={29.4}/><Weight name="动量" value={23.5}/><Weight name="质量" value={0}/></div>
        <div className="rule-strip"><span>TOP <b>8</b></span><span>BUFFER <b>12</b></span><span>MAX SWAP <b>2</b></span><span>WEIGHT <b>10%</b></span></div>
      </Card>
      <Card title="目标持仓" subtitle={`生成日期 ${data.shadow.trade_date ?? data.latest_date}`}><HoldingTiles rows={data.shadow.holdings?.length ? data.shadow.ranking.filter(r => Boolean(r.selected)) : data.latest_holdings} /></Card>
    </div>
    <Card title="择时策略比较" subtitle="相同Top-K、成本和T+1成交规则；KAMA保留为被拒绝基线">
      <DataTable rows={data.timing.report} columns={[["strategy","risk_degree策略"],["period","区间"],["cagr","年化"],["max_drawdown","最大回撤"],["sharpe","夏普"],["average_risk_degree","平均风险度"],["fills","成交数"]]} format={{ cagr: pct, max_drawdown: pct, sharpe: num, average_risk_degree: pct, fills: money }} />
    </Card>
    <Card title="实时横截面排名" subtitle={`${rows.length} 只银行股的可审计打分结果`}>
      <DataTable rows={rows} columns={[["rank","排名"],["symbol","证券"],["score","总分"],["value_score","价值"],["defensive_score","防御"],["momentum_score","动量"],["selected","目标"]]} format={{ score: num, value_score: num, defensive_score: num, momentum_score: num, selected: (v) => v ? "持有" : "—" }} />
    </Card>
    <Card title="行业候选池与差异化模型" subtitle={`ETF 持仓披露日 ${sectors.disclosure_date} · 当前仅运行影子研究`}>
      {sectors.universes.map((universe) => <div key={universe.sector} className="sector-block">
        <h3>{universe.name} <small>{universe.fund_code} · {universe.style}</small></h3>
        <DataTable rows={universe.ranking.slice(0, 10)} columns={[["rank","排名"],["symbol","证券"],["score","行业分"],["selected","目标"]]} format={{ score: num, selected: (v) => v ? "持有" : "—" }} />
      </div>)}
      <p className="evidence-note">{sectors.warning}</p>
    </Card>
  </>;
}

function Execution({ data }: { data: ExecutionData }) {
  const recon = data.reconciliation;
  return <>
    <section className="metrics">
      <Metric label="账户权益" value={`¥ ${money(data.portfolio?.equity)}`} note="bank_shadow 独立账户" />
      <Metric label="可用现金" value={`¥ ${money(data.portfolio?.cash)}`} note="收盘盯市" />
      <Metric label="累计成交" value={text(data.fills.length)} note="最近 100 条" />
      <Metric label="对账" value={recon.matched ? "一致" : "需检查"} note={`现金差 ${money(recon.cash_difference)}`} tone={recon.matched ? undefined : "orange"} />
    </section>
    <div className="grid two">
      <Card title="当前持仓" subtitle="影子经纪商账本"><DataTable rows={data.positions} columns={[["symbol","证券"],["quantity","数量"],["available_quantity","可用"],["average_price","成本"]]} format={{ quantity: money, available_quantity: money, average_price: num }} empty="尚未发生首笔成交" /></Card>
      <Card title="对账检查" subtitle="订单、成交、现金与持仓闭环"><Gate promotion={{ eligible: Boolean(recon.matched), status: "", checks: { "现金账一致": Number(recon.cash_difference) === 0, "持仓账一致": Number(recon.quantity_difference) === 0, "无缺失成交": Number(recon.missing_fill_count) === 0, "无孤儿成交": Number(recon.orphan_fill_count) === 0 }, reasons: [] }} /></Card>
    </div>
    <Card title="委托流水" subtitle="信号在下一交易日开盘撮合"><DataTable rows={data.orders} columns={[["trade_date","日期"],["symbol","证券"],["side","方向"],["quantity","数量"],["status","状态"],["reason","原因"]]} format={{ quantity: money }} empty="等待今日流水线生成委托" /></Card>
    <Card title="成交明细" subtitle="费用与滑点进入影子账本"><DataTable rows={data.fills} columns={[["trade_date","日期"],["symbol","证券"],["side","方向"],["quantity","数量"],["price","价格"],["commission","佣金"],["stamp_duty","印花税"]]} format={{ quantity: money, price: num, commission: num, stamp_duty: num }} empty="待下一交易日开盘撮合" /></Card>
  </>;
}

function Pipeline({ data }: { data: DashboardData }) {
  const steps = [["01","交易日校验","确认交易日与最新可用数据"],["02","数据同步","行情、估值、财务与分红增量入库"],["03","因子截面","21 因子计算、去极值与标准化"],["04","组合构建","Top-K 排名、缓冲与换仓约束"],["05","影子撮合","前日委托按当日开盘成交"],["06","对账归档","现金、持仓、成交闭环并固化证据"]];
  return <>
    <section className="pipeline-hero"><div><span className="live-dot"/><small>SERVER SCHEDULER</small><h2>每日 {data.scheduler.time}</h2><p>{data.scheduler.timezone} · 服务端持续运行 · 非 Codex 自动任务</p></div><div className="next-run"><small>NEXT RUN</small><b>{data.scheduler.next_run ? new Date(data.scheduler.next_run).toLocaleString("zh-CN") : "由交易日历触发"}</b></div></section>
    <div className="steps">{steps.map(([n,t,d]) => <div key={n}><b>{n}</b><span><strong>{t}</strong><small>{d}</small></span></div>)}</div>
    <div className="grid two">
      <Card title="最近运行" subtitle="手动与定时任务共用同一条流水线"><RunList rows={data.recent_runs} /></Card>
      <Card title="实盘隔离线" subtitle="华泰 MiniQMT 审批完成前">
        <div className="isolation"><b>PAPER_ONLY</b><p>行情与策略已产品化运行，但委托只写入 bank_shadow 影子账户。未来接入 QMT 时替换执行适配器，不改变研究、组合与风控层。</p><span>研究 → 组合 → 风控 → <em>Paper Broker</em></span></div>
      </Card>
    </div>
  </>;
}

function Card({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return <section className="card"><div className="card-head"><div><h3>{title}</h3>{subtitle && <p>{subtitle}</p>}</div><span>•••</span></div>{children}</section>;
}
function Metric({ label, value, note, tone }: { label: string; value: string; note: string; tone?: string }) {
  return <div className={`metric ${tone ?? ""}`}><small>{label}</small><b>{value}</b><span>{note}</span></div>;
}
function HoldingTiles({ rows }: { rows: Row[] }) {
  return <div className="holdings">{rows.slice(0,8).map((r,i) => <div key={`${text(r.symbol)}-${i}`}><b>{text(r.rank ?? i + 1).padStart(2,"0")}</b><span><strong>{text(r.symbol)}</strong><small>{r.target ? pct(r.target) : "目标持仓"}</small></span></div>)}</div>;
}
function RunList({ rows }: { rows: Run[] }) {
  return <div className="runs">{rows.length ? rows.map(r => <div key={r.id}><span className={`status ${r.status.toLowerCase()}`}/><b>{r.trade_date}</b><span>{r.source}</span><em>{r.status}</em></div>) : <p className="empty">暂无运行记录</p>}</div>;
}
function Gate({ promotion }: { promotion: DashboardData["promotion"] }) {
  return <div className="gate">{Object.entries(promotion.checks).map(([name,ok]) => <div key={name}><span>{ok ? "✓" : "×"}</span><b>{name.replaceAll("_"," ")}</b><em>{ok ? "通过" : "未通过"}</em></div>)}</div>;
}
function FactorBar({ row }: { row: Row }) {
  const value = Number(row.rank_ic_mean ?? 0);
  return <div className="factor-bar"><span>{factorNames[text(row.factor)] ?? text(row.factor)}</span><i><b style={{ width: `${Math.min(100, Math.abs(value) * 500)}%` }} className={value < 0 ? "negative" : ""}/></i><em>{num(value,3)}</em></div>;
}
function Weight({ name, value }: { name: string; value: number }) {
  return <div><span><b>{name}</b><em>{value}%</em></span><i><b style={{ width: `${value}%` }}/></i></div>;
}
function TimingAdvice({ timing }: { timing: DashboardData["timing"] }) {
  const enabled = timing.risk_degree;
  return <div className="timing-advice">
    <div><span>建议银行模拟仓持股</span><b>{pct(enabled)}</b></div>
    <p>若银行模拟仓预算固定为总资金10%，当前目标银行持股约为总资金 <strong>{pct(enabled * 0.1)}</strong>，其余留在模拟现金。</p>
    <div className="timing-scale"><i style={{ width: `${Math.min(100, enabled * 100)}%` }}/></div>
    <ul><li>策略：{timing.active_strategy}</li><li>Qlib风险度：{pct(timing.risk_degree)}</li><li>证据状态：前瞻候选</li><li>数据日：{timing.latest_date}</li></ul>
  </div>;
}
function DataTable({ rows, columns, format = {}, empty = "暂无数据" }: { rows: Row[]; columns: [string,string][]; format?: Record<string,(v: unknown) => string>; empty?: string }) {
  if (!rows.length) return <p className="empty">{empty}</p>;
  return <div className="table-wrap"><table><thead><tr>{columns.map(([,label]) => <th key={label}>{label}</th>)}</tr></thead><tbody>{rows.map((row,i) => <tr key={i}>{columns.map(([key]) => <td key={key}>{format[key] ? format[key](row[key]) : text(row[key])}</td>)}</tr>)}</tbody></table></div>;
}
function EquityCurve({ rows }: { rows: DashboardData["equity_curve"] }) {
  const points = useMemo(() => {
    if (!rows.length) return "";
    const values = rows.map(r => r.equity); const min = Math.min(...values), max = Math.max(...values);
    return rows.map((r,i) => `${(i/(rows.length-1||1))*100},${92-((r.equity-min)/(max-min||1))*78}`).join(" ");
  }, [rows]);
  const last = rows.at(-1);
  return <div className="chart"><div className="chart-value"><b>{last ? num(last.equity,3) : "—"}</b><span>累计净值</span></div><svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-label="组合净值曲线"><polyline points={points} fill="none" vectorEffect="non-scaling-stroke"/></svg><div className="chart-axis"><span>{rows[0]?.date}</span><span>{last?.date}</span></div></div>;
}
