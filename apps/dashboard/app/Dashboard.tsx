"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";

type Row = Record<string, unknown>;
type Page = "overview" | "sectors" | "research" | "challenger" | "data" | "operations";
type Bank = {
  latest_date: string; latest_scores: Row[]; latest_holdings: Row[];
  scheduler: { enabled: boolean; time: string; timezone: string; next_run?: string };
  recent_runs: { id: number; trade_date: string; source: string; status: string }[];
  timing: { risk_degree: number; active_strategy: string; evidence_note: string };
  shadow: { portfolio: { equity: number; cash: number; positions: Row[] } };
  symbol_names: Record<string, string>;
};
type Universe = {
  sector: string; name: string; fund_code: string; style: string;
  factor_weights: Record<string, number>; ranking: Row[];
};
type Sectors = {
  latest_date: string; disclosure_date: string; evidence_status: string; warning: string;
  allocation_method: string; allocation: Row[]; report: Row[]; universes: Universe[];
  portfolio_policy?: Row; risk_metrics?: Row;
  symbol_names: Record<string, string>;
  strategy_universe?: Row; industry_catalog?: Row[]; board_catalog?: Row[];
  strategy_constituents?: Row[];
};
type Execution = {
  account_id: string; status: string; trade_date?: string;
  target_weights: Record<string, number>; symbol_sectors: Record<string, string>;
  theoretical_target_weights?: Record<string, number>; target_adjustments?: Row[];
  orders: Row[]; fills: Row[]; positions: Row[];
  portfolio: Bank["shadow"]["portfolio"] | null; reconciliation: Row;
  attribution: Row[]; symbol_names: Record<string, string>;
  return_attribution: Row[]; risk_attribution: Row[];
  execution_attribution: Row[]; attribution_reconciliation: Row;
  metrics: Row; history: Row[]; deviations: Row[]; risk_alerts: Row[];
  corporate_actions_today: { registered: Row[]; settled: Row[] };
  corporate_action_ledger: Row[];
  risk_state: Row; risk_transitions: Row[];
};
type DataQuality = {
  latest: Row; checks: Row[]; history: Row[]; symbol_names: Record<string, string>;
};
type OperationsCenter = {
  task_runs: Row[]; task_steps: Row[];
  notifications: (Row & { id: number; acknowledged: boolean })[];
  pending_orders: Row[]; deferred_attempts: Row[]; recent_attempts: Row[];
  scheduler: Row; preview: Row & { steps?: Row[]; expected_effects?: string[]; warnings?: string[] };
  readiness: Row & { checks?: Row[] };
  open_execution?: Row & { accounts?: Row[]; fills?: Row[]; pending_orders?: Row[]; account_ids?: string[] };
};
type IntradayExecution = {
  policy_id: string; activation_date: string; account_id: string;
  pending_orders: Row[]; fills: Row[]; account_ticks: Row[];
  latest: Row & { decisions?: Row[]; executions?: Row[] };
};
type ModelRegistry = { versions: Row[]; artifacts: Row[]; bindings: Row[] };
type MonthlyAcceptance = {
  cycle_id: string; start_date: string; observation_date: string; status: string;
  progress: number; expected_earliest_completion: string; checks: Row[];
  evidence: Row; report_path?: string | null;
};
type Challenger = {
  account_id: string; research: Row & { metrics?: Row[] };
  next_trade_date?: string | null;
  forward: Row & { daily?: Row[] };
  comparison: Row & { metrics?: Row[]; histories?: Record<string, Row[]>; independent_histories?: Record<string, Row[]> };
  latest: Row & { selected?: string[]; scores?: Row[]; portfolio?: Bank["shadow"]["portfolio"] };
  orders: Row[]; fills: Row[]; reconciliation: Row;
  baseline: { account_id: string; latest: Row; orders?: Row[]; fills?: Row[] };
  intraday_baseline?: { account_id:string; status:string; portfolio?:Row; orders:Row[]; fills:Row[] };
  historical_execution: Row & { strategies?: (Row & { summary?: Row })[] };
  point_in_time: Row & { historical_replay_gate?: Row };
  governance: Row & { releases?: Row[]; transitions?: Row[] };
  drift: Row & { breaches?: string[] };
  long_term_review: Row & { evaluation?: Row; criteria?: Record<string, boolean>; cost_ratios?: Row };
  trade_cycle_analysis?: Record<string, Row & { summary?: Row; by_symbol?: Row[]; closed_cycles?: Row[]; open_cycles?: Row[] }>;
  candidate_observation?: {
    status: string; candidate_tag?: string; account_id?: string;
    research_gate_passed?: boolean; latest_trade_date?: string;
    leaderboard: Row[]; latest: Row & { selected?: string[]; portfolio?: Bank["shadow"]["portfolio"] };
    orders: Row[]; fills: Row[]; reconciliation?: Row;
    comparison: Row & { metrics?: Row[]; histories?: Record<string, Row[]> };
    candidates?: (Row & { candidate_tag:string; account_id:string; latest:Row & { portfolio?:Bank["shadow"]["portfolio"] }; orders:Row[]; fills:Row[]; comparison:Challenger["comparison"] })[];
    global_strategy_view?: Challenger["comparison"] & { strategy_count?:number };
  };
};

const nav: { id: Page; label: string; note: string }[] = [
  { id: "overview", label: "组合总览", note: "PORTFOLIO" },
  { id: "sectors", label: "行业与个股", note: "SLEEVES" },
  { id: "research", label: "策略研究", note: "MODELS" },
  { id: "challenger", label: "Qlib挑战者", note: "AI LAB" },
  { id: "data", label: "数据健康", note: "QUALITY" },
  { id: "operations", label: "运行与对账", note: "PAPER" },
];
const meta: Record<string, { label: string; color: string; thesis: string }> = {
  bank: { label: "银行", color: "#a8e063", thesis: "估值 · 防御 · 动量" },
  dividend: { label: "红利", color: "#f1be62", thesis: "股息 · 低波 · 价值" },
  metals: { label: "工业有色", color: "#db806e", thesis: "周期 · 动量 · 估值" },
  chip: { label: "芯片", color: "#7e91e8", thesis: "成长 · 景气 · 趋势" },
  science50: { label: "科创50", color: "#a07ce5", thesis: "硬科技 · 成长 · 动量" },
  consumer: { label: "主要消费", color: "#e89a5b", thesis: "品牌 · 质量 · 防御" },
  growth: { label: "创业板成长", color: "#53bfb0", thesis: "营收增长 · ROE · 动量" },
};
const industryColors = ["#a8e063","#f1be62","#db806e","#7e91e8","#a07ce5","#e89a5b","#53bfb0","#6fa0a8","#c08366","#809a5d"];
const sectorMeta = (value: unknown) => {
  const key = text(value);
  if (meta[key]) return meta[key];
  const hash = [...key].reduce((sum, char) => (sum * 31 + char.charCodeAt(0)) >>> 0, 0);
  return { label:key, color:industryColors[hash % industryColors.length], thesis:"全局选股后的行业归因 · 不设行业预算" };
};
const factorLabels: Record<string, string> = {
  momentum_252_21: "12-1月动量", return_120: "120日收益", quarterly_revenue_growth: "季度营收增长",
  roe: "ROE", volatility_60: "60日低波", earnings_yield: "盈利收益率", book_to_price: "账面市值比",
  drawdown_120: "120日回撤", dividend_yield_ttm: "股息率", price_to_ma_60: "60日趋势",
};
const pct = (v: unknown) => `${(Number(v ?? 0) * 100).toFixed(1)}%`;
const num = (v: unknown) => Number(v ?? 0).toFixed(2);
const modelScore = (v: unknown) => Number(v ?? 0).toFixed(6);
const text = (v: unknown) => v == null ? "—" : String(v);
const security = (v: unknown, names: Record<string, string>) => {
  const code = text(v);
  return names[code] ? `${names[code]} · ${code}` : code;
};
const money = (v: unknown) => `¥${Number(v ?? 0).toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`;
const duration = (v: unknown) => {
  const seconds = Math.max(0, Number(v ?? 0));
  return `${Math.floor(seconds / 3600)}小时${Math.floor((seconds % 3600) / 60)}分`;
};
async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { cache: "no-store", ...init });
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  return response.json() as Promise<T>;
}

