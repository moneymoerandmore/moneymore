# MoneyMore

面向个人投资者的 A 股量化交易系统。当前版本先把最容易被忽略、但决定结果是否可信的部分做对：

- 信号在收盘后生成，下一交易日开盘成交，避免未来函数；
- A 股买入按 100 股整数手、卖出收印花税；
- 支持佣金最低收费、双边过户费、滑点；
- 停牌、涨跌停无法成交；
- 组合级仓位上限、单标的上限和回撤熔断；
- 数据、策略、回测、执行适配器分层，实盘默认锁死。

## 快速开始

需要 Python 3.11+：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m pytest
python -m moneymore.cli demo
```

`demo` 使用确定性的合成行情，只用于验证系统链路，不代表策略收益。

## Tushare 历史数据

1. 在 Tushare 官网取得 Token。
2. 复制 `.env.example` 为 `.env`，只在本机填写 `TUSHARE_TOKEN`。
3. 首次先同步一个较短区间验证权限：

```powershell
Copy-Item .env.example .env
# 用文本编辑器在 .env 中填写 Token
.\.venv\Scripts\python.exe -m moneymore.cli sync-tushare --start 20250101 --end 20250131
```

原始响应按数据源、表和日期不可变保存到 `data/raw`，并生成包含抓取时间、行数和 SHA-256
的清单；去重后的研究表写入 `data/processed`。真实 Token、数据文件和日志均被 Git 忽略。

默认同步 A 股。ETF 使用独立接口和表，避免与股票行情混淆：

```powershell
.\.venv\Scripts\python.exe -m moneymore.cli sync-tushare --asset etfs --start 20250101 --end 20250131
```

按 Tushare 当前官方说明，股票基础信息、交易日历和股票复权因子通常需要至少 2000
积分；ETF 日线需要至少 5000 积分，新的 ETF 基础信息接口需要 8000 积分。以账户实际
权限为准。权限不足不做降级伪造，命令会直接失败。

用本地真实数据验证回测链路：

```powershell
.\.venv\Scripts\python.exe -m moneymore.cli backtest-stock --symbol 000001.SZ --fast 3 --slow 5
```

执行价格使用当时未复权开盘价，均线信号使用独立复权因子构造的调整价格。短周期参数仅供
链路验收，不能作为实盘策略。

多年历史回填建议使用较大批次；命令支持断点续传，并会从原始快照修复尚未合并的研究表：

```powershell
.\.venv\Scripts\python.exe -m moneymore.cli sync-tushare `
  --asset stocks `
  --start 20150101 `
  --end 20260724 `
  --batch-days 200
```

招商银行 M3 研究：

```powershell
.\.venv\Scripts\python.exe -m moneymore.cli sync-research-reference `
  --symbol 600036.SH --start 20150101 --end 20260724
.\.venv\Scripts\python.exe -m moneymore.cli research-stock --symbol 600036.SH
```

PaperBroker 每日信号演练：

```powershell
.\.venv\Scripts\python.exe -m moneymore.cli paper-signal `
  --symbol 600036.SH --as-of 20260724 `
  --cash 1000000 --equity 1000000 --position 0
```

详见 `docs/PAPER_RUNBOOK.md`。当前实现没有任何真实券商连接。

## 可视化量化控制台

控制台把数据状态、M3 信号、纸面账户、任务运行和三方对账集中到本机页面。
服务端进程内置每日 18:30 调度，不依赖 Codex 自动任务：

```powershell
.\scripts\start_dashboard.ps1
```

打开 `http://127.0.0.1:3000`。停止服务：

```powershell
.\scripts\stop_dashboard.ps1
```

服务只监听本机回环地址，Tushare Token、Parquet 行情和 SQLite 账本不会上传。

## 项目边界

当前里程碑是 M1：可靠的日频研究/回测内核。真实数据接入、点时财务数据、模拟撮合和券商
适配器将按 `docs/PLAN.md` 逐阶段实现。任何真实自动委托前，先向开户券商完成程序化交易报告，
确认所用接口、部署环境及账户权限符合券商要求。
