#!/usr/bin/env bun
/**
 * seed-liquid-tier.ts
 *
 * Reads margin_liquidity_report and margin-stocks.json to find
 * margin stocks with avg f5Vol*price >= 5L, then tags them
 * with "Liquid5L" tier in ClickHouse watchlist.
 *
 * Usage: bun run scripts/seed-liquid-tier.ts
 */

import { readFileSync } from "node:fs";

const CH_URL = process.env.CLICKHOUSE_URL || "http://localhost:8123";
const DATA_DIR = "data";
const MIN_VOL_RS = 500_000; // 5 lakh

async function chQuery(sql: string): Promise<string> {
  const res = await fetch(CH_URL, { method: "POST", body: sql });
  if (!res.ok) throw new Error(`CH error: ${res.status} ${await res.text()}`);
  return res.text();
}

async function main() {
  // Seed tier_state
  await chQuery(
    `INSERT INTO trading.tier_state (tier_name, enabled) VALUES ('Liquid5L', 0)`
  );
  console.log("Seeded Liquid5L tier");

  // Parse the liquidity report to extract symbols with vol >= 5L
  // Faster approach: re-compute from candle data inline
  // But since we already have margin_liquidity_report.txt, let's parse it.
  // Actually, the report format is hard to parse. Let's use the candle data directly.

  // Load margin stocks (these are the ones with 4-10x margin)
  const marginData = JSON.parse(readFileSync(`${DATA_DIR}/margin-stocks.json`, "utf-8"));
  const marginSyms = new Set(marginData.stocks.map((s: any) => s.tradingSymbol));

  // Load candle data, compute avg f5vol*price per symbol (only margin stocks)
  const files = [
    `${DATA_DIR}/candles-consolidated.ndjson`,
    `${DATA_DIR}/candles-consolidated_new.ndjson`,
  ];

  const volSums: Record<string, { total: number; count: number }> = {};

  for (const fp of files) {
    const text = readFileSync(fp, "utf-8");
    for (const line of text.split("\n")) {
      if (!line.trim()) continue;
      const rec = JSON.parse(line);
      const sym = rec.symbol;
      if (!marginSyms.has(sym)) continue;
      const vol = (rec.f5Vol || 0) * (rec.dayOpen || 0);
      if (!volSums[sym]) volSums[sym] = { total: 0, count: 0 };
      volSums[sym].total += vol;
      volSums[sym].count += 1;
    }
  }

  // Filter: avg vol >= 5L
  const liquidSyms: string[] = [];
  for (const [sym, data] of Object.entries(volSums)) {
    const avg = data.total / data.count;
    if (avg >= MIN_VOL_RS) liquidSyms.push(sym);
  }

  console.log(`Margin stocks in candle data: ${Object.keys(volSums).length}`);
  console.log(`With avg f5vol*price >= 5L: ${liquidSyms.length}`);

  const liquidSet = new Set(liquidSyms);

  // Get current watchlist
  const rawRows = await chQuery(
    `SELECT security_id, symbol, company_name, tiers, enabled, min_volume
     FROM trading.watchlist FINAL FORMAT JSONEachRow`
  );

  const rows = rawRows.trim().split("\n").filter(Boolean).map((l) => JSON.parse(l));
  console.log(`Watchlist: ${rows.length} stocks`);

  const insertRows: string[] = [];
  let already = 0;

  for (const row of rows) {
    const tiers: string[] = row.tiers;
    if (!liquidSet.has(row.symbol)) continue;

    if (tiers.includes("Liquid5L")) {
      already++;
      continue;
    }

    const newTiers = [...tiers, "Liquid5L"];
    const tiersStr = newTiers.map((t) => `'${t}'`).join(",");
    const company = (row.company_name || "").replace(/'/g, "\\'");
    const symbol = (row.symbol || "").replace(/'/g, "\\'");

    insertRows.push(
      `('${row.security_id}','${symbol}','${company}',[${tiersStr}],${row.enabled},${row.min_volume})`
    );
  }

  console.log(`To update: ${insertRows.length}, already tagged: ${already}`);

  if (insertRows.length > 0) {
    const BATCH = 500;
    for (let i = 0; i < insertRows.length; i += BATCH) {
      const batch = insertRows.slice(i, i + BATCH);
      await chQuery(
        `INSERT INTO trading.watchlist (security_id, symbol, company_name, tiers, enabled, min_volume)
         VALUES ${batch.join(",")}`
      );
      console.log(`  Batch ${Math.floor(i / BATCH) + 1}/${Math.ceil(insertRows.length / BATCH)}`);
    }
  }

  const count = (await chQuery(
    `SELECT count() FROM trading.watchlist FINAL WHERE has(tiers, 'Liquid5L')`
  )).trim();
  console.log(`Done. ${count} stocks now have Liquid5L tier.`);
}

main().catch((e) => {
  console.error("Fatal:", e);
  process.exit(1);
});
