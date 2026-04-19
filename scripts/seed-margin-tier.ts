#!/usr/bin/env bun
/**
 * seed-margin-tier.ts
 *
 * Reads data/margin-stocks.json and updates ClickHouse watchlist
 * to add "Margin4x" tier to matching stocks.
 *
 * Uses ReplacingMergeTree INSERT (latest row wins by inserted_at).
 *
 * Usage: bun run scripts/seed-margin-tier.ts
 */

import { readFileSync } from "node:fs";

const CH_URL = process.env.CLICKHOUSE_URL || "http://localhost:8123";

interface MarginStock {
  securityId: string;
  tradingSymbol: string;
  leverage: number;
}

async function chQuery(sql: string): Promise<string> {
  const res = await fetch(CH_URL, { method: "POST", body: sql });
  if (!res.ok) throw new Error(`CH error: ${res.status} ${await res.text()}`);
  return res.text();
}

async function main() {
  // Load margin stocks
  const data = JSON.parse(
    readFileSync("data/margin-stocks.json", "utf-8")
  );
  const marginStocks: MarginStock[] = data.stocks;
  const marginSecIds = new Set(marginStocks.map((s) => s.securityId));
  console.log(`Loaded ${marginSecIds.size} margin stocks from JSON`);

  // Get current watchlist from ClickHouse
  const rawRows = await chQuery(
    `SELECT security_id, symbol, company_name, tiers, enabled, min_volume
     FROM trading.watchlist FINAL
     FORMAT JSONEachRow`
  );

  const rows = rawRows
    .trim()
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line));

  console.log(`Watchlist has ${rows.length} stocks`);

  // Find stocks that need Margin4x added
  let updated = 0;
  let alreadyTagged = 0;
  const insertRows: string[] = [];

  for (const row of rows) {
    const tiers: string[] = row.tiers;

    if (marginSecIds.has(row.security_id)) {
      if (tiers.includes("Margin4x")) {
        alreadyTagged++;
        continue;
      }

      // Add Margin4x to tiers
      const newTiers = [...tiers, "Margin4x"];
      const tiersStr = newTiers.map((t) => `'${t}'`).join(",");

      // Escape single quotes in company_name
      const company = (row.company_name || "").replace(/'/g, "\\'");
      const symbol = (row.symbol || "").replace(/'/g, "\\'");

      insertRows.push(
        `('${row.security_id}','${symbol}','${company}',[${tiersStr}],${row.enabled},${row.min_volume})`
      );
      updated++;
    }
  }

  console.log(
    `${updated} stocks to update, ${alreadyTagged} already have Margin4x`
  );

  if (insertRows.length === 0) {
    console.log("Nothing to do.");
    return;
  }

  // Batch insert (500 at a time)
  const BATCH = 500;
  for (let i = 0; i < insertRows.length; i += BATCH) {
    const batch = insertRows.slice(i, i + BATCH);
    const sql = `INSERT INTO trading.watchlist
      (security_id, symbol, company_name, tiers, enabled, min_volume)
      VALUES ${batch.join(",")}`;

    await chQuery(sql);
    console.log(
      `  Inserted batch ${Math.floor(i / BATCH) + 1}/${Math.ceil(insertRows.length / BATCH)}`
    );
  }

  // Verify
  const countResult = await chQuery(
    `SELECT count() FROM trading.watchlist FINAL WHERE has(tiers, 'Margin4x')`
  );
  console.log(`\nDone. ${countResult.trim()} stocks now have Margin4x tier.`);
}

main().catch((e) => {
  console.error("Fatal:", e);
  process.exit(1);
});
