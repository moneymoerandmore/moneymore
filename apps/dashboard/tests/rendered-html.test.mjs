import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const app = new URL("../app/", import.meta.url);

test("dashboard presents the complete multi-sector portfolio", async () => {
  const source = await readFile(new URL("Dashboard.tsx", app), "utf8");
  for (const label of ["多行业动态组合", "整体配置全貌", "行业与个股", "策略研究", "运行与对账", "银行", "红利", "工业有色", "芯片", "创业板成长"]) {
    assert.match(source, new RegExp(label));
  }
  assert.match(source, /\/api\/sector-portfolio/);
  assert.match(source, /\/api\/bank-dashboard/);
  assert.match(source, /\/api\/multi-sector-execution/);
  assert.match(source, /symbol_names/);
  assert.match(source, /security\(v,names\)/);
  assert.match(source, /names\[s\]/);
  assert.doesNotMatch(source, /MA120|MA250|招商银行|长江电力/);
});

test("metadata describes the multi-sector console", async () => {
  const layout = await readFile(new URL("layout.tsx", app), "utf8");
  assert.match(layout, /MoneyMore · 多行业量化组合/);
  assert.match(layout, /银行、红利、工业有色、芯片与创业板成长/);
});
