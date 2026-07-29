# Qlib 深度学习挑战账户

## 目标

在不修改 `multi_sector_shadow` 因子账户及其 M4.14 前瞻证据的前提下，
建立独立的 Qlib 深度学习模拟账户 `qlib_gru_shadow`，用同口径数据、
标签、样本切分、Top-K、费用、滑点和 T+1 撮合规则进行对照。

## 首版协议

- 股票池：银行及四类 ETF 的成分股研究池，共 79 只；
- 特征：6 个量价序列特征 × 60 个交易日；
- 标签：未来 5 日收益减同行业同期平均收益；
- 训练集：2015-01-01 至 2023-12-31；
- 验证集：2024-01-01 至 2024-12-31；
- 测试集：2025-01-01 至 2026-07-26；
- 前瞻期：2026-07-27 起；
- 基准模型：Qlib LightGBM；
- 挑战模型：Qlib GRU，CUDA 训练；
- 账户初始资金：1,000,000 元；
- 目标总仓位：40%，每行业最多选择 2 只。

## 启用门槛

GRU 必须同时满足以下条件，挑战账户才允许生成模拟订单：

- 测试样本不少于 10,000；
- 日度横截面 Rank IC 不低于 0.02；
- Rank ICIR 不低于 0.10。

任一条件不满足时，状态为 `RESEARCH_GATE_FAILED`，账户保持全现金。
挑战者失败只产生独立告警，不会使原因子账户的每日流水线失败。

## 首轮结果

数据截止日为 2026-07-21，测试样本 29,492 条：

| 模型 | Rank IC | Rank ICIR | Top-K 日均超额 |
|---|---:|---:|---:|
| Qlib LightGBM | 0.00236 | 0.0165 | 0.0787% |
| Qlib GRU | 0.01175 | 0.0905 | 0.1211% |

GRU 优于同特征树模型，但未达到启用门槛。因此首轮账户已创建并完成对账，
当前权益 1,000,000 元、持仓 0、订单 0。

## 运行

```powershell
.\.venv\Scripts\python.exe scripts\research_qlib_challenger.py
.\.venv\Scripts\python.exe scripts\run_qlib_challenger_daily.py --trade-date 20260729
```

服务端每日流水线会在原因子账户完成后运行
`qlib_challenger_execution`。研究和账户数据可通过
`/api/qlib-challenger` 及前台“Qlib挑战者”页面查看。
