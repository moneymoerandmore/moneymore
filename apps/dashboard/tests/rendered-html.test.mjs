import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const app = new URL("../app/", import.meta.url);

test("dashboard is portfolio-first and removes legacy single-stock pages", async () => {
  const source = await readFile(new URL("Dashboard.tsx", app), "utf8");
  assert.match(source, /银行多因子组合/);
  assert.match(source, /组合总览/);
  assert.match(source, /因子研究/);
  assert.match(source, /成交对账/);
  assert.match(source, /每日流水线/);
  assert.match(source, /\/api\/bank-dashboard/);
  assert.match(source, /\/api\/bank-execution/);
  assert.doesNotMatch(source, /招商银行|长江电力|MA120|MA250/);
});

test("metadata describes the bank portfolio console", async () => {
  const layout = await readFile(new URL("layout.tsx", app), "utf8");
  assert.match(layout, /MoneyMore · 银行多因子组合/);
  assert.match(layout, /Top-K 影子组合/);
});