export default function Dashboard() {
  const [page, setPage] = useState<Page>("overview");
  const [bank, setBank] = useState<Bank | null>(null);
  const [sectors, setSectors] = useState<Sectors | null>(null);
  const [execution, setExecution] = useState<Execution | null>(null);
  const [quality, setQuality] = useState<DataQuality | null>(null);
  const [operations, setOperations] = useState<OperationsCenter | null>(null);
  const [models, setModels] = useState<ModelRegistry | null>(null);
  const [acceptance, setAcceptance] = useState<MonthlyAcceptance | null>(null);
  const [challenger, setChallenger] = useState<Challenger | null>(null);
  const [intraday, setIntraday] = useState<IntradayExecution | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => {
    try {
      const [b, s, e, q, o, m, a, c, i] = await Promise.all([
        json<Bank>("/api/bank-dashboard"), json<Sectors>("/api/sector-portfolio"), json<Execution>("/api/multi-sector-execution"), json<DataQuality>("/api/data-quality"), json<OperationsCenter>("/api/operations-center"), json<ModelRegistry>("/api/model-registry"), json<MonthlyAcceptance>("/api/monthly-acceptance"), json<Challenger>("/api/qlib-challenger"),
        json<IntradayExecution>("/api/intraday-execution"),
      ]);
      setBank(b); setSectors(s); setExecution(e); setQuality(q); setOperations(o); setModels(m); setAcceptance(a); setChallenger(c); setIntraday(i); setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "服务暂不可用"); }
  }, []);
  useEffect(() => {
    const initial = window.setTimeout(refresh, 0);
    const id = window.setInterval(refresh, 30_000);
    return () => { clearTimeout(initial); clearInterval(id); };
  }, [refresh]);
  const run = async () => {
    setBusy(true);
    try { await json("/api/tasks/daily-run", { method: "POST" }); window.setTimeout(refresh, 1200); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "任务启动失败"); }
    finally { setBusy(false); }
  };
  if (!bank || !sectors || !execution || !quality || !operations || !models || !acceptance || !challenger || !intraday) return <main className="loading"><span>M</span><b>MoneyMore</b><p>{error || "正在装载综合组合…"}</p><button onClick={() => void refresh()}>重新连接</button></main>;
  const current = nav.find((item) => item.id === page)!;
  return <div className="app-shell">
    <aside>
      <div className="brand"><span>M</span><div><b>MoneyMore</b><small>MULTI-SECTOR QUANT</small></div></div>
      <div className="system-card"><i/><small>ACTIVE SYSTEM</small><b>全局横截面组合</b><span>统一候选池 · 行业仅作归因</span></div>
      <nav>{nav.map((item, index) => <button key={item.id} className={page === item.id ? "active" : ""} onClick={() => setPage(item.id)}><em>0{index + 1}</em><span>{item.label}<small>{item.note}</small></span></button>)}</nav>
      <div className="aside-foot"><i/> PAPER ONLY<br/><small>{bank.scheduler.time} · {bank.scheduler.timezone}</small></div>
    </aside>
    <main className="workspace">
      <header><div><small>MONEYMORE / {current.note}</small><h1>{current.label}</h1></div><div className="header-actions"><span><i/> 数据日 {sectors.latest_date}</span><button onClick={() => void refresh()}>刷新</button><button className="primary" disabled={busy} onClick={() => void run()}>{busy ? "恢复中…" : "恢复并重算"}</button></div></header>
      {error && <div className="error">{error}</div>}
      {page === "overview" && <Overview sectors={sectors} execution={execution}/>}
      {page === "sectors" && <SectorPage sectors={sectors}/>}
      {page === "research" && <Research sectors={sectors} models={models}/>}
      {page === "challenger" && <><ChallengerPage challenger={challenger} names={sectors.symbol_names}/><ChallengerEvidence challenger={challenger}/><PointInTimeEvidence challenger={challenger}/><GovernanceEvidence challenger={challenger}/><LongTermReview challenger={challenger}/></>}
      {page === "data" && <DataHealth quality={quality}/>}
      {page === "operations" && <Operations bank={bank} execution={execution} operations={operations} acceptance={acceptance} intraday={intraday} names={sectors.symbol_names} onRefresh={refresh}/>}
    </main>
  </div>;
}

function Overview({ sectors, execution }: { sectors: Sectors; execution: Execution }) {
  const invested = sectors.allocation.reduce((sum, row) => sum + Number(row.target_weight), 0);
  const selected = sectors.allocation.reduce((sum, row) => sum + text(row.selected).split(",").filter(Boolean).length, 0);
  return <>
    <section className="portfolio-hero"><div><span>GLOBAL CROSS-SECTION</span><h2>一个候选池，一套全局排名</h2><p>行业不参与名额和预算。模型全局选股，组合层依据历史价格相关性限制重复风险，行业只在此处汇总展示。</p><div className="hero-tags"><b>全局Top-10</b><b>排名递减权重</b><b>相关风险簇</b><b>T+1 影子执行</b></div></div><div className="exposure"><small>当前股票总仓位</small><b>{pct(invested)}</b><div><i style={{width:`${invested*100}%`}}/></div><span>现金 {pct(1-invested)} · 候选 {selected} 只</span></div></section>
    <section className="kpis"><Kpi label="统一候选池" value={String(sectors.strategy_universe?.size??0)} note={`${text(sectors.strategy_universe?.effective_date)} 起生效 · 市值Top1000`}/><Kpi label="全局持仓" value={String(selected)} note="无行业名额与预算"/><Kpi label="股票目标仓位" value={pct(invested)} note="统一账户风险预算" accent/><Kpi label="覆盖行业" value={String(sectors.industry_catalog?.length??0)} note="选股后反向分类归因"/></section>
    <div className="two-col wide"><Panel title="整体配置全貌" subtitle="Top1000全局选股结果按行业标签汇总，行业不参与策略"><Allocation rows={sectors.allocation}/></Panel><Panel title="当前风险结构" subtitle="相关风险约束优先于普通换手缓冲"><div className="decision"><strong>全局排名 + 动态相关簇约束</strong><p>120日相关系数达到 {num(sectors.portfolio_policy?.cluster_correlation_threshold)} 的股票归入同一风险簇，每簇最多 {text(sectors.portfolio_policy?.maximum_cluster_members)} 只。</p>{sectors.allocation.map((row)=>{const m=sectorMeta(row.sector);return <div key={text(row.sector)}><i style={{background:m.color}}/><span>{m.label}</span><b>{pct(row.target_weight)}</b><small>{text(row.selected).split(",").length}只</small></div>})}</div></Panel></div>
    <Panel title="当前目标持仓的行业归因" subtitle="行业由入选股票反向汇总，不构成行业配额"><SectorCards rows={sectors.allocation} names={sectors.symbol_names}/></Panel>
    <div className="two-col"><Panel title="板块覆盖" subtitle="沪深主板、创业板、科创板统一按总市值排序"><Table rows={sectors.board_catalog??[]} columns={[["board","板块"],["stock_count","股票数"],["market_value","总市值（万元）"]]} format={{market_value:money}}/></Panel><Panel title="候选池行业覆盖 Top 20" subtitle="Tushare当前行业分类，仅作为展示与风险归因"><Table rows={(sectors.industry_catalog??[]).slice(0,20)} columns={[["industry","行业"],["stock_count","股票数"],["market_value","总市值（万元）"]]} format={{market_value:money}}/></Panel></div>
    <Evidence sectors={sectors}/>
  </>;
}

function SectorPage({ sectors }: { sectors: Sectors }) {
  const [industry,setIndustry]=useState("全部行业");
  const constituents=sectors.strategy_constituents??[];
  const visible=industry==="全部行业"?constituents:constituents.filter((row)=>text(row.industry)===industry);
  const selectedCount=constituents.filter((row)=>Boolean(row.selected)).length;
  return <><Intro tag="CURRENT UNIVERSE" title="行业与个股已切换到当前Top1000候选集">沪深主板、创业板和科创板按最新总市值统一入池，再按证券主数据反向归类行业。行业只用于浏览和归因，不拥有预算、名额或独立Top-K。</Intro>
    <section className="kpis"><Kpi label="候选股票" value={String(constituents.length)} note={text(sectors.strategy_universe?.universe_version)}/><Kpi label="反向行业" value={String(sectors.industry_catalog?.length??0)} note="当前证券行业标签"/><Kpi label="全局目标" value={String(selectedCount)} note="基线策略当前入选" accent/><Kpi label="当前筛选" value={String(visible.length)} note={industry}/></section>
    <div className="two-col"><Panel title="行业候选分布" subtitle="按行业总市值排序；目标数来自当前基线全局排名"><Table rows={sectors.industry_catalog??[]} columns={[["industry","行业"],["stock_count","候选数"],["selected_count","当前目标"],["market_value","总市值（万元）"]]} format={{market_value:money}}/></Panel><Panel title="上市板块覆盖" subtitle="四个板块共同参与全局排名"><Table rows={sectors.board_catalog??[]} columns={[["board","板块"],["stock_count","候选数"],["market_value","总市值（万元）"]]} format={{market_value:money}}/></Panel></div>
    <Panel title="当前候选集个股明细" subtitle={`显示 ${visible.length} / ${constituents.length} 只；市值排名为当前候选池排名，因子排名为基线策略全局排名`}><div className="preview-controls"><select value={industry} onChange={(event)=>setIndustry(event.target.value)}><option>全部行业</option>{(sectors.industry_catalog??[]).map((row)=><option key={text(row.industry)}>{text(row.industry)}</option>)}</select><b>{industry}</b></div><Table rows={visible} columns={[["market_cap_rank","市值排名"],["symbol","证券"],["industry","行业"],["board","板块"],["total_mv","总市值（万元）"],["global_rank","因子排名"],["factor_score","因子分"],["selected","当前目标"]]} format={{symbol:(v)=>security(v,sectors.symbol_names),total_mv:money,factor_score:num,selected:(v)=>v?"入选":"—"}}/></Panel>
  </>;
}

