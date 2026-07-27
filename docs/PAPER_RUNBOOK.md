# 招商银行策略 PaperBroker 运行手册

## 安全边界

- 当前系统只写本地 SQLite 和 JSON，不包含华泰、QMT 或任何真实下单连接。
- 固定策略 `cmb_ma_120_250_v1`，目标仓位只能为 0% 或 10%。
- 同一策略、证券、信号日期只能记录一次；重复运行返回 `DUPLICATE_DECISION`。
- 信号 JSON 不可变，同名文件内容不同会直接报错。

## 每日收盘后流程

1. 等待 Tushare 收盘数据完成更新。
2. 增量同步当天股票日线和复权因子。
3. 检查同步输出不存在空表或质量错误。
4. 用实际纸面账户现金、权益和持仓运行信号。
5. 人工检查 JSON 输出，不执行真实委托。

示例：

```powershell
.\.venv\Scripts\python.exe -m moneymore.cli sync-tushare `
  --asset stocks --start 20260727 --end 20260727

.\.venv\Scripts\python.exe -m moneymore.cli paper-signal `
  --symbol 600036.SH --as-of 20260727 `
  --cash 1000000 --equity 1000000 --position 0
```

## 输出解释

- `ENTER/TREND_CONFIRMED`：趋势首次满足，生成调整到10%仓位的纸面买单。
- `HOLD/TREND_REMAINS_ACTIVE`：继续保持10%目标，根据当前纸面仓位决定是否需要调整。
- `EXIT/TREND_BROKEN`：趋势失效，生成纸面卖单。
- `STAY_CASH/TREND_INACTIVE`：目标0%，无持仓时返回 `NO_ACTION`。
- `INSUFFICIENT_HISTORY`：少于250根历史K线，强制空仓。

## 必须停止的情况

- 最新数据日期不是预期交易日；
- 信号文件与同日既有文件冲突；
- 目标仓位超过10%；
- 现金、权益、持仓或参考价格异常；
- 出现重复但内容不同的订单；
- 数据质量检查、SQLite或文件写入失败。

PaperBroker 数据位于 `state/`，被 Git 忽略。备份时应同时保存数据库与信号目录。
# 纸面交易撮合与对账

当前执行层只操作本地 SQLite 纸面账户，不连接华泰 MiniQMT，也不会发出真实委托。

## 每日流程

首次创建账户（重复执行不会重置资金）：

```powershell
.venv\Scripts\moneymore.exe paper-init --cash 1000000
```

收盘后生成信号。启用 `--account-state` 后，风险模块从纸面账本读取现金、
权益和持仓，不再依赖手工输入：

```powershell
.venv\Scripts\moneymore.exe paper-signal --symbol 600036.SH --as-of 20260724 --account-state
```

下一交易日获得日线后，用未复权开盘价撮合待处理订单：

```powershell
.venv\Scripts\moneymore.exe paper-execute --symbol 600036.SH --trade-date 20260727
```

撮合完成后执行独立对账：

```powershell
.venv\Scripts\moneymore.exe paper-reconcile
```

只有 `matched=true` 才表示三项同时一致：

1. 初始资金加全部成交现金流等于账户现金；
2. 全部买卖成交的净数量等于持仓数量；
3. `FILLED` 订单与成交记录一一对应，无缺失或孤儿成交。

## 撮合约束

- 信号日不成交，只允许后续交易日按开盘价加滑点撮合；
- 买入涨停、卖出跌停时保持 `PENDING`，后续交易日重试；
- 买入当日数量不可卖，T+1 不满足时延迟订单；
- 佣金、最低佣金、印花税、过户费与回测共用同一配置；
- 现金或持仓不足属于永久拒单，避免账本出现负数；
- 同一幂等键只能产生一笔订单和一笔成交。

华泰 MiniQMT 获批后，订单意图、风险检查和对账接口保持不变，只替换实际
委托与成交回报适配器。

## 自动日终流水线

日线数据通常需要等待数据商完成收盘处理。建议 Windows 任务计划程序在每个
自然日 18:30 启动入口脚本，脚本内部会查询交易日历，休市日自动跳过：

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File D:\Users\cloud\Documents\MoneyMore\scripts\run_daily.ps1
```

手工补跑指定交易日：

```powershell
.\scripts\run_daily.ps1 -TradeDate 20260724
```

单次流水线严格按以下顺序执行：

1. 查询交易日历，确认是否开市；
2. 增量同步全市场日线与复权因子，并执行数据质量门禁；
3. 同步招商银行当日涨跌停价格和停牌状态；
4. 用当日未复权开盘价撮合此前的 `PENDING` 订单；
5. 用当日收盘数据生成新信号，新订单留待下一交易日；
6. 独立重建资金、持仓和成交关系并对账；
7. 向 `state/daily-runs/` 写入独立 JSON 审计报告。

任何数据为空、日期错位、不可变快照冲突或对账失败都会令进程返回非零退出码。
任务计划程序应配置为失败时重试并保留运行历史。不要在行情尚未完成更新时把
空数据当作正常结果。
