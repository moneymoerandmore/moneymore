"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";

type Row = Record<string, unknown>;
type Page = "overview" | "sectors" | "research" | "operations";
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
};

const nav: { id: Page; label: string; note: string }[] = [
  { id: "overview", label: "组合总览", note: "PORTFOLIO" },
  { id: "sectors", label: "行业与个股", note: "SLEEVES" },
  { id: "research", label: "策略研究", note: "MODELS" },
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
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => {
    try {
      const [b, s, e] = await Promise.all([
        json<Bank>("/api/bank-dashboard"), json<Sectors>("/api/sector-portfolio"), json<Execution>("/api/multi-sector-execution"),
      ]);
      setBank(b); setSectors(s); setExecution(e); setError("");
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
  if (!bank || !sectors || !execution) return <main className="loading"><span>M</span><b>MoneyMore</b><p>{error || "正在装载综合组合…"}</p><button onClick={() => void refresh()}>重新连接</button></main>;
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
      {page === "research" && <Research sectors={sectors}/>}
      {page === "operations" && <Operations bank={bank} execution={execution} names={sectors.symbol_names}/>}
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

function Research({ sectors }: { sectors: Sectors }) {
  const reports=sectors.report.filter((row)=>row.period==="sample_out");
  return <><Intro tag="RESEARCH GOVERNANCE" title="回测是诊断，不是收益承诺">每个行业模型独立评估，跨行业只负责风险预算；ETF 历史持仓缺失，因此行业回看统一降级标记。</Intro>
    <section className="kpis"><Kpi label="研究标的" value="79" note="39 银行 + 40 ETF权重股"/><Kpi label="行业模型" value="5" note="独立因子权重与择时"/><Kpi label="ETF证据状态" value="有偏诊断" note="不可视作纯样本外" accent/><Kpi label="前瞻起点" value="2026-07-27" note="此后才是新证据"/></section>
    <Panel title="分行业历史诊断" subtitle="只用于比较风险特征，不作为建仓理由"><Table rows={reports} columns={[["sector","行业"],["cagr","年化"],["volatility","波动率"],["sharpe","夏普"],["max_drawdown","最大回撤"],["fills","成交数"]]} format={{sector:(v)=>meta[text(v)]?.label??text(v),cagr:pct,volatility:pct,sharpe:num,max_drawdown:pct}}/></Panel>
    <Panel title="整体配置架构" subtitle="系统当前采用的分层决策结构"><div className="architecture">{[["01","数据层","行情、估值、财务、分红、ETF披露"],["02","行业模型","五套因子模型，行业内排序"],["03","组合模型","Top-K、缓冲退出、替换约束"],["04","择时模型","波动率目标决定风险度"],["05","总账户","逆波动预算与行业边界"],["06","执行层","T+1撮合、成本与对账"]].map(([n,t,d])=><div key={n}><b>{n}</b><span><strong>{t}</strong><small>{d}</small></span></div>)}</div></Panel><Evidence sectors={sectors}/></>;
}

function Operations({ bank, execution, names }: { bank: Bank; execution: Execution; names: Record<string, string> }) {
  return <><section className="ops-banner"><div><i/><small>SERVER SCHEDULER</small><h2>每日 {bank.scheduler.time}</h2><p>服务端持续运行 · Asia/Shanghai · 非 Codex 自动任务</p></div><span>{bank.scheduler.enabled?"已启用":"已暂停"}</span></section>
    <section className="kpis"><Kpi label="综合账户权益" value={money(execution.portfolio?.equity)} note={execution.account_id}/><Kpi label="可用现金" value={money(execution.portfolio?.cash)} note="收盘盯市"/><Kpi label="累计成交" value={String(execution.fills.length)} note="五行业统一账本"/><Kpi label="对账状态" value={execution.reconciliation.matched?"一致":"待检查"} note={`现金差 ${money(execution.reconciliation.cash_difference)}`} accent={!execution.reconciliation.matched}/></section>
    <div className="two-col"><Panel title="每日综合流水线"><div className="architecture compact">{["交易日校验","全市场数据同步","五行业因子截面","行业择时与风险预算","影子撮合","现金持仓对账"].map((name,index)=><div key={name}><b>0{index+1}</b><span><strong>{name}</strong><small>{index<4?"组合决策层":"执行审计层"}</small></span></div>)}</div></Panel><Panel title="最近任务"><div className="runs">{bank.recent_runs.map((r)=><div key={r.id}><i className={r.status.toLowerCase()}/><b>{r.trade_date}</b><span>{r.source}</span><em>{r.status}</em></div>)}</div></Panel></div>
    <div className="two-col"><Panel title="行业实际持仓归因" subtitle="按当前市值汇总综合账户"><Table rows={execution.attribution} columns={[["sector","行业"],["actual_weight","实际仓位"],["market_value","市值"],["unrealized_pnl","浮动盈亏"]]} format={{sector:(v)=>meta[text(v)]?.label??text(v),actual_weight:pct,market_value:money,unrealized_pnl:money}}/></Panel><Panel title="当前综合持仓" subtitle="股票名、代码和实际数量"><Table rows={execution.positions} columns={[["symbol","证券"],["quantity","数量"],["available_quantity","可用"],["avg_cost","成本"]]} format={{symbol:(v)=>security(v,names),avg_cost:num}}/></Panel></div>
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