function Research({ sectors, models }: { sectors: Sectors; models: ModelRegistry }) {
  const reports=sectors.report.filter((row)=>row.period==="sample_out");
  const constituents=sectors.strategy_constituents??[];
  const factorRanking=[...constituents].sort((a,b)=>Number(a.global_rank??99999)-Number(b.global_rank??99999));
  const selectedCount=constituents.filter((row)=>Boolean(row.selected)).length;
  return <><Intro tag="RESEARCH GOVERNANCE" title="Top1000统一横截面研究体系">因子基线和Qlib模型在同一候选池全局评分，行业不参与排名和预算。组合构建只处理Top-K、换手缓冲、整手约束与相关风险簇。</Intro>
    <section className="kpis"><Kpi label="统一研究池" value={String(constituents.length)} note="沪深主板 + 创业板 + 科创板"/><Kpi label="模型路线" value="2" note="因子基线 + Qlib GRU"/><Kpi label="当前基线目标" value={String(selectedCount)} note="全局Top-K与风险簇约束" accent/><Kpi label="新口径前瞻起点" value={text(sectors.strategy_universe?.effective_date)} note="此前历史冻结，不回刷"/></section>
    <Panel title="当前因子基线全局排名" subtitle="所有股票使用同一截面口径；行业列仅用于解释暴露"><Table rows={factorRanking.slice(0,30)} columns={[["global_rank","全局排名"],["symbol","证券"],["industry","行业"],["board","板块"],["factor_score","因子分"],["selected","当前目标"],["target_weight","目标权重"]]} format={{symbol:(v)=>security(v,sectors.symbol_names),factor_score:num,selected:(v)=>v?"入选":"—",target_weight:pct}}/></Panel>
    <Panel title="当前策略架构" subtitle="展示正在运行的研究与组合链路"><div className="architecture">{[["01","版本化候选池","市值Top1000，按月刷新，历史成分不回写"],["02","统一特征层","行情、估值、财务、分红和可交易性按时点对齐"],["03","并行模型层","因子模型与Qlib GRU独立评分、独立账户公平PK"],["04","全局组合层","全局Top-K、退出缓冲、单期替换限制和排名递减权重"],["05","相关风险层","120日收益相关聚类，限制同类风险重复下注"],["06","模拟执行层","T+1撮合、整手、成本、公司行为、对账与收益归因"]].map(([n,t,d])=><div key={n}><b>{n}</b><span><strong>{t}</strong><small>{d}</small></span></div>)}</div></Panel>
    <div className="two-col"><Panel title="候选池行业分布" subtitle="反向分类只用于研究诊断，不形成行业袖套"><Table rows={(sectors.industry_catalog??[]).slice(0,30)} columns={[["industry","行业"],["stock_count","候选数"],["selected_count","当前目标"],["market_value","总市值（万元）"]]} format={{market_value:money}}/></Panel><Panel title="当前组合规则" subtitle="规则来自正在运行的全局组合构建器"><Table rows={Object.entries(sectors.portfolio_policy??{}).map(([rule,value])=>({rule,value:Array.isArray(value)?value.join(", "):value}))} columns={[["rule","规则"],["value","当前值"]]}/></Panel></div>
    <Panel title="模型版本注册表" subtitle="模型版本绑定代码、配置、股票池和数据截止日；任一内容改变都会产生新版本"><Table rows={models.versions} columns={[["version_id","版本"],["lifecycle","生命周期"],["evidence_stage","证据阶段"],["data_cutoff","数据截止"],["universe_hash","股票池指纹"],["code_hash","代码指纹"]]}/></Panel>
    <div className="two-col"><Panel title="证据隔离" subtitle="历史诊断不能自动升级为前瞻证据"><Table rows={models.artifacts.slice(0,20)} columns={[["artifact_type","产物"],["evidence_stage","证据阶段"],["artifact_key","定位"]]}/></Panel><Panel title="信号与订单绑定" subtitle="每个信号和订单都可反查具体模型版本"><Table rows={models.bindings.slice(0,20)} columns={[["trade_date","交易日"],["binding_type","类型"],["symbol","证券"],["version_id","模型版本"]]}/></Panel></div>
    <Panel title="迁移前分行业历史诊断（归档）" subtitle="旧人工候选池研究结果，仅保留审计，不代表当前Top1000策略"><Table rows={reports} columns={[["sector","旧分类"],["cagr","年化"],["volatility","波动率"],["sharpe","夏普"],["max_drawdown","最大回撤"],["fills","成交数"]]} format={{sector:(v)=>meta[text(v)]?.label??text(v),cagr:pct,volatility:pct,sharpe:num,max_drawdown:pct}}/></Panel><Evidence sectors={sectors}/></>;
}

function ChallengerPage({ challenger, names }: { challenger: Challenger; names: Record<string, string> }) {
  const candidateVersions = challenger.candidate_observation?.candidates??[];
  const [selectedCandidateTag,setSelectedCandidateTag] = useState("");
  const selectedCandidate = candidateVersions.find((row)=>row.candidate_tag===selectedCandidateTag)??candidateVersions.find((row)=>Boolean(row.latest_trade_date))??candidateVersions[0];
  const metrics = challenger.research.metrics??[];
  const researchGate = (challenger.research.gate??{}) as Row;
  const selected = new Set(challenger.latest.selected??[]);
  const sectorRanks: Record<string, number> = {};
  const rankedScores = (challenger.latest.scores??[]).map((row) => {
    const sector = text(row.sector);
    sectorRanks[sector] = (sectorRanks[sector]??0) + 1;
    return {...row, sector_rank:sectorRanks[sector], selected:selected.has(text(row.instrument))};
  });
  const challengerEquity = Number(challenger.latest.portfolio?.equity??1_000_000);
  const baselinePortfolio = challenger.baseline.latest.portfolio as Row|undefined;
  const baselineEquity = Number(baselinePortfolio?.equity??1_000_000);
  const observation = challenger.candidate_observation;
  const candidatePortfolio = selectedCandidate?.latest?.portfolio??observation?.latest?.portfolio;
  const candidateEquity = Number(candidatePortfolio?.equity??1_000_000);
  const globalView = observation?.global_strategy_view??challenger.comparison;
  return <><Intro tag="QLIB CHALLENGER LAB" title="深度学习只能通过公平竞赛晋级">挑战者使用独立资金、模型、信号、订单和持仓。因子影子账户保持冻结；GPU只加速训练，不改变样本外和成本后晋级标准。</Intro>
    <CandidateSwitcher observation={observation} selectedTag={text(selectedCandidate?.candidate_tag)} onSelect={setSelectedCandidateTag}/>
    <div className="four-col account-columns">
      <AccountColumn title="基线" badge="FACTOR BASELINE" accountId={challenger.baseline.account_id} status={text(challenger.baseline.latest.status)} equity={baselineEquity} portfolio={baselinePortfolio} orders={challenger.baseline.orders??[]} fills={challenger.baseline.fills??[]} names={names}/>
      <AccountColumn title="基线·日内执行" badge="ADAPTIVE VWAP" accountId={text(challenger.intraday_baseline?.account_id)} status={text(challenger.intraday_baseline?.status)} equity={Number(challenger.intraday_baseline?.portfolio?.equity??0)} portfolio={challenger.intraday_baseline?.portfolio} orders={challenger.intraday_baseline?.orders??[]} fills={challenger.intraday_baseline?.fills??[]} names={names}/>
      <AccountColumn title="挑战者" badge="ACTIVE GRU" accountId={challenger.account_id} status={text(challenger.latest.status)} equity={challengerEquity} portfolio={challenger.latest.portfolio} targetExposure={Number(challenger.latest.target_gross_exposure)} exposurePolicy={challenger.latest.exposure_policy as Row|undefined} orders={challenger.orders} fills={challenger.fills} names={names}/>
      <AccountColumn title="候选者" badge={text(selectedCandidate?.candidate_tag??observation?.candidate_tag)} accountId={text(selectedCandidate?.account_id??observation?.account_id)} status={text(selectedCandidate?.review_stage??observation?.status)} equity={candidateEquity} portfolio={candidatePortfolio} targetExposure={Number(selectedCandidate?.latest?.target_gross_exposure??observation?.latest?.target_gross_exposure)} exposurePolicy={(selectedCandidate?.latest?.exposure_policy??observation?.latest?.exposure_policy) as Row|undefined} orders={selectedCandidate?.orders??observation?.orders??[]} fills={selectedCandidate?.fills??observation?.fills??[]} names={names} accent={!Boolean(selectedCandidate?.research_gate_passed??observation?.research_gate_passed)}/>
    </div>
    <PerformanceComparisonCharts comparison={globalView}/>
    <CandidateObservation challenger={challenger} selectedTag={text(selectedCandidate?.candidate_tag)}/>
    <TradeCycleComparison challenger={challenger} names={names} candidateAccountId={selectedCandidate?.account_id}/>
    <Panel title="统一样本外模型竞赛" subtitle="相同股票池、标签、训练切分和Top-K规则"><Table rows={metrics} columns={[["model_id","模型"],["segment","区间"],["samples","样本"],["rank_ic","Rank IC"],["rank_ic_ir","Rank ICIR"],["top_k_excess_return","Top-K超额"]]} format={{rank_ic:num,rank_ic_ir:num,top_k_excess_return:pct}}/></Panel>
    <Panel title="GRU最新全局排名" subtitle="统一候选池横截面评分；全局Top-K进入独立挑战者账户，行业仅用于归因"><Table rows={rankedScores.slice(0,30)} columns={[["instrument","证券"],["sector","行业"],["sector_rank","行业内参考排名"],["score","预测分数"],["selected","入选"]]} format={{instrument:(v)=>security(v,names),sector:(v)=>meta[text(v)]?.label??text(v),score:modelScore,selected:(v)=>v?"全局Top-K":"—"}}/></Panel>
    <Panel title="下一交易日委托计划" subtitle="信号在收盘后生成，计划在下一交易日开盘撮合；两个日期必须分开理解"><Table rows={[{signal_date:challenger.latest.trade_date,planned_trade_date:challenger.next_trade_date,status:challenger.latest.status,gate:Boolean(researchGate.passed)?"PASSED（可申请晋级）":"BLOCKED（不可晋级）",selected:(challenger.latest.selected??[]).map((symbol:string)=>security(symbol,names)).join("、"),order_action:"实验模拟盘持续生成 T+1 委托"}]} columns={[["signal_date","信号生成日"],["planned_trade_date","计划成交日"],["status","运行状态"],["gate","研究门禁"],["selected","模型入选"],["order_action","下一步"]]}/></Panel>
    <div className="evidence"><b>隔离边界</b><p>挑战者结果不进入M4.14正式影子验收；只有完成独立样本外、随机种子稳定性和前瞻模拟后，才允许提出模型晋级。</p><span>CHALLENGER_ONLY</span></div>
  </>;
}

