# MoneyMore factor system

MoneyMore borrows Qlib's separation of data, features, models, portfolio
construction and execution, while keeping a small native runtime that can use
Tushare now and Huatai QMT later.

## Contract

Every registered factor declares:

- stable name and explicit version;
- category and whether high or low values are preferred;
- required input fields and minimum lookback;
- point-in-time availability rule;
- deterministic calculation function;
- optional research reference.

The registry is intentionally strict. Unknown factors, missing inputs, duplicate
names and incorrectly-sized outputs fail before a backtest can run. Infinite
outputs are normalized to missing values, not silently capped.

## Point-in-time rules

- Market and valuation inputs use the previous available trading observation.
- Financial indicators become available after `ann_date`, never at report
  `end_date`.
- Adjusted prices are used for research signals and returns. Raw exchange prices
  remain the execution prices.
- Availability metadata does not replace the data loader's lagging
  responsibility; both are retained so audits can verify the contract.

## Initial catalog

The first catalog contains 21 factors in seven groups:

- trend: adjusted price versus MA20/60/120/250;
- momentum: return over 20/60/120 days and 252-to-21-day momentum;
- volatility: annualized volatility, downside volatility and rolling drawdown;
- liquidity: traded amount and turnover;
- value: book-to-price, earnings yield and trailing dividend yield;
- quality: ROE and operating cash flow per share;
- growth: reported profit and revenue growth.

These are candidate features, not approved trading signals. Promotion requires
coverage checks, IC/Rank-IC analysis, turnover and decay analysis, correlation
control, walk-forward testing and a full cost-aware portfolio backtest.

## Next milestones

1. Build daily cross-sectional factor snapshots for a survivorship-safe universe.
2. Add robust winsorization, industry/size neutralization and rank normalization.
3. Add IC, Rank IC, ICIR, quantile returns, decay and turnover reports.
4. Add factor correlation and redundancy gates.
5. Combine approved factors into industry-specific scorecards.
6. Feed scores into a Top-K gradual replacement portfolio strategy.

## Bank model v1

The first sector model is deliberately fixed rather than parameter-searched:

- value 40%: dividend yield, earnings yield and book-to-price;
- defensive 25%: low 60-day volatility and drawdown position;
- momentum 20%: 252-to-21-day momentum and 120-day return;
- quality 15%: ROE and reported revenue growth.

It holds eight banks at 10% each, keeps existing holdings inside a rank-12
buffer, and normally replaces no more than two names per monthly rebalance.
The model remains `RESEARCH_ONLY`. Its 2022+ results have already been inspected
and therefore cannot be represented as a pristine untouched test set.

Robustness testing found that removing the quality group improved both historical
partitions. That variant is frozen in `configs/bank_models.yaml` as a
`PROSPECTIVE_CANDIDATE`; it is not retroactively promoted. Only observations
after 2026-07-27 count as new forward evidence.
