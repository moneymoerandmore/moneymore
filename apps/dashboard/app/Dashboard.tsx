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
  symbol_names: Record<string, string>;
};
type Execution = {
  account_id: string; status: string; trade_date?: string;
  target_weights: Record<string, number>; symbol_sectors: Record<string, string>;
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
};
type ModelRegistry = { versions: Row[]; artifacts: Row[]; bindings: Row[] };
type MonthlyAcceptance = {
  cycle_id: string; start_date: string; observation_date: string; status: string;
  progress: number; expected_earliest_completion: string; checks: Row[];
  evidence: Row; report_path?: string | null;
};
type Challenger = {
  account_id: string; research: Row & { metrics?: Row[] };
  forward: Row & { daily?: Row[] };
  comparison: Row & { metrics?: Row[]; histories?: Record<string, Row[]> };
  latest: Row & { selected?: string[]; scores?: Row[]; portfolio?: Bank["shadow"]["portfolio"] };
  orders: Row[]; fills: Row[]; reconciliation: Row;
  baseline: { account_id: string; latest: Row };
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
  growth: { label: "创业板成长", color: "#53bfb0", thesis: "营收增长 · ROE · 动量" },
};
const factorLabels: Record<string, string> = {
  momentum_252_21: "12-1月动量", return_120: "120日收益", quarterly_revenue_growth: "季度营收增长",
  roe: "ROE", volatility_60: "60日低波", earnings_yield: "盈利收益率", book_to_price: "账面市值比",
  drawdown_120: "120日回撤", dividend_yield_ttm: "股息率", price_to_ma_60: "60日趋势",
};
const pct = (v: unknown) => `${(Number(v ?? 0) * 100).toFixed(1)}%`;
const num = (v: unknown) => Number(v ?? 0).toFixed(2);
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
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => {
    try {
      const [b, s, e, q, o, m, a, c] = await Promise.all([
        json<Bank>("/api/bank-dashboard"), json<Sectors>("/api/sector-portfolio"), json<Execution>("/api/multi-sector-execution"), json<DataQuality>("/api/data-quality"), json<OperationsCenter>("/api/operations-center"), json<ModelRegistry>("/api/model-registry"), json<MonthlyAcceptance>("/api/monthly-acceptance"), json<Challenger>("/api/qlib-challenger"),
      ]);
      setBank(b); setSectors(s); setExecution(e); setQuality(q); setOperations(o); setModels(m); setAcceptance(a); setChallenger(c); setError("");
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
  if (!bank || !sectors || !execution || !quality || !operations || !models || !acceptance || !challenger) return <main className="loading"><span>M</span><b>MoneyMore</b><p>{error || "正在装载综合组合…"}</p><button onClick={() => void refresh()}>重新连接</button></main>;
  const current = nav.find((item) => item.id === page)!;
  return <div className="app-shell">
    <aside>
      <div className="brand"><span>M</span><div><b>MoneyMore</b><small>MULTI-SECTOR QUANT</small></div></div>
      <div className="system-card"><i/><small>ACTIVE SYSTEM</small><b>多行业动态组合</b><span>5 行业袖套 · 79 只研究标的</span></div>
      <nav>{nav.map((item, index) => <button key={item.id} className={page === item.id ? "active" : ""} onClick={() => setPage(item.id)}><em>0{index + 1}</em><span>{item.label}<small>{item.note}</small></span></button>)}</nav>
      <div className="aside-foot"><i/> PAPER ONLY<br/><small>{bank.scheduler.time} · {bank.scheduler.timezone}</small></div>
    </aside>
    <main className="workspace">
      <header><div><small>MONEYMORE / {current.note}</small><h1>{current.label}</h1></div><div className="header-actions"><span><i/> 数据日 {sectors.latest_date}</span><button onClick={() => void refresh()}>刷新</button><button className="primary" disabled={busy} onClick={() => void run()}>{busy ? "运行中…" : "运行今日流水线"}</button></div></header>
      {error && <div className="error">{error}</div>}
      {page === "overview" && <Overview sectors={sectors} execution={execution}/>}
      {page === "sectors" && <SectorPage bank={bank} sectors={sectors}/>}
      {page === "research" && <Research sectors={sectors} models={models}/>}
      {page === "challenger" && <><ChallengerPage challenger={challenger} names={sectors.symbol_names}/><ChallengerEvidence challenger={challenger}/></>}
      {page === "data" && <DataHealth quality={quality}/>}
      {page === "operations" && <Operations bank={bank} execution={execution} operations={operations} acceptance={acceptance} names={sectors.symbol_names} onRefresh={refresh}/>}
    </main>
  </div>;
}

function Overview({ sectors, execution }: { sectors: Sectors; execution: Execution }) {
  const invested = sectors.allocation.reduce((sum, row) => sum + Number(row.target_weight), 0);
  const selected = sectors.allocation.reduce((sum, row) => sum + text(row.selected).split(",").filter(Boolean).length, 0);
  return <>
    <section className="portfolio-hero"><div><span>COMBINED ALLOCATION</span><h2>一个账户，五套行业逻辑</h2><p>先在行业内部选股与择时，再跨行业分配风险。银行是综合组合中的防御袖套，不再是页面主体。</p><div className="hero-tags"><b>动态仓位</b><b>行业差异化</b><b>月度换仓</b><b>T+1 影子执行</b></div></div><div className="exposure"><small>当前股票总仓位</small><b>{pct(invested)}</b><div><i style={{width:`${invested*100}%`}}/></div><span>现金 {pct(1-invested)} · 候选 {selected} 只</span></div></section>
    <section className="kpis"><Kpi label="行业袖套" value="5" note="银行 / 红利 / 有色 / 芯片 / 成长"/><Kpi label="股票目标仓位" value={pct(invested)} note="择时后实际风险暴露" accent/><Kpi label="现金缓冲" value={pct(1-invested)} note="未使用风险预算"/><Kpi label="综合影子账户" value={money(execution.portfolio?.equity)} note={`${execution.positions.length} 个已成交持仓 · ${execution.status}`}/></section>
    <div className="two-col wide"><Panel title="整体配置全貌" subtitle="淡色为风险预算，实色为择时后的账户目标仓位"><Allocation rows={sectors.allocation}/></Panel><Panel title="今日组合决策" subtitle="从风险预算到可执行目标"><div className="decision"><strong>保持防御，分批建立风险仓位</strong><p>银行与红利承担主要配置；芯片和成长维持观察仓，有色保留周期暴露。</p>{sectors.allocation.map((row)=><div key={text(row.sector)}><i style={{background:meta[text(row.sector)].color}}/><span>{meta[text(row.sector)].label}</span><b>{pct(row.target_weight)}</b><small>{text(row.selected).split(",").length}只</small></div>)}</div></Panel></div>
    <Panel title="行业目标与候选股票" subtitle={`ETF 持仓来源披露日 ${sectors.disclosure_date}`}><SectorCards rows={sectors.allocation} names={sectors.symbol_names}/></Panel>
    <Evidence sectors={sectors}/>
  </>;
}

function SectorPage({ bank, sectors }: { bank: Bank; sectors: Sectors }) {
  const bankAllocation = sectors.allocation.find((row) => row.sector === "bank");
  const bankSelected = new Set(text(bankAllocation?.selected).split(",").filter(Boolean));
  const bankUniverse: Universe = { sector:"bank", name:"A股银行多因子池", fund_code:"BANK_CN", style:"value_defensive_momentum", factor_weights:{value:.471,defensive:.294,momentum:.235}, ranking:bank.latest_scores.map((row,index)=>({...row,rank:index+1,selected:bankSelected.has(text(row.symbol))})) };
  return <><Intro tag="SECTOR PLAYBOOKS" title="不同产业，使用不同的胜率来源">不再用同一套均线评价所有股票。每个行业拥有独立因子权重、Top-K 缓冲和风险目标。</Intro><div className="sector-grid">{[bankUniverse,...sectors.universes].map((u)=><UniverseCard key={u.sector} universe={u} names={sectors.symbol_names}/>)}</div></>;
}

function Research({ sectors, models }: { sectors: Sectors; models: ModelRegistry }) {
  const reports=sectors.report.filter((row)=>row.period==="sample_out");
  return <><Intro tag="RESEARCH GOVERNANCE" title="回测是诊断，不是收益承诺">每个行业模型独立评估，跨行业只负责风险预算；ETF 历史持仓缺失，因此行业回看统一降级标记。</Intro>
    <section className="kpis"><Kpi label="研究标的" value="79" note="39 银行 + 40 ETF权重股"/><Kpi label="行业模型" value="5" note="独立因子权重与择时"/><Kpi label="ETF证据状态" value="有偏诊断" note="不可视作纯样本外" accent/><Kpi label="前瞻起点" value="2026-07-27" note="此后才是新证据"/></section>
    <Panel title="分行业历史诊断" subtitle="只用于比较风险特征，不作为建仓理由"><Table rows={reports} columns={[["sector","行业"],["cagr","年化"],["volatility","波动率"],["sharpe","夏普"],["max_drawdown","最大回撤"],["fills","成交数"]]} format={{sector:(v)=>meta[text(v)]?.label??text(v),cagr:pct,volatility:pct,sharpe:num,max_drawdown:pct}}/></Panel>
    <Panel title="整体配置架构" subtitle="系统当前采用的分层决策结构"><div className="architecture">{[["01","数据层","行情、估值、财务、分红、ETF披露"],["02","行业模型","五套因子模型，行业内排序"],["03","组合模型","Top-K、缓冲退出、替换约束"],["04","择时模型","波动率目标决定风险度"],["05","总账户","逆波动预算与行业边界"],["06","执行层","T+1撮合、成本与对账"]].map(([n,t,d])=><div key={n}><b>{n}</b><span><strong>{t}</strong><small>{d}</small></span></div>)}</div></Panel>
    <Panel title="模型版本注册表" subtitle="模型版本绑定代码、配置、股票池和数据截止日；任一内容改变都会产生新版本"><Table rows={models.versions} columns={[["version_id","版本"],["lifecycle","生命周期"],["evidence_stage","证据阶段"],["data_cutoff","数据截止"],["universe_hash","股票池指纹"],["code_hash","代码指纹"]]}/></Panel>
    <div className="two-col"><Panel title="证据隔离" subtitle="历史诊断不能自动升级为前瞻证据"><Table rows={models.artifacts.slice(0,20)} columns={[["artifact_type","产物"],["evidence_stage","证据阶段"],["artifact_key","定位"]]}/></Panel><Panel title="信号与订单绑定" subtitle="每个信号和订单都可反查具体模型版本"><Table rows={models.bindings.slice(0,20)} columns={[["trade_date","交易日"],["binding_type","类型"],["symbol","证券"],["version_id","模型版本"]]}/></Panel></div><Evidence sectors={sectors}/></>;
}

function ChallengerPage({ challenger, names }: { challenger: Challenger; names: Record<string, string> }) {
  const metrics = challenger.research.metrics??[];
  const challengerEquity = Number(challenger.latest.portfolio?.equity??1_000_000);
  const baselinePortfolio = challenger.baseline.latest.portfolio as Row|undefined;
  const baselineEquity = Number(baselinePortfolio?.equity??1_000_000);
  return <><Intro tag="QLIB CHALLENGER LAB" title="深度学习只能通过公平竞赛晋级">挑战者使用独立资金、模型、信号、订单和持仓。因子影子账户保持冻结；GPU只加速训练，不改变样本外和成本后晋级标准。</Intro>
    <section className="kpis"><Kpi label="挑战者状态" value={text(challenger.latest.status)} note={challenger.account_id} accent={text(challenger.latest.status)!=="COMPLETED"}/><Kpi label="GPU训练" value={Boolean(challenger.research.cuda_available)?"CUDA":"未启用"} note={text(challenger.research.cuda_device)}/><Kpi label="挑战者权益" value={money(challengerEquity)} note={`累计 ${pct(challengerEquity/1_000_000-1)}`}/><Kpi label="因子基线权益" value={money(baselineEquity)} note={`累计 ${pct(baselineEquity/1_000_000-1)}`}/></section>
    <Panel title="统一样本外模型竞赛" subtitle="相同股票池、标签、训练切分和Top-K规则"><Table rows={metrics} columns={[["model_id","模型"],["segment","区间"],["samples","样本"],["rank_ic","Rank IC"],["rank_ic_ir","Rank ICIR"],["top_k_excess_return","Top-K超额"]]} format={{rank_ic:num,rank_ic_ir:num,top_k_excess_return:pct}}/></Panel>
    <div className="two-col"><Panel title="GRU最新排名" subtitle="每行业Top-2进入独立挑战者账户"><Table rows={(challenger.latest.scores??[]).slice(0,30)} columns={[["instrument","证券"],["sector","行业"],["score","预测分数"]]} format={{instrument:(v)=>security(v,names),sector:(v)=>meta[text(v)]?.label??text(v),score:num}}/></Panel><Panel title="挑战者当前持仓" subtitle="与正式因子影子账户完全隔离"><Table rows={challenger.latest.portfolio?.positions??[]} columns={[["symbol","证券"],["quantity","数量"],["available_quantity","可用"],["avg_cost","成本"]]} format={{symbol:(v)=>security(v,names),avg_cost:num}}/></Panel></div>
    <Panel title="挑战者订单与成交" subtitle="仍采用T+1开盘撮合、费用、滑点和对账规则"><Table rows={challenger.orders.slice(0,30)} columns={[["signal_date","信号日"],["symbol","证券"],["side","方向"],["quantity","数量"],["status","状态"],["reason_code","原因"]]} format={{symbol:(v)=>security(v,names)}}/></Panel>
    <div className="evidence"><b>隔离边界</b><p>挑战者结果不进入M4.14正式影子验收；只有完成独立样本外、随机种子稳定性和前瞻模拟后，才允许提出模型晋级。</p><span>CHALLENGER_ONLY</span></div>
  </>;
}

function ChallengerEvidence({ challenger }: { challenger: Challenger }) {
  return <div className="two-col"><Panel title="前瞻观察证据" subtitle="预测生成5个交易日后自动成熟标签"><Table rows={challenger.forward.daily??[]} columns={[["observation_date","观察日"],["maturity_date","成熟日"],["rank_ic","Rank IC"],["selected_excess_return","Top-K超额"],["sample_count","样本"]]} format={{rank_ic:num,selected_excess_return:pct}}/></Panel><Panel title="双策略同口径比较" subtitle="至少20个共同模拟交易日后才形成比较结论"><Table rows={challenger.comparison.metrics??[]} columns={[["account_id","账户"],["observation_days","观察日"],["total_return","累计收益"],["annualized_volatility","年化波动"],["sharpe","夏普"],["max_drawdown","最大回撤"]]} format={{total_return:pct,annualized_volatility:pct,sharpe:num,max_drawdown:pct}}/></Panel></div>;
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

function Operations({ bank, execution, operations, acceptance, names, onRefresh }: { bank: Bank; execution: Execution; operations: OperationsCenter; acceptance: MonthlyAcceptance; names: Record<string, string>; onRefresh: () => Promise<void> }) {
  const recover = async () => { await json("/api/risk-state/recover", { method: "POST" }); await onRefresh(); };
  const acknowledge = async (id: number) => { await json(`/api/notifications/${id}/acknowledge`, { method: "POST" }); await onRefresh(); };
  const [previewDate, setPreviewDate] = useState(text(operations.preview.trade_date));
  const [preview, setPreview] = useState(operations.preview);
  const inspect = async () => setPreview(await json(`/api/tasks/daily-run/preview?trade_date=${previewDate}`));
  return <><section className="ops-banner"><div><i/><small>SERVER SCHEDULER</small><h2>每日 {bank.scheduler.time}</h2><p>服务端持续运行 · Asia/Shanghai · 非 Codex 自动任务</p></div><span>{bank.scheduler.enabled?"已启用":"已暂停"}</span></section>
    <section className="kpis"><Kpi label="综合账户权益" value={money(execution.portfolio?.equity)} note={execution.account_id}/><Kpi label="实际 / 目标仓位" value={`${pct(execution.metrics.gross_exposure)} / ${pct(execution.metrics.target_exposure)}`} note="收盘实际与模型目标"/><Kpi label="累计收益 / 回撤" value={`${pct((Number(execution.portfolio?.equity??1_000_000)/1_000_000)-1)} / ${pct(execution.metrics.drawdown)}`} note={`${execution.history.length}个日度快照`}/><Kpi label="账户风险状态" value={text(execution.risk_state.effective_state)} note={text(execution.risk_state.reason_code)} accent={text(execution.risk_state.effective_state)!=="NORMAL"}/></section>
    <section className="kpis"><Kpi label="下次自动运行" value={duration(operations.scheduler.seconds_to_next_run)} note={text(operations.scheduler.next_run)}/><Kpi label="待执行订单" value={String(operations.pending_orders.length)} note={`${text(preview.eligible_for_execution_count)}笔在所选交易日可撮合`} accent={operations.pending_orders.length>0}/><Kpi label="延期执行记录" value={String(operations.deferred_attempts.length)} note="涨跌停、T+1、现金不足"/><Kpi label="补跑预览" value={text(preview.market_session)} note={`${text(preview.trade_date)} · 只读检查`}/></section>
    <Panel title="运行保障状态" subtitle="正式交易日历、服务端调度和后台线程必须在流水线启动前可恢复"><div className="acceptance-head"><b>{text(operations.readiness.status)}</b><span><strong>{text(operations.readiness.market_session)}</strong><small>{text(operations.readiness.trade_date)} · {text(operations.readiness.repair_count)}项待自动修复</small></span><em>{Number(operations.readiness.blocking_count)>0?"存在阻断":"允许启动预检"}</em></div><div className="quality-grid">{(operations.readiness.checks??[]).map((row)=><article key={text(row.code)} className={text(row.status)==="PASS"?"pass":"warn"}><header><b>{text(row.status)}</b><span>{text(row.code)}</span></header><p>{text(row.message)}</p></article>)}</div></Panel>
    <Panel title="完整月度换仓验收" subtitle={`${acceptance.cycle_id} · 最早完成估算 ${acceptance.expected_earliest_completion}`}><div className="acceptance-head"><b>{pct(acceptance.progress)}</b><span><strong>{acceptance.status}</strong><small>观察期 {acceptance.start_date} → {acceptance.observation_date}</small></span><em>{acceptance.report_path?"验收报告已冻结":"证据积累中"}</em></div><div className="quality-grid">{acceptance.checks.map((row)=><article key={text(row.code)} className={Boolean(row.passed)?"pass":"warn"}><header><b>{Boolean(row.passed)?"PASS":"WAIT"}</b><span>{text(row.code)}</span></header><h3>{text(row.observed)} / {text(row.required)}</h3><p>{text(row.explanation)}</p></article>)}</div></Panel>
    <div className="two-col"><Panel title="待执行订单" subtitle="显示信号日、方向、数量和进入下一撮合窗口的资格"><Table rows={operations.pending_orders.slice(0,30)} columns={[["signal_date","信号日"],["symbol","证券"],["side","方向"],["quantity","数量"],["status","状态"],["reason_code","策略原因"]]} format={{symbol:(v)=>security(v,names)}}/></Panel><Panel title="延期与拒绝原因" subtitle="保留每次执行尝试，不覆盖此前失败原因"><Table rows={operations.deferred_attempts.slice(0,30)} columns={[["trade_date","交易日"],["symbol","证券"],["side","方向"],["outcome","结果"],["reason_code","原因"],["quantity","数量"]]} format={{symbol:(v)=>security(v,names)}}/></Panel></div>
    <Panel title="手动补跑影响预览" subtitle="预览不会启动任务、写入数据或改变模拟持仓"><div className="preview-controls"><input value={previewDate} onChange={(event)=>setPreviewDate(event.target.value)} maxLength={8}/><button className="recover" onClick={()=>void inspect()}>检查影响</button><b>{text(preview.calendar_source)}</b></div><section className="kpis"><Kpi label="历史运行" value={text(preview.prior_run_count)} note="该交易日已有任务数"/><Kpi label="全部待执行" value={text(preview.pending_order_count)} note="综合影子账户"/><Kpi label="预计可撮合" value={text(preview.eligible_for_execution_count)} note={`${text(preview.eligible_buy_count)}买 / ${text(preview.eligible_sell_count)}卖`}/><Kpi label="最近影子账本" value={text(preview.latest_shadow_date)} note="补跑前基线"/></section><Table rows={preview.steps??[]} columns={[["step_name","步骤"],["action","补跑动作"]]}/>{(preview.expected_effects??[]).map((item)=><p key={item}>· {item}</p>)}{(preview.warnings??[]).map((item)=><p className="error" key={item}>{item}</p>)}</Panel>
    <Panel title="账户风险状态机" subtitle="风险自动升级；状态降级必须人工确认"><div className="risk-machine">{["NORMAL","REDUCE_ONLY","SELL_ONLY","SUSPENDED"].map((state)=><div key={state} className={execution.risk_state.effective_state===state?"active":""}><b>{state}</b><small>{state==="NORMAL"?"允许正常调仓":state==="REDUCE_ONLY"?"只允许降低风险":state==="SELL_ONLY"?"目标强制降为零":"冻结全部订单"}</small></div>)}</div>{Boolean(execution.risk_state.recovery_required)&&<button className="recover" onClick={()=>void recover()}>确认恢复到 {text(execution.risk_state.proposed_state)}</button>}<Table rows={execution.risk_transitions.slice(0,10)} columns={[["trade_date","交易日"],["from_state","原状态"],["to_state","生效状态"],["proposed_state","建议状态"],["reason_code","原因"],["transition_type","类型"]]}/></Panel>
    <Panel title="风险监控" subtitle="阻断级告警会停止生成新订单"><div className="risk-alerts">{execution.risk_alerts.length?execution.risk_alerts.map((row)=><div key={text(row.code)} className={text(row.severity).toLowerCase()}><b>{text(row.severity)}</b><span>{text(row.message)}</span><small>{text(row.code)}</small></div>):<p className="empty">当前无风险告警</p>}</div></Panel>
    <Panel title="任务告警中心" subtitle="流水线失败、数据阻断、风控暂停和延迟订单会在这里留痕"><div className="risk-alerts">{operations.notifications.length?operations.notifications.map((row)=><div key={row.id} className={text(row.severity).toLowerCase()}><b>{text(row.severity)}</b><span><strong>{text(row.title)}</strong><br/>{text(row.message)}</span><small>{text(row.trade_date)} · {text(row.code)} {!row.acknowledged&&<button className="recover" onClick={()=>void acknowledge(row.id)}>确认</button>}</small></div>):<p className="empty">当前无任务告警</p>}</div></Panel>
    <Panel title="可恢复步骤账本" subtitle="每个步骤独立重试；同一交易日已完成步骤在重跑时自动跳过"><Table rows={operations.task_steps.slice(0,30)} columns={[["run_id","运行"],["trade_date","交易日"],["step_name","步骤"],["attempt","尝试"],["status","状态"],["error","错误"]]}/></Panel>
    <div className="two-col"><Panel title="每日综合流水线"><div className="architecture compact">{["交易日校验","全市场数据同步","五行业因子截面","行业择时与风险预算","影子撮合","现金持仓对账"].map((name,index)=><div key={name}><b>0{index+1}</b><span><strong>{name}</strong><small>{index<4?"组合决策层":"执行审计层"}</small></span></div>)}</div></Panel><Panel title="最近任务"><Table rows={operations.task_runs.slice(0,15)} columns={[["trade_date","交易日"],["source","来源"],["status","状态"],["started_at","开始"],["finished_at","结束"],["error","错误"]]}/></Panel></div>
    <div className="two-col"><Panel title="行业收益与持仓归因" subtitle="当日贡献、实际仓位和浮动盈亏"><Table rows={execution.attribution} columns={[["sector","行业"],["daily_pnl","当日盈亏"],["daily_contribution","收益贡献"],["actual_weight","实际仓位"],["unrealized_pnl","浮动盈亏"]]} format={{sector:(v)=>meta[text(v)]?.label??text(v),daily_pnl:money,daily_contribution:pct,actual_weight:pct,unrealized_pnl:money}}/></Panel><Panel title="目标与实际仓位偏差" subtitle="解释未成交、整手和T+1差异"><Table rows={execution.deviations} columns={[["symbol","证券"],["target_weight","目标"],["actual_weight","实际"],["weight_gap","偏差"],["reason","原因"]]} format={{symbol:(v)=>security(v,names),target_weight:pct,actual_weight:pct,weight_gap:pct}}/></Panel></div>
    <Panel title="完整收益归因" subtitle={`账户盈亏 ${money(execution.attribution_reconciliation.actual_pnl)} · 已解释 ${money(execution.attribution_reconciliation.explained_pnl)} · 未解释 ${money(execution.attribution_reconciliation.unexplained_pnl)}`}><Table rows={execution.return_attribution} columns={[["component","贡献来源"],["sector","行业"],["pnl","盈亏"],["contribution","收益贡献"],["detail","口径"]]} format={{sector:(v)=>meta[text(v)]?.label??text(v),pnl:money,contribution:pct}}/></Panel>
    <div className="two-col"><Panel title="组合风险归因" subtitle="波动、相关性、集中度与边际风险"><Table rows={execution.risk_attribution} columns={[["symbol","证券"],["sector","行业"],["weight","权重"],["volatility_contribution","波动贡献"],["correlation_contribution","相关性贡献"],["concentration_contribution","集中度"],["marginal_risk","边际风险"]]} format={{symbol:(v)=>security(v,names),sector:(v)=>meta[text(v)]?.label??text(v),weight:pct,volatility_contribution:pct,correlation_contribution:pct,concentration_contribution:pct,marginal_risk:num}}/></Panel><Panel title="执行缺口归因" subtitle="整手、T+1、涨跌停、现金不足和其他执行约束"><Table rows={execution.execution_attribution} columns={[["reason","原因"],["symbol_count","证券数"],["absolute_weight_gap","绝对仓位缺口"],["signed_weight_gap","净缺口"],["execution_events","执行事件"]]} format={{absolute_weight_gap:pct,signed_weight_gap:pct}}/></Panel></div>
    <Panel title="当前综合持仓" subtitle="股票名、代码、实际数量和成本"><Table rows={execution.positions} columns={[["symbol","证券"],["quantity","数量"],["available_quantity","可用"],["avg_cost","成本"]]} format={{symbol:(v)=>security(v,names),avg_cost:num}}/></Panel>
    <Panel title="公司行为入账" subtitle="现金分红、送转股和权益登记均采用幂等审计账本"><Table rows={execution.corporate_action_ledger} columns={[["trade_date","入账日"],["symbol","证券"],["action_type","类型"],["entitled_quantity","登记股数"],["cash_amount","现金"],["share_quantity","新增股数"]]} format={{symbol:(v)=>security(v,names),cash_amount:money}}/></Panel>
    <Panel title="最近委托与成交" subtitle="五行业目标统一进入影子账户，真实委托保持隔离"><Table rows={execution.orders.slice(0,30)} columns={[["signal_date","信号日"],["symbol","证券"],["side","方向"],["quantity","数量"],["status","状态"],["reason_code","原因"]]} format={{symbol:(v)=>security(v,names)}}/></Panel></>;
}

function Allocation({rows}:{rows:Row[]}){return <div className="allocation">{rows.map((row)=>{const m=meta[text(row.sector)];return <div key={text(row.sector)}><div className="allocation-name"><i style={{background:m.color}}/><span><b>{m.label}</b><small>{m.thesis}</small></span></div><div className="bars"><span><i style={{width:`${Number(row.budget_weight)*100}%`,background:`${m.color}55`}}/></span><span><i style={{width:`${Number(row.target_weight)*100}%`,background:m.color}}/></span></div><div className="values"><b>{pct(row.target_weight)}</b><small>预算 {pct(row.budget_weight)} · risk {pct(row.risk_degree)}</small></div></div>})}</div>}
function SectorCards({rows,names}:{rows:Row[];names:Record<string,string>}){return <div className="sector-cards">{rows.map((row)=>{const m=meta[text(row.sector)];return <article key={text(row.sector)} style={{"--sector":m.color} as React.CSSProperties}><header><span>{m.label}</span><b>{pct(row.target_weight)}</b></header><p>{m.thesis}</p><div>{text(row.selected).split(",").map((s)=><small key={s}><b>{names[s]??"未知证券"}</b><span>{s}</span></small>)}</div><footer><span>预算 {pct(row.budget_weight)}</span><span>现金 {pct(row.cash_weight)}</span></footer></article>})}</div>}
function UniverseCard({universe,names}:{universe:Universe;names:Record<string,string>}){const m=meta[universe.sector];const selectedCount=universe.ranking.filter((row)=>Boolean(row.selected)).length;return <article className="universe-card" style={{"--sector":m.color} as React.CSSProperties}><header><div><span>{m.label}</span><h3>{universe.name}</h3><small>{universe.fund_code} · {universe.style}</small></div><b>{selectedCount}<small>目标持仓</small></b></header><div className="factor-chips">{Object.entries(universe.factor_weights).map(([f,w])=><span key={f}>{factorLabels[f]??f}<b>{pct(w)}</b></span>)}</div><Table rows={universe.ranking.slice(0,universe.sector==="bank"?12:10)} columns={[["rank","排名"],["symbol","证券"],["score","模型分"],["selected","目标"]]} format={{symbol:(v)=>security(v,names),score:num,selected:(v)=>v?"持有":"—"}}/></article>}
function Evidence({sectors}:{sectors:Sectors}){return <div className="evidence"><b>证据边界</b><p>{sectors.warning}</p><span>{sectors.evidence_status}</span></div>}
function Intro({tag,title,children}:{tag:string;title:string;children:ReactNode}){return <section className="intro"><span>{tag}</span><h2>{title}</h2><p>{children}</p></section>}
function Panel({title,subtitle,children}:{title:string;subtitle?:string;children:ReactNode}){return <section className="panel"><header><div><h3>{title}</h3>{subtitle&&<p>{subtitle}</p>}</div><span>•••</span></header>{children}</section>}
function Kpi({label,value,note,accent}:{label:string;value:string;note:string;accent?:boolean}){return <div className={`kpi ${accent?"accent":""}`}><small>{label}</small><b>{value}</b><span>{note}</span></div>}
function Table({rows,columns,format={}}:{rows:Row[];columns:[string,string][];format?:Record<string,(v:unknown)=>string>}){if(!rows.length)return <p className="empty">暂无数据</p>;return <div className="table-wrap"><table><thead><tr>{columns.map(([,l])=><th key={l}>{l}</th>)}</tr></thead><tbody>{rows.map((row,i)=><tr key={i}>{columns.map(([k])=><td key={k}>{format[k]?format[k](row[k]):text(row[k])}</td>)}</tr>)}</tbody></table></div>}