function AccountColumn({ title, badge, accountId, status, equity, portfolio, targetExposure, exposurePolicy, orders, fills, names, accent=false }: { title:string; badge:string; accountId:string; status:string; equity:number; portfolio?:Row; targetExposure?:number; exposurePolicy?:Row; orders:Row[]; fills:Row[]; names:Record<string,string>; accent?:boolean }) {
  const positions = (portfolio?.positions??[]) as Row[];
  const marketValue = Number(portfolio?.market_value??0);
  return <article className={`account-column${accent?" account-column-accent":""}`}><header><span>{badge}</span><h3>{title}</h3><small>{accountId}</small></header><div className="account-equity"><b>{money(equity)}</b><span>累计 {pct(equity/1_000_000-1)} · 仓位 {pct(equity?marketValue/equity:0)}{Number.isFinite(targetExposure)?` / 目标 ${pct(targetExposure)}`:""}</span>{exposurePolicy?.realized_volatility!=null&&<small>组合波动 {pct(exposurePolicy.realized_volatility)} · 风险目标 {pct(exposurePolicy.annual_target)}</small>}<i>{status}</i></div><h4>当前持仓 · {positions.length}</h4><Table rows={positions.slice(0,12)} columns={[["symbol","证券"],["quantity","数量"],["avg_cost","成本"]]} format={{symbol:(v)=>security(v,names),avg_cost:num}}/><h4>最近委托</h4><Table rows={orders.slice(0,8)} columns={[["signal_date","信号日"],["symbol","证券"],["side","方向"],["status","状态"]]} format={{symbol:(v)=>security(v,names)}}/><h4>最近成交</h4><Table rows={fills.slice(-8).reverse()} columns={[["trade_date","成交日"],["symbol","股票"],["side","方向"],["quantity","数量"],["price","成交价"]]} format={{symbol:(v)=>names[text(v)]??"未知证券",price:num}}/></article>;
}

function CandidateSwitcher({ observation, selectedTag, onSelect }: { observation:Challenger["candidate_observation"]; selectedTag:string; onSelect:(tag:string)=>void }) {
  if (!observation) return null;
  return <div className="candidate-switcher"><b>第三列候选策略</b><select value={selectedTag} onChange={(event)=>onSelect(event.target.value)}>{(observation.candidates??[]).map((row)=><option key={row.candidate_tag} value={row.candidate_tag}>{row.candidate_tag} · {row.latest_trade_date?`${text(row.review_stage)} · ${text(row.common_observation_days)}日`:"等待首次收盘观察"}</option>)}</select><span>默认展示最近已有真实委托的候选；新模型在训练完成后的首个收盘流水线开始观察</span></div>;
}

function CandidateObservation({ challenger, selectedTag }: { challenger: Challenger; selectedTag:string }) {
  const observation = challenger.candidate_observation;
  if (!observation) return null;
  const selected = observation.candidates?.find((row)=>row.candidate_tag===selectedTag);
  const commonDays = Number(selected?.common_observation_days??observation.comparison?.common_observation_days??0);
  return <>
    <section className="kpis"><Kpi label="候选队列" value={String(observation.candidates?.length??0)} note="各版本独立账户持续运行"/><Kpi label="初评门槛" value="20共同日" note="候选与两个对照均有快照"/><Kpi label="正式评审" value="60共同日" note="达到后冻结证据等待人工评审"/><Kpi label="当前选择" value={selectedTag} note={`${commonDays} 个共同日`}/></section>
    <Panel title="每周候选模型排行榜" subtitle="离线测试按训练版本永久留档；BLOCKED 候选仍可进入隔离观察，但绝不会自动替换当前模型"><Table rows={observation.leaderboard} columns={[["candidate_tag","候选版本"],["data_cutoff","数据截止"],["rank_ic","Rank IC"],["rank_ic_ir","ICIR"],["cost_adjusted_top_k_excess_return","成本后Top-K超额"],["positive_seed_ratio","正种子率"],["average_turnover","换手率"],["research_gate_passed","研究门禁"],["observation_days","观察日"],["latest_status","观察状态"]]} format={{rank_ic:num,rank_ic_ir:num,cost_adjusted_top_k_excess_return:pct,positive_seed_ratio:pct,average_turnover:pct,research_gate_passed:(v)=>v?"PASS":"BLOCKED"}}/></Panel>
  </>;
}

function TradeCycleComparison({ challenger, names, candidateAccountId }: { challenger: Challenger; names: Record<string, string>; candidateAccountId?:string }) {
  const analysis = challenger.trade_cycle_analysis??{};
  const accounts = [
    { id:"multi_sector_shadow", label:"因子基线" },
    { id:"multi_sector_intraday_shadow", label:"因子基线·日内执行" },
    { id:"qlib_gru_shadow", label:"Qlib GRU 挑战者" },
    ...(candidateAccountId?[{id:candidateAccountId,label:"Qlib 候选者"}]:[]),
  ];
  const summaryRows = accounts.map(({id,label})=>({strategy:label,...(analysis[id]?.summary??{})}));
  return <><Panel title="完整持仓周期胜率与赔率" subtitle="从首次建仓到仓位归零算一个闭合周期；分批买卖合并，费用、滑点和分红计入；未清仓周期不进入胜率"><Table rows={summaryRows} columns={[["strategy","策略"],["closed_cycles","闭合周期"],["wins","盈利"],["losses","亏损"],["win_rate","胜率"],["payoff_ratio","赔率"],["profit_factor","盈亏因子"],["realized_pnl","累计已实现盈亏"],["average_return","平均周期收益"]]} format={{win_rate:pct,payoff_ratio:num,profit_factor:num,realized_pnl:money,average_return:pct}}/></Panel><div className="four-col">{accounts.map(({id,label})=><Panel key={id} title={`${label} · 个股持仓周期`} subtitle="每只股票所有已闭合周期聚合；当前开放周期单独标记"><Table rows={analysis[id]?.by_symbol??[]} columns={[["symbol","证券"],["closed_cycles","闭合"],["wins","赢"],["losses","亏"],["win_rate","胜率"],["payoff_ratio","赔率"],["profit_factor","盈亏因子"],["realized_pnl","已实现盈亏"],["open_cycle","持仓中"],["open_estimated_pnl","浮动盈亏"]]} format={{symbol:(v)=>security(v,names),win_rate:pct,payoff_ratio:num,profit_factor:num,realized_pnl:money,open_cycle:(v)=>v?"是":"否",open_estimated_pnl:money}}/></Panel>)}</div><div className="four-col">{accounts.map(({id,label})=><Panel key={id} title={`${label} · 已闭合周期明细`} subtitle="用于核验每次从建仓到清仓的真实结果"><Table rows={(analysis[id]?.closed_cycles??[]).slice(0,100)} columns={[["entry_date","建仓日"],["exit_date","清仓日"],["symbol","证券"],["pnl","净盈亏"],["return_rate","周期收益"],["dividend_income","分红"],["fees","费用"],["fill_count","成交笔数"],["result","结果"]]} format={{symbol:(v)=>security(v,names),pnl:money,return_rate:pct,dividend_income:money,fees:money}}/></Panel>)}</div></>;
}

