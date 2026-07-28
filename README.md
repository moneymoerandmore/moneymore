# MoneyMore

MoneyMore 是一个面向个人投资者的A股量化研究与模拟交易系统。当前主策略是银行多因子Top-K组合，并采用Qlib式动态 `risk_degree` 完成组合择时。

当前模式：`PAPER_ONLY`。系统不连接任何真实券商账户，也不会自动发送真实委托。

## 当前能力

- Tushare历史行情、估值、财务和分红数据；
- 原始数据不可变快照与Parquet研究表；
- point-in-time财务特征；
- 21因子注册表和横截面预处理；
- 因子Rank IC、ICIR和分层收益分析；
- 银行股历史股票池；
- 银行多因子预测分数；
- Qlib式Top-K缓冲换仓；
- 固定风险度、KAMA和波动率目标择时；
- Qlib `risk_degree` 语义的动态组合仓位；
- T日信号、T+1开盘模拟撮合；
- 订单、成交、持仓、现金和对账账本；
- 每日18:30服务端自动流水线；
- 组合研究与运行观测前台。

详细路线见[规划文档](docs/PLAN.md)。

## 策略结构

```text
Tushare点时数据
      ↓
MoneyMore因子注册表
      ↓
横截面预处理与多因子分数
      ↓
Qlib式Top-K策略
Top 8 / Rank 12缓冲 / 最多替换2只
      ↓
Qlib risk_degree择时
固定80% / KAMA / 12%波动率目标
      ↓
目标权重与风险约束
      ↓
PaperBroker次日开盘模拟成交
      ↓
订单、成交、持仓和现金对账
```

Qlib在本项目中是研究和策略接口的主体规范。MoneyMore当前使用轻量原生实现对齐Qlib的信号、Top-K和 `risk_degree` 语义，以保留A股交易规则和本地数据控制。后续接入原生Qlib模型训练器时，不需要修改模拟撮合和前台接口。

## 当前银行策略

模型：`bank_multifactor_no_quality_candidate`

因子组：

- 价值：47.0588%
- 防御：29.4118%
- 动量：23.5294%
- 质量：0%

组合规则：

- 当前银行股票池39只；
- 选择Top 8；
- 持仓跌出Rank 12才触发正常退出；
- 每期最多替换2只；
- 每只股票基础权重为银行子账户10%；
- 固定基准总风险度80%。

当前择时：`vol_target_12`

- 60日实现波动率；
- 年化目标波动率12%；
- `risk_degree`范围20%～80%；
- 10个百分点为调整台阶；
- 每20个交易日重新评估；
- 状态为 `PROSPECTIVE_CANDIDATE`。

最终单股目标权重计算：

```text
单股目标权重 = 10% × risk_degree ÷ 80%
```

例如 `risk_degree=70%`：

- 银行子账户总持股目标约70%；
- 8只股票每只约8.75%；
- 银行子账户约30%保留现金；
- 如果银行预算占总账户10%，则总账户银行持股约7%。

## 环境要求

- Windows；
- Python 3.11或更高版本；
- Node.js仅用于前台；
- Tushare Token；
- 建议16GB内存和SSD。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

复制环境变量文件：

```powershell
Copy-Item .env.example .env
```

在本地 `.env` 中填写：

```text
TUSHARE_TOKEN=你的Token
```

不得提交 `.env`、Token、行情数据、运行状态或本地账本。

## 数据同步

同步股票数据：

```powershell
.\.venv\Scripts\python.exe -m moneymore.cli sync-tushare `
  --asset stocks `
  --start 20150101 `
  --end 20260727 `
  --batch-days 200
```

数据目录：

```text
data/raw        Tushare不可变原始快照
data/processed  去重后的研究表和研究产物
```

ETF需要独立权限。当前ETF权限尚未开通，不应使用降级或网页抓取方式伪造ETF数据。

## 运行银行择时研究

```powershell
.\.venv\Scripts\python.exe scripts\research_bank_timing.py
```

该命令使用完全相同的Top-K、交易费用和T+1成交规则，对比：

- `fixed_80`
- `kama_10_2_30`
- `vol_target_12`

输出：

```text
data/processed/bank_timing_report.parquet
data/processed/bank_timing_degrees.parquet
data/processed/bank_timing_equity.parquet
```

历史结果已参与模型开发，只能作为验证证据。2026-07-27之后的模拟记录才属于新前瞻证据。

## 启动组合控制台

```powershell
.\scripts\start_dashboard.ps1
```

访问：

- 前台：[http://127.0.0.1:3000](http://127.0.0.1:3000)
- API健康检查：[http://127.0.0.1:8788/api/health](http://127.0.0.1:8788/api/health)
- 银行组合数据：[http://127.0.0.1:8788/api/bank-dashboard](http://127.0.0.1:8788/api/bank-dashboard)
- 择时研究数据：[http://127.0.0.1:8788/api/bank-timing](http://127.0.0.1:8788/api/bank-timing)
- 模拟成交数据：[http://127.0.0.1:8788/api/bank-execution](http://127.0.0.1:8788/api/bank-execution)

停止服务：

```powershell
.\scripts\stop_dashboard.ps1
```

服务只监听本机回环地址，不对公网开放。

## 每日流水线

服务端每天18:30（Asia/Shanghai）自动运行，不依赖Codex自动任务。

流水线执行：

1. 交易日检查；
2. 行情和基本数据增量入库；
3. 前一日委托开盘模拟撮合；
4. 因子和银行多因子分数计算；
5. Top-K缓冲换仓；
6. 动态 `risk_degree` 计算；
7. 下一交易日目标委托生成；
8. 订单、成交、持仓和现金对账；
9. 运行证据归档。

流水线报告：

```text
state/bank-shadow/YYYYMMDD.json
```

信号和报告均按日期固化，避免同一交易日被静默覆盖。

## 测试

后端：

```powershell
.\.venv\Scripts\ruff.exe check src tests scripts\research_bank_timing.py
.\.venv\Scripts\pytest.exe -q
```

前端：

```powershell
cd apps\dashboard
pnpm run lint
pnpm test
```

## 重要口径

- Top-K是交易策略：决定何时调仓、买谁、卖谁和换多少；
- `risk_degree`是组合择时：决定银行子账户投入多少风险资金；
- 因子分数不等于买入建议；
- 历史最优不等于前瞻有效；
- KAMA在本银行组合中表现较差，不能因算法成熟而直接采用；
- 波动率目标只是前瞻候选，不是已证明的最优策略；
- 页面中的持仓、订单和收益均为模拟结果；
- 项目不提供收益保证或个股投资建议。

## 项目边界

当前阶段不做：

- 真实券商账户连接；
- 程序化实盘下单；
- 高频或日内交易；
- 未授权网页行情抓取；
- 用未来披露数据回填历史；
- 用同一历史区间反复搜索并宣称得到纯样本外结论。

后续工作以持续模拟、模型治理、组合归因和前瞻证据积累为主。

## 多行业统一影子账户

当前前台主账户为 `multi_sector_shadow`。银行、红利、工业有色、芯片和创业板成长的目标仓位会统一拆分为最终股票权重，并进入同一个模拟资金、订单、成交、持仓和现金账本。

```text
五行业研究快照
→ 行业风险预算与择时仓位
→ 最终单股目标权重
→ T+1 开盘影子撮合
→ 综合对账与行业归因
```

原 `bank_shadow` 继续保留，用作银行策略的独立基线，不再代表前台综合账户。

综合执行接口：

- [http://127.0.0.1:8788/api/multi-sector-execution](http://127.0.0.1:8788/api/multi-sector-execution)