function ChallengerEvidence({ challenger }: { challenger: Challenger }) {
  const commonDays = Number(challenger.comparison.common_observation_days??0);
  const commonStart = text(challenger.comparison.common_start_date);
  const commonEnd = text(challenger.comparison.common_end_date);
  const replayRows = (challenger.historical_execution.strategies??[]).map((row)=>({strategy_id:row.strategy_id,...(row.summary??{})}));
  const globalView=challenger.candidate_observation?.global_strategy_view??challenger.comparison;
  const strategyCount=Number(globalView.strategy_count??globalView.metrics?.length??0);
  return <><section className="kpis"><Kpi label="候选评审口径" value="版本独立" note="每个候选分别与两个固定对照计算"/><Kpi label="当前候选共同日" value={String(commonDays)} note="不受其他候选加入影响"/><Kpi label="当前共同区间" value={commonStart==="—"?"等待重叠数据":`${commonStart} → ${commonEnd}`} note="用于候选20/60日门禁"/><Kpi label="全局策略数量" value={String(strategyCount)} note="图表与全局表不隐藏历史策略"/></section><Panel title="M5.3 完整历史执行重放" subtitle={`${text(challenger.historical_execution.start_date)} → ${text(challenger.historical_execution.end_date)} · ${text(challenger.historical_execution.evidence_status)} · 与模拟盘共用 PaperBroker`}><Table rows={replayRows} columns={[["strategy_id","策略"],["observation_days","交易日"],["total_return","累计收益"],["average_gross_exposure","平均仓位"],["annualized_volatility","年化波动"],["sharpe","夏普"],["max_drawdown","最大回撤"],["order_count","订单"],["fill_count","成交"],["transaction_cost","交易成本"],["reconciled","对账"]]} format={{total_return:pct,average_gross_exposure:pct,annualized_volatility:pct,sharpe:num,max_drawdown:pct,transaction_cost:money}}/></Panel><div className="two-col"><Panel title="前瞻观察证据" subtitle="预测生成5个交易日后自动成熟标签"><Table rows={challenger.forward.daily??[]} columns={[["observation_date","观察日"],["maturity_date","成熟日"],["rank_ic","Rank IC"],["selected_excess_return","Top-K超额"],["sample_count","样本"]]} format={{rank_ic:num,selected_excess_return:pct}}/></Panel><Panel title={`${strategyCount}策略公平 PK`} subtitle="全局表展示每个策略自身完整运行区间；候选晋级仍使用各版本独立共同日"><Table rows={globalView.metrics??[]} columns={[["account_id","账户"],["start_date","开始日"],["end_date","结束日"],["observation_days","运行日"],["total_return","累计收益"],["annualized_volatility","年化波动"],["sharpe","夏普"],["max_drawdown","最大回撤"]]} format={{total_return:pct,annualized_volatility:pct,sharpe:num,max_drawdown:pct}}/></Panel></div></>;
}

const chartPalette: Record<string, { label: string; color: string }> = {
  "multi_sector_shadow": { label: "因子基线", color: "#102c24" },
  "multi_sector_intraday_shadow": { label: "因子基线·日内执行", color: "#168a83" },
  "qlib_gru_shadow": { label: "Qlib GRU 挑战者", color: "#ef7655" },
};
const candidateChartColors=["#3568c0","#8b5fbf","#168a83","#c14974","#a56a00","#537188","#8b6f47"];

function PerformanceComparisonCharts({ comparison }: { comparison: Challenger["comparison"] }) {
  const histories = comparison.independent_histories??comparison.histories??{};
  const series = Object.entries(histories).map(([accountId, rows],index) => ({
    accountId,
    label: chartPalette[accountId]?.label??(accountId.startsWith("qlib_candidate_")?`Qlib 候选者 ${accountId.replace("qlib_candidate_","")}`:accountId),
    color: chartPalette[accountId]?.color??candidateChartColors[index%candidateChartColors.length],
    rows: rows as Row[],
  })).filter((series)=>series.rows.length > 0);
  const [selectedAccountIds,setSelectedAccountIds]=useState<Set<string>>(()=>new Set());
  const toggleAccount=(accountId:string)=>setSelectedAccountIds((current)=>{
    if (!current.size) return new Set([accountId]);
    const next=new Set(current);
    if (next.has(accountId)) next.delete(accountId); else next.add(accountId);
    return next;
  });
  const showAll=!selectedAccountIds.size;
  return <><div className="strategy-focus" role="group" aria-label="选择需要高亮的策略"><span>高亮策略（可多选）</span><button className={showAll?"active":""} onClick={()=>setSelectedAccountIds(new Set())}>全部</button>{series.map((item)=><button key={item.accountId} aria-pressed={!showAll&&selectedAccountIds.has(item.accountId)} className={!showAll&&selectedAccountIds.has(item.accountId)?"active":""} onClick={()=>toggleAccount(item.accountId)}><i style={{background:item.color}}/>{item.label}</button>)}</div><div className="two-col"><LineComparisonChart title="累计权益走势" subtitle="各策略从自身首个运行日归一为 100；未运行区间留空" series={series} valueKey="normalized_nav" formatValue={(value)=>`${(value*100).toFixed(1)}`} selectedAccountIds={selectedAccountIds} onToggle={toggleAccount}/><LineComparisonChart title="每日收益率波动" subtitle="按各账户实际运行日净权益逐日计算；未运行区间留空，虚线为 0%" series={series} valueKey="daily_return" formatValue={(value)=>pct(value)} zeroLine selectedAccountIds={selectedAccountIds} onToggle={toggleAccount}/></div></>;
}

function LineComparisonChart({ title, subtitle, series, valueKey, formatValue, zeroLine=false, selectedAccountIds, onToggle }: { title: string; subtitle: string; series: { accountId: string; label: string; color: string; rows: Row[] }[]; valueKey: string; formatValue: (value: number)=>string; zeroLine?: boolean; selectedAccountIds:Set<string>; onToggle:(accountId:string)=>void }) {
  const width = 720, height = 238, left = 48, right = 18, top = 16, bottom = 33;
  const dates = [...new Set(series.flatMap((item)=>item.rows.map((row)=>text(row.trade_date))))].sort();
  const dateIndex = new Map(dates.map((date,index)=>[date,index]));
  const points = series.flatMap((item)=>item.rows.map((row)=>({ date:text(row.trade_date), value:Number(row[valueKey]) })).filter((point)=>Number.isFinite(point.value)));
  if (!points.length) return <Panel title={title} subtitle={subtitle}><p className="empty">等待账户产生实际运行日权益快照</p></Panel>;
  const count = Math.max(dates.length, 1);
  const rawMin = Math.min(...points.map((point)=>point.value), zeroLine ? 0 : Infinity);
  const rawMax = Math.max(...points.map((point)=>point.value), zeroLine ? 0 : -Infinity);
  const padding = Math.max((rawMax - rawMin) * 0.12, zeroLine ? 0.001 : 0.005);
  const min = rawMin - padding, max = rawMax + padding, span = Math.max(max - min, 0.000001);
  const x = (date: string) => left + (count <= 1 ? 0 : Number(dateIndex.get(date)??0) / (count - 1)) * (width - left - right);
  const y = (value: number) => top + (max - value) / span * (height - top - bottom);
  const path = (rows: Row[]) => rows.map((row, index)=>`${index ? "L" : "M"}${x(text(row.trade_date)).toFixed(1)},${y(Number(row[valueKey])).toFixed(1)}`).join(" ");
  const last = series.map((item)=>({ ...item, value: Number(item.rows.at(-1)?.[valueKey]??0) }));
  const ticks = [0, 0.5, 1];
  const showAll=!selectedAccountIds.size;
  const ordered=[...series].sort((a,b)=>Number(selectedAccountIds.has(a.accountId))-Number(selectedAccountIds.has(b.accountId)));
  return <Panel title={title} subtitle={subtitle}><div className="chart-legend">{last.map((item)=>{const focused=showAll||selectedAccountIds.has(item.accountId);return <button key={item.accountId} aria-pressed={!showAll&&selectedAccountIds.has(item.accountId)} className={focused?"active":"muted"} onClick={()=>onToggle(item.accountId)}><i style={{background:item.color}}/>{item.label} <b>{formatValue(item.value)}</b></button>})}</div><svg className="comparison-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title}>{ticks.map((tick)=><g key={tick}><line x1={left} x2={width-right} y1={top+tick*(height-top-bottom)} y2={top+tick*(height-top-bottom)} className="chart-grid"/><text x={left-8} y={top+tick*(height-top-bottom)+3} textAnchor="end">{formatValue(max-tick*(max-min))}</text></g>)}{zeroLine && min <= 0 && max >= 0 && <line x1={left} x2={width-right} y1={y(0)} y2={y(0)} className="chart-zero"/>}{ordered.map((item)=>{const focused=showAll||selectedAccountIds.has(item.accountId);const selected=selectedAccountIds.has(item.accountId);return <g key={item.accountId} opacity={focused?1:.12}><path d={path(item.rows)} fill="none" stroke={item.color} strokeWidth={selected?"4":"2.5"} strokeLinecap="round" strokeLinejoin="round"/>{selected&&item.rows.map((row)=><circle key={text(row.trade_date)} cx={x(text(row.trade_date))} cy={y(Number(row[valueKey]))} r="3.2" fill={item.color} stroke="#fff" strokeWidth="1.5"><title>{`${item.label} · ${text(row.trade_date)} · ${formatValue(Number(row[valueKey]))}`}</title></circle>)}</g>})}<text x={left} y={height-8}>{text(dates[0])}</text><text x={width-right} y={height-8} textAnchor="end">{text(dates.at(-1))}</text></svg></Panel>;
}

function PointInTimeEvidence({ challenger }: { challenger: Challenger }) {
  const gate = challenger.point_in_time.historical_replay_gate??{};
  return <><section className="kpis"><Kpi label="M5.4 时点存储" value={text(challenger.point_in_time.status)} note={`${text(challenger.point_in_time.feature_rows)} 条特征快照`}/><Kpi label="股票池门禁" value={text(gate.status)} note="成分必须在当时已被系统捕获" accent={text(gate.status)!=="PASS"}/><Kpi label="历史订单覆盖" value={pct(gate.coverage)} note={`${text(gate.eligible_orders)} / ${text(gate.tested_orders)}`}/><Kpi label="可信起点" value={text(gate.earliest_trustworthy_date)} note="此前收益只能作为诊断"/></section><Panel title="时点数据可用规则" subtitle={`数据指纹 ${text(challenger.point_in_time.source_fingerprint).slice(0,16)}…`}><Table rows={Object.entries((challenger.point_in_time.availability_rules??{}) as Row).map(([source,rule])=>({source,rule}))} columns={[["source","数据层"],["rule","可用时间规则"]]}/></Panel></>;
}

function GovernanceEvidence({ challenger }: { challenger: Challenger }) {
  return <><section className="kpis"><Kpi label="M5.5 部署状态" value={text(challenger.governance.lifecycle)} note={text(challenger.governance.release_id)}/><Kpi label="挑战者执行模式" value={text(challenger.governance.execution_mode)} note={text(challenger.governance.reason)} accent={text(challenger.governance.execution_mode)!=="PAPER_TRADING"}/><Kpi label="漂移状态" value={text(challenger.drift.status)} note={`${text(challenger.drift.matured_forward_days)} 个成熟前瞻日`} accent={text(challenger.drift.status)==="BREACH"}/><Kpi label="滚动 Rank IC" value={num(challenger.drift.rolling_rank_ic)} note={`换手 ${pct(challenger.drift.average_turnover)} · 成本率 ${pct(challenger.drift.cost_ratio)}`}/></section><div className="two-col"><Panel title="模型发布注册表" subtitle="候选模型不会自动替换当前挑战者"><Table rows={challenger.governance.releases??[]} columns={[["release_id","版本"],["model_id","模型"],["lifecycle","生命周期"],["data_cutoff","数据截止"],["artifact_hash","产物指纹"]]}/></Panel><Panel title="部署状态转换" subtitle="自动操作只能降级；晋级和恢复必须通过门禁"><Table rows={challenger.governance.transitions??[]} columns={[["created_at","时间"],["from_lifecycle","原状态"],["to_lifecycle","新状态"],["to_mode","执行模式"],["transition_type","类型"],["reason","原因"]]}/></Panel></div><Panel title="漂移监控" subtitle="滚动 IC、预测 PSI、特征 PSI、换手和成本共同决定是否降级"><Table rows={[challenger.drift]} columns={[["status","状态"],["observed_prediction_days","预测日"],["matured_forward_days","成熟日"],["rolling_rank_ic","滚动IC"],["score_psi","预测PSI"],["maximum_feature_psi","特征PSI"],["average_turnover","换手"],["cost_ratio","成本率"],["breaches","触发项"]]} format={{rolling_rank_ic:num,score_psi:num,maximum_feature_psi:num,average_turnover:pct,cost_ratio:pct}}/></Panel></>;
}

function LongTermReview({ challenger }: { challenger: Challenger }) {
  const review=challenger.long_term_review;
  const evaluation=review.evaluation??{};
  const checks=Object.entries(review.criteria??{}).map(([criterion,passed])=>({criterion,status:passed?"PASS":"WAIT"}));
  return <><Intro tag="M5.6 LONG-TERM REVIEW" title="20日只观察，60日后才允许提出晋级">评审只使用2026-07-30之后两个账户共有的可信交易日。Bootstrap置信区间、风险归一收益、回撤、成本、漂移和前瞻门禁必须同时通过；系统永远不会自动替换因子Champion。</Intro><section className="kpis"><Kpi label="评审状态" value={text(review.status)} note={text(review.decision_authority)} accent={text(review.status)!=="ELIGIBLE_FOR_MANUAL_PROMOTION_REVIEW"}/><Kpi label="可信共同日" value={text(evaluation.common_observation_days)} note={`初评 ${text(review.minimum_preliminary_days)} · 正式 ${text(review.minimum_formal_days)}`}/><Kpi label="年化配对超额" value={pct(evaluation.annualized_excess_return)} note={`CI ${pct(evaluation.annualized_excess_ci_low)} → ${pct(evaluation.annualized_excess_ci_high)}`}/><Kpi label="Deflated Sharpe置信度" value={pct(evaluation.deflated_sharpe_probability)} note={`${text(evaluation.trial_count)} 个模型试验修正`}/></section><div className="two-col"><Panel title="风险归一比较" subtitle="统一到10%年化波动后比较"><Table rows={[{account:"因子 Champion",total_return:evaluation.factor_total_return,risk_return:evaluation.factor_risk_normalized_return,volatility:evaluation.factor_annualized_volatility,max_drawdown:evaluation.factor_max_drawdown},{account:"Qlib Challenger",total_return:evaluation.qlib_total_return,risk_return:evaluation.qlib_risk_normalized_return,volatility:evaluation.qlib_annualized_volatility,max_drawdown:evaluation.qlib_max_drawdown}]} columns={[["account","账户"],["total_return","累计收益"],["risk_return","风险归一收益"],["volatility","年化波动"],["max_drawdown","最大回撤"]]} format={{total_return:pct,risk_return:pct,volatility:pct,max_drawdown:pct}}/></Panel><Panel title="正式晋级门禁" subtitle="全部通过也只获得人工评审资格"><Table rows={checks} columns={[["criterion","门禁"],["status","状态"]]}/></Panel></div></>;
}

function DataHealth({ quality }: { quality: DataQuality }) {
  const blocked = Number(quality.latest.blocking_count ?? 0);
  const warnings = Number(quality.latest.warning_count ?? 0);
  const affected = quality.checks.reduce((sum, row) => sum + Number(row.affected_count ?? 0), 0);
  return <><Intro tag="DATA QUALITY CENTER" title="坏数据不能变成订单">每日检查行情、复权、估值、财务、分红、ETF披露和研究产物。阻断级检查未通过时，综合账户不会撮合旧订单，也不会生成新订单。</Intro>
    <section className="kpis"><Kpi label="整体状态" value={text(quality.latest.status)} note={`数据日 ${text(quality.latest.trade_date)}`} accent={blocked>0}/><Kpi label="阻断项" value={String(blocked)} note="必须修复后才能生成订单" accent={blocked>0}/><Kpi label="警告项" value={String(warnings)} note="允许运行但需要跟踪"/><Kpi label="受影响记录" value={String(affected)} note={`${text(quality.latest.target_count)}只目标股票`}/></section>
    <Panel title="数据质量检查矩阵" subtitle="PASS / WARN / BLOCK 全部保留日度证据"><div className="quality-grid">{quality.checks.map((row)=><article key={text(row.code)} className={text(row.status).toLowerCase()}><header><b>{text(row.status)}</b><span>{text(row.category)}</span></header><h3>{text(row.code)}</h3><p>{text(row.message)}</p><small>{Number(row.affected_count)>0?`影响 ${text(row.affected_count)} 只：${text(row.affected_symbols)}`:"未发现异常"}</small></article>)}</div></Panel>
    <Panel title="最近数据健康记录" subtitle="用于观察连续失败和恢复"><Table rows={quality.history} columns={[["trade_date","交易日"],["status","状态"],["target_count","标的数"],["blocking_count","阻断"],["warning_count","警告"]]}/></Panel></>;
}

function Operations({ bank, execution, operations, acceptance, intraday, names, onRefresh }: { bank: Bank; execution: Execution; operations: OperationsCenter; acceptance: MonthlyAcceptance; intraday: IntradayExecution; names: Record<string, string>; onRefresh: () => Promise<void> }) {
  const recover = async () => { await json("/api/risk-state/recover", { method: "POST" }); await onRefresh(); };
  const acknowledge = async (id: number) => { await json(`/api/notifications/${id}/acknowledge`, { method: "POST" }); await onRefresh(); };
  const [previewDate, setPreviewDate] = useState(text(operations.preview.trade_date));
  const [preview, setPreview] = useState(operations.preview);
  const inspect = async () => setPreview(await json(`/api/tasks/daily-run/preview?trade_date=${previewDate}`));
  const firstTickEquity=Number(intraday.account_ticks[0]?.equity??0);
  const intradaySeries=intraday.account_ticks.map((row)=>({...row,trade_date:row.observed_at,normalized_nav:firstTickEquity?Number(row.equity)/firstTickEquity:1}));
  const openExecution=operations.open_execution??{};
  const accountLabel=(value:unknown)=>chartPalette[text(value)]?.label??(text(value).startsWith("qlib_candidate_")?`Qlib 候选者 ${text(value).replace("qlib_candidate_","")}`:text(value));
  return <><section className="ops-banner"><div><i/><small>SERVER SCHEDULER</small><h2>09:31 开盘撮合 · 18:30 收盘流水线</h2><p>开盘执行昨日计划；收盘后更新数据、计算信号并生成下一交易日计划</p></div><span>{bank.scheduler.enabled?"已启用":"已暂停"}</span></section>
    <Panel title="今日开盘执行" subtitle={`${text(openExecution.price_source)} · 纯基线、Qlib挑战者及全部候选者同步在开盘阶段生效`}><section className="kpis"><Kpi label="执行状态" value={text(openExecution.status??"WAITING_FOR_OPEN")} note={`${text(openExecution.trade_date)} · ${text(openExecution.scheduled_time??"09:31")}`}/><Kpi label="覆盖策略" value={String((openExecution.account_ids??[]).length)} note="独立账户、同一开盘价口径"/><Kpi label="今日成交" value={String((openExecution.fills??[]).length)} note="成交后立即进入持仓与权益" accent={Number((openExecution.fills??[]).length)>0}/><Kpi label="遗留待撮合" value={String((openExecution.pending_orders??[]).length)} note="缺行情或交易限制将继续重试" accent={Number((openExecution.pending_orders??[]).length)>0}/></section><Table rows={openExecution.accounts??[]} columns={[["account_id","策略账户"],["pending_before","开盘前委托"],["executions","执行事件"],["reconciliation","对账"]]} format={{account_id:accountLabel,reconciliation:(v)=>Boolean((v as Row)?.matched)?"一致":"异常"}}/><h4>今日开盘成交明细</h4><Table rows={openExecution.fills??[]} columns={[["account_id","策略"],["symbol","证券"],["side","方向"],["quantity","数量"],["price","成交价"],["fee","费用"]]} format={{account_id:accountLabel,symbol:(v)=>security(v,names),price:num,fee:money}}/></Panel>
    <Panel title="基线日内智能执行" subtitle={`${intraday.policy_id} · ${intraday.activation_date}起生效 · 仅改变成交时机，不改变日频目标`}><section className="kpis"><Kpi label="执行状态" value={text(intraday.latest.status??"等待交易时段")} note={text(intraday.latest.observed_at)}/><Kpi label="待处理委托" value={String(intraday.pending_orders.length)} note="基线综合账户" accent={intraday.pending_orders.length>0}/><Kpi label="本轮触发" value={String((intraday.latest.executions??[]).length)} note="VWAP / 价差 / 截止时间"/><Kpi label="本轮观察" value={String((intraday.latest.decisions??[]).length)} note="含持仓、可卖量和成本"/></section><Table rows={intraday.latest.decisions??[]} columns={[["symbol","证券"],["side","方向"],["quantity","数量"],["price","现价"],["vwap","VWAP"],["spread_bps","价差bps"],["action","动作"],["reason","原因"]]} format={{symbol:(v)=>security(v,names),price:num,vwap:num,spread_bps:num}}/></Panel>
    <div className="two-col"><LineComparisonChart title="日内实时权益变化" subtitle="以当日首个QMT快照归一为100，每分钟刷新" series={[{accountId:intraday.account_id,label:"基线·日内执行",color:"#168a83",rows:intradaySeries}]} valueKey="normalized_nav" formatValue={(value)=>`${(value*100).toFixed(3)}`} selectedAccountIds={new Set([intraday.account_id])} onToggle={()=>{}}/><Panel title="日内实时成交" subtitle="成交后立即进入独立账户账本"><Table rows={intraday.fills.slice(-30).reverse()} columns={[["trade_date","成交日"],["symbol","证券"],["side","方向"],["quantity","数量"],["price","成交价"],["fee","费用"]]} format={{symbol:(v)=>security(v,names),price:num,fee:money}}/></Panel></div>
    <section className="kpis"><Kpi label="综合账户权益" value={money(execution.portfolio?.equity)} note={execution.account_id}/><Kpi label="实际 / 可执行目标" value={`${pct(execution.metrics.gross_exposure)} / ${pct(execution.metrics.target_exposure)}`} note={`理论目标 ${pct(execution.metrics.theoretical_target_exposure??execution.metrics.target_exposure)} · 已考虑一手约束`}/><Kpi label="累计收益 / 回撤" value={`${pct((Number(execution.portfolio?.equity??1_000_000)/1_000_000)-1)} / ${pct(execution.metrics.drawdown)}`} note={`${execution.history.length}个日度快照`}/><Kpi label="账户风险状态" value={text(execution.risk_state.effective_state)} note={text(execution.risk_state.reason_code)} accent={text(execution.risk_state.effective_state)!=="NORMAL"}/></section>
    <Panel title="理论目标到可执行目标" subtitle="一手金额超过可分配预算的股票被剔除；释放预算优先在同一行业内重新分配"><Table rows={execution.target_adjustments??[]} columns={[["symbol","证券"],["sector","行业"],["theoretical_weight","理论权重"],["executable_weight","可执行权重"],["minimum_lot_weight","一手占账户"],["reason","调整原因"]]} format={{symbol:(v)=>security(v,names),sector:(v)=>meta[text(v)]?.label??text(v),theoretical_weight:pct,executable_weight:pct,minimum_lot_weight:pct}}/></Panel>
    <section className="kpis"><Kpi label="下次自动运行" value={duration(operations.scheduler.seconds_to_next_run)} note={text(operations.scheduler.next_run)}/><Kpi label="待执行订单" value={String(operations.pending_orders.length)} note={`${text(preview.eligible_for_execution_count)}笔在所选交易日可撮合`} accent={operations.pending_orders.length>0}/><Kpi label="延期执行记录" value={String(operations.deferred_attempts.length)} note="涨跌停、T+1、现金不足"/><Kpi label="补跑预览" value={text(preview.market_session)} note={`${text(preview.trade_date)} · 只读检查`}/></section>
    <Panel title="运行保障状态" subtitle="正式交易日历、服务端调度和后台线程必须在流水线启动前可恢复"><div className="acceptance-head"><b>{text(operations.readiness.status)}</b><span><strong>{text(operations.readiness.market_session)}</strong><small>{text(operations.readiness.trade_date)} · {text(operations.readiness.repair_count)}项待自动修复</small></span><em>{Number(operations.readiness.blocking_count)>0?"存在阻断":"允许启动预检"}</em></div><div className="quality-grid">{(operations.readiness.checks??[]).map((row)=><article key={text(row.code)} className={text(row.status)==="PASS"?"pass":"warn"}><header><b>{text(row.status)}</b><span>{text(row.code)}</span></header><p>{text(row.message)}</p></article>)}</div></Panel>
    <Panel title="完整月度换仓验收" subtitle={`${acceptance.cycle_id} · 最早完成估算 ${acceptance.expected_earliest_completion}`}><div className="acceptance-head"><b>{pct(acceptance.progress)}</b><span><strong>{acceptance.status}</strong><small>观察期 {acceptance.start_date} → {acceptance.observation_date}</small></span><em>{acceptance.report_path?"验收报告已冻结":"证据积累中"}</em></div><div className="quality-grid">{acceptance.checks.map((row)=><article key={text(row.code)} className={Boolean(row.passed)?"pass":"warn"}><header><b>{Boolean(row.passed)?"PASS":"WAIT"}</b><span>{text(row.code)}</span></header><h3>{text(row.observed)} / {text(row.required)}</h3><p>{text(row.explanation)}</p></article>)}</div></Panel>
    <div className="two-col"><Panel title="待执行订单" subtitle="显示信号日、方向、数量和进入下一撮合窗口的资格"><Table rows={operations.pending_orders.slice(0,30)} columns={[["signal_date","信号日"],["symbol","证券"],["side","方向"],["quantity","数量"],["status","状态"],["reason_code","策略原因"]]} format={{symbol:(v)=>security(v,names)}}/></Panel><Panel title="延期与拒绝原因" subtitle="保留每次执行尝试，不覆盖此前失败原因"><Table rows={operations.deferred_attempts.slice(0,30)} columns={[["trade_date","交易日"],["symbol","证券"],["side","方向"],["outcome","结果"],["reason_code","原因"],["quantity","数量"]]} format={{symbol:(v)=>security(v,names)}}/></Panel></div>
    <Panel title="手动补跑影响预览" subtitle="预览不会启动任务、写入数据或改变模拟持仓"><div className="preview-controls"><input value={previewDate} onChange={(event)=>setPreviewDate(event.target.value)} maxLength={8}/><button className="recover" onClick={()=>void inspect()}>检查影响</button><b>{text(preview.calendar_source)}</b></div><section className="kpis"><Kpi label="历史运行" value={text(preview.prior_run_count)} note="该交易日已有任务数"/><Kpi label="全部待执行" value={text(preview.pending_order_count)} note="综合影子账户"/><Kpi label="预计可撮合" value={text(preview.eligible_for_execution_count)} note={`${text(preview.eligible_buy_count)}买 / ${text(preview.eligible_sell_count)}卖`}/><Kpi label="最近影子账本" value={text(preview.latest_shadow_date)} note="补跑前基线"/></section><Table rows={preview.steps??[]} columns={[["step_name","步骤"],["action","补跑动作"]]}/>{(preview.expected_effects??[]).map((item)=><p key={item}>· {item}</p>)}{(preview.warnings??[]).map((item)=><p className="error" key={item}>{item}</p>)}</Panel>
    <Panel title="账户风险状态机" subtitle="风险自动升级；状态降级必须人工确认"><div className="risk-machine">{["NORMAL","REDUCE_ONLY","SELL_ONLY","SUSPENDED"].map((state)=><div key={state} className={execution.risk_state.effective_state===state?"active":""}><b>{state}</b><small>{state==="NORMAL"?"允许正常调仓":state==="REDUCE_ONLY"?"只允许降低风险":state==="SELL_ONLY"?"目标强制降为零":"冻结全部订单"}</small></div>)}</div>{Boolean(execution.risk_state.recovery_required)&&<button className="recover" onClick={()=>void recover()}>确认恢复到 {text(execution.risk_state.proposed_state)}</button>}<Table rows={execution.risk_transitions.slice(0,10)} columns={[["trade_date","交易日"],["from_state","原状态"],["to_state","生效状态"],["proposed_state","建议状态"],["reason_code","原因"],["transition_type","类型"]]}/></Panel>
    <Panel title="风险监控" subtitle="阻断级告警会停止生成新订单"><div className="risk-alerts">{execution.risk_alerts.length?execution.risk_alerts.map((row)=><div key={text(row.code)} className={text(row.severity).toLowerCase()}><b>{text(row.severity)}</b><span>{text(row.message)}</span><small>{text(row.code)}</small></div>):<p className="empty">当前无风险告警</p>}</div></Panel>
    <Panel title="可恢复步骤账本" subtitle="每个步骤独立重试；同一交易日已完成步骤在重跑时自动跳过"><Table rows={operations.task_steps.slice(0,30)} columns={[["run_id","运行"],["trade_date","交易日"],["step_name","步骤"],["attempt","尝试"],["status","状态"],["error","错误"]]}/></Panel>
    <div className="two-col"><Panel title="每日综合流水线"><div className="architecture compact">{["交易日校验","全市场数据同步","五行业因子截面","行业择时与风险预算","影子撮合","现金持仓对账"].map((name,index)=><div key={name}><b>0{index+1}</b><span><strong>{name}</strong><small>{index<4?"组合决策层":"执行审计层"}</small></span></div>)}</div></Panel><Panel title="最近任务"><Table rows={operations.task_runs.slice(0,15)} columns={[["trade_date","交易日"],["source","来源"],["status","状态"],["started_at","开始"],["finished_at","结束"],["error","错误"]]}/></Panel></div>
    <div className="two-col"><Panel title="行业收益与持仓归因" subtitle="当日贡献、实际仓位和浮动盈亏"><Table rows={execution.attribution} columns={[["sector","行业"],["daily_pnl","当日盈亏"],["daily_contribution","收益贡献"],["actual_weight","实际仓位"],["unrealized_pnl","浮动盈亏"]]} format={{sector:(v)=>meta[text(v)]?.label??text(v),daily_pnl:money,daily_contribution:pct,actual_weight:pct,unrealized_pnl:money}}/></Panel><Panel title="目标与实际仓位偏差" subtitle="解释未成交、整手和T+1差异"><Table rows={execution.deviations} columns={[["symbol","证券"],["target_weight","目标"],["actual_weight","实际"],["weight_gap","偏差"],["reason","原因"]]} format={{symbol:(v)=>security(v,names),target_weight:pct,actual_weight:pct,weight_gap:pct}}/></Panel></div>
    <Panel title="完整收益归因" subtitle={`账户盈亏 ${money(execution.attribution_reconciliation.actual_pnl)} · 已解释 ${money(execution.attribution_reconciliation.explained_pnl)} · 未解释 ${money(execution.attribution_reconciliation.unexplained_pnl)}`}><Table rows={execution.return_attribution} columns={[["component","贡献来源"],["sector","行业"],["pnl","盈亏"],["contribution","收益贡献"],["detail","口径"]]} format={{sector:(v)=>meta[text(v)]?.label??text(v),pnl:money,contribution:pct}}/></Panel>
    <div className="two-col"><Panel title="组合风险归因" subtitle="波动、相关性、集中度与边际风险"><Table rows={execution.risk_attribution} columns={[["symbol","证券"],["sector","行业"],["weight","权重"],["volatility_contribution","波动贡献"],["correlation_contribution","相关性贡献"],["concentration_contribution","集中度"],["marginal_risk","边际风险"]]} format={{symbol:(v)=>security(v,names),sector:(v)=>meta[text(v)]?.label??text(v),weight:pct,volatility_contribution:pct,correlation_contribution:pct,concentration_contribution:pct,marginal_risk:num}}/></Panel><Panel title="执行缺口归因" subtitle="整手、T+1、涨跌停、现金不足和其他执行约束"><Table rows={execution.execution_attribution} columns={[["reason","原因"],["symbol_count","证券数"],["absolute_weight_gap","绝对仓位缺口"],["signed_weight_gap","净缺口"],["execution_events","执行事件"]]} format={{absolute_weight_gap:pct,signed_weight_gap:pct}}/></Panel></div>
    <Panel title="当前综合持仓" subtitle="股票名、代码、实际数量和成本"><Table rows={execution.positions} columns={[["symbol","证券"],["quantity","数量"],["available_quantity","可用"],["avg_cost","成本"]]} format={{symbol:(v)=>security(v,names),avg_cost:num}}/></Panel>
    <Panel title="公司行为入账" subtitle="现金分红、送转股和权益登记均采用幂等审计账本"><Table rows={execution.corporate_action_ledger} columns={[["trade_date","入账日"],["symbol","证券"],["action_type","类型"],["entitled_quantity","登记股数"],["cash_amount","现金"],["share_quantity","新增股数"]]} format={{symbol:(v)=>security(v,names),cash_amount:money}}/></Panel>
    <Panel title="最近委托与成交" subtitle="五行业目标统一进入影子账户，真实委托保持隔离"><Table rows={execution.orders.slice(0,30)} columns={[["signal_date","信号日"],["symbol","证券"],["side","方向"],["quantity","数量"],["status","状态"],["reason_code","原因"]]} format={{symbol:(v)=>security(v,names)}}/></Panel>
    <Panel title="任务告警中心" subtitle="流水线失败、数据阻断、风控暂停和延迟订单会在这里留痕"><div className="risk-alerts">{operations.notifications.length?operations.notifications.map((row)=><div key={row.id} className={text(row.severity).toLowerCase()}><b>{text(row.severity)}</b><span><strong>{text(row.title)}</strong><br/>{text(row.message)}</span><small>{text(row.trade_date)} · {text(row.code)} {!row.acknowledged&&<button className="recover" onClick={()=>void acknowledge(row.id)}>确认</button>}</small></div>):<p className="empty">当前无任务告警</p>}</div></Panel></>;
}

function Allocation({rows}:{rows:Row[]}){return <div className="allocation">{rows.map((row)=>{const m=sectorMeta(row.sector);return <div key={text(row.sector)}><div className="allocation-name"><i style={{background:m.color}}/><span><b>{m.label}</b><small>{m.thesis}</small></span></div><div className="bars"><span><i style={{width:`${Number(row.target_weight)*100}%`,background:`${m.color}55`}}/></span><span><i style={{width:`${Number(row.target_weight)*100}%`,background:m.color}}/></span></div><div className="values"><b>{pct(row.target_weight)}</b><small>全局选股后的行业暴露 · 非预算约束</small></div></div>})}</div>}
function SectorCards({rows,names}:{rows:Row[];names:Record<string,string>}){return <div className="sector-cards">{rows.map((row)=>{const m=sectorMeta(row.sector);return <article key={text(row.sector)} style={{"--sector":m.color} as React.CSSProperties}><header><span>{m.label}</span><b>{pct(row.target_weight)}</b></header><p>{m.thesis}</p><div>{text(row.selected).split(",").map((s)=><small key={s}><b>{names[s]??"未知证券"}</b><span>{s}</span></small>)}</div><footer><span>实际目标暴露 {pct(row.target_weight)}</span><span>仅展示汇总</span></footer></article>})}</div>}
function UniverseCard({universe,names}:{universe:Universe;names:Record<string,string>}){const m=sectorMeta(universe.sector);const selectedCount=universe.ranking.filter((row)=>Boolean(row.selected)).length;return <article className="universe-card" style={{"--sector":m.color} as React.CSSProperties}><header><div><span>{m.label}</span><h3>{universe.name}</h3><small>{universe.fund_code} · {universe.style}</small></div><b>{selectedCount}<small>目标持仓</small></b></header><div className="factor-chips">{Object.entries(universe.factor_weights).map(([f,w])=><span key={f}>{factorLabels[f]??f}<b>{pct(w)}</b></span>)}</div><Table rows={universe.ranking.slice(0,universe.sector==="bank"?12:10)} columns={[["rank","排名"],["symbol","证券"],["score","模型分"],["selected","目标"]]} format={{symbol:(v)=>security(v,names),score:num,selected:(v)=>v?"持有":"—"}}/></article>}
function Evidence({sectors}:{sectors:Sectors}){return <div className="evidence"><b>证据边界</b><p>{sectors.warning}</p><span>{sectors.evidence_status}</span></div>}
function Intro({tag,title,children}:{tag:string;title:string;children:ReactNode}){return <section className="intro"><span>{tag}</span><h2>{title}</h2><p>{children}</p></section>}
function Panel({title,subtitle,children}:{title:string;subtitle?:string;children:ReactNode}){return <section className="panel"><header><div><h3>{title}</h3>{subtitle&&<p>{subtitle}</p>}</div><span>•••</span></header>{children}</section>}
function Kpi({label,value,note,accent}:{label:string;value:string;note:string;accent?:boolean}){return <div className={`kpi ${accent?"accent":""}`}><small>{label}</small><b>{value}</b><span>{note}</span></div>}
function Table({rows,columns,format={}}:{rows:Row[];columns:[string,string][];format?:Record<string,(v:unknown)=>string>}){if(!rows.length)return <p className="empty">暂无数据</p>;return <div className="table-wrap"><table><thead><tr>{columns.map(([,l])=><th key={l}>{l}</th>)}</tr></thead><tbody>{rows.map((row,i)=><tr key={i}>{columns.map(([k])=><td key={k}>{format[k]?format[k](row[k]):text(row[k])}</td>)}</tr>)}</tbody></table></div>}
