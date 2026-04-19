#!/usr/bin/env bun
/**
 * fetch-margin-stocks.ts
 *
 * Finds all NSE equity stocks where Dhan gives 4–10x intraday margin.
 *
 * Steps:
 *   1. Download scrip master CSV from Dhan
 *   2. Filter to NSE equity cash instruments
 *   3. Batch-call /v2/margincalculator to get leverage per stock
 *   4. Output stocks with leverage 4–10x as JSON
 *
 * Usage:
 *   bun run scripts/fetch-margin-stocks.ts
 *   bun run scripts/fetch-margin-stocks.ts --min-lev 5 --max-lev 10
 *   bun run scripts/fetch-margin-stocks.ts --output data/margin-stocks.json
 */

const ACCESS_TOKEN =
  "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc0OTYzNzI5LCJpYXQiOjE3NzQ4NzczMjksInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODk2NDk3In0.BYuo-AN9nj5bMcLS-6I5ACwmKT6j1nB7QBrMN4UsU5PN7h6r_8DnYjT87VTIaM0mzxN6unlbPgzSksq9EvUD8g";
const CLIENT_ID = "1100896497";

const SCRIP_CSV_URL =
  "https://images.dhan.co/api-data/api-scrip-master-detailed.csv";
const DHAN_BASE = "https://api.dhan.co/v2";

// ── CLI args ──
import { parseArgs } from "node:util";
import { writeFileSync } from "node:fs";

const { values: args } = parseArgs({
  options: {
    "min-lev": { type: "string", default: "4" },
    "max-lev": { type: "string", default: "10" },
    output: { type: "string", short: "o", default: "data/margin-stocks.json" },
  },
});

const MIN_LEV = Number(args["min-lev"]);
const MAX_LEV = Number(args["max-lev"]);
const OUT_PATH = args.output!;

// ── Rate limiter ──
const MAX_RPS = 5;
const MAX_RPM = 200;
let callLog: number[] = [];

function canCall(): boolean {
  const now = Date.now();
  callLog = callLog.filter((t) => now - t < 60_000);
  const last1s = callLog.filter((t) => now - t < 1_000).length;
  return last1s < MAX_RPS && callLog.length < MAX_RPM;
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

// ── Types ──
interface ScripRow {
  securityId: string;
  exchangeSegment: "NSE_EQ" | "BSE_EQ";
  tradingSymbol: string;
  lastPrice: number;
}

interface MarginResult {
  securityId: string;
  exchangeSegment: string;
  tradingSymbol: string;
  leverage: number;
  totalMargin: number;
  price: number;
}

// ─────────────────────────────────────────────
// 1. Download & parse scrip master
// ─────────────────────────────────────────────
async function fetchScripMaster(): Promise<ScripRow[]> {
  console.log("Downloading scrip master CSV...");
  const res = await fetch(SCRIP_CSV_URL);
  if (!res.ok) throw new Error(`Scrip master download failed: ${res.status}`);
  const text = await res.text();

  const lines = text.split("\n");
  const header = lines[0].split(",").map((h) => h.trim());

  // Find column indices
  const col = (name: string) => {
    const idx = header.indexOf(name);
    if (idx === -1) {
      // Try case-insensitive
      const lc = name.toLowerCase();
      for (let i = 0; i < header.length; i++) {
        if (header[i].toLowerCase() === lc) return i;
      }
    }
    return idx;
  };

  const iExch = col("EXCH_ID");
  const iSeg = col("SEGMENT");
  const iInst = col("INSTRUMENT");
  const iSecId = col("SECURITY_ID");
  const iSymbol = col("UNDERLYING_SYMBOL");    // trading symbol like "RELIANCE"
  const iName = col("SYMBOL_NAME");            // full name
  const iInstType = col("INSTRUMENT_TYPE");    // ES = equity shares
  const iSeries = col("SERIES");               // EQ = normal equity
  const iUpper = col("SM_UPPER_LIMIT");        // upper circuit
  const iLower = col("SM_LOWER_LIMIT");        // lower circuit

  if (iExch === -1 || iSecId === -1) {
    console.log("CSV columns found:", header.slice(0, 25));
    throw new Error("Could not find expected CSV columns");
  }

  console.log(
    `CSV: ${lines.length} rows, columns: EXCH=${iExch}, SEG=${iSeg}, SECID=${iSecId}, SYM=${iSymbol}, TYPE=${iInstType}, SERIES=${iSeries}`
  );

  const scripts: ScripRow[] = [];

  for (let i = 1; i < lines.length; i++) {
    const row = lines[i].split(",");
    if (row.length < 10) continue;

    const exch = row[iExch]?.trim();
    const seg = row[iSeg]?.trim();
    const instType = row[iInstType]?.trim();
    const series = row[iSeries]?.trim();
    const secId = row[iSecId]?.trim();
    const symbol = row[iSymbol]?.trim() || "";

    // Filter: NSE equity cash instruments (SERIES=EQ, INSTRUMENT_TYPE=ES)
    if (exch !== "NSE") continue;
    if (seg !== "E") continue;
    if (instType !== "ES") continue;   // equity shares only (not debt/bond)
    if (series !== "EQ") continue;     // normal equity (not BE, BL, etc.)
    if (!secId || secId === "0") continue;

    // Reference price = midpoint of circuit limits
    const upper = parseFloat(row[iUpper] || "0");
    const lower = parseFloat(row[iLower] || "0");
    const refPrice = upper > 0 && lower > 0 ? (upper + lower) / 2 : 0;
    if (refPrice <= 0) continue;

    scripts.push({
      securityId: secId,
      exchangeSegment: "NSE_EQ",
      tradingSymbol: symbol,
      lastPrice: Math.round(refPrice * 100) / 100,
    });
  }

  console.log(`Filtered: ${scripts.length} NSE equity instruments`);
  return scripts;
}

// ─────────────────────────────────────────────
// 2. Call margin calculator with throttling
// ─────────────────────────────────────────────
async function callMarginCalc(
  scripList: Array<{
    exchangeSegment: string;
    transactionType: string;
    quantity: number;
    productType: string;
    securityId: string;
    price: number;
    triggerPrice: number;
  }>
): Promise<any> {
  // Wait for rate limit window
  while (!canCall()) {
    await wait(120);
  }
  callLog.push(Date.now());

  const payload = {
    dhanClientId: CLIENT_ID,
    includePosition: false,
    includeOrders: false,
    scripList,
  };

  const res = await fetch(`${DHAN_BASE}/margincalculator`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "access-token": ACCESS_TOKEN,
    },
    body: JSON.stringify(payload),
  });

  if (res.status === 429) {
    console.warn("  429 rate limit hit, backing off 3s...");
    await wait(3000);
    return callMarginCalc(scripList);
  }

  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Margin API ${res.status}: ${body}`);
  }

  return res.json();
}

// ─────────────────────────────────────────────
// 3. Process in batches
// ─────────────────────────────────────────────
function chunk<T>(arr: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

function parseLeverage(levStr: string | number): number {
  // Dhan returns leverage as "5.00X" or "4.00X"
  const s = String(levStr).replace(/[Xx]/g, "").trim();
  return parseFloat(s) || 0;
}

async function findMarginStocks(scripts: ScripRow[]): Promise<MarginResult[]> {
  const results: MarginResult[] = [];
  let processed = 0;
  let errors = 0;

  // Use single-scrip endpoint (reliable, returns leverage directly)
  console.log(`Calling /margincalculator for ${scripts.length} stocks (single mode, ~5 req/s)...`);

  for (const s of scripts) {
    try {
      const payload = {
        dhanClientId: CLIENT_ID,
        exchangeSegment: s.exchangeSegment,
        transactionType: "BUY",
        quantity: 1,
        productType: "INTRADAY",
        securityId: s.securityId,
        price: s.lastPrice,
        triggerPrice: 0,
      };

      // Throttle
      while (!canCall()) await wait(120);
      callLog.push(Date.now());

      const res = await fetch(`${DHAN_BASE}/margincalculator`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "access-token": ACCESS_TOKEN,
        },
        body: JSON.stringify(payload),
      });

      if (res.status === 429) {
        console.warn("  429 hit, backing off 3s...");
        await wait(3000);
        // retry once
        while (!canCall()) await wait(120);
        callLog.push(Date.now());
        const retry = await fetch(`${DHAN_BASE}/margincalculator`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "access-token": ACCESS_TOKEN,
          },
          body: JSON.stringify(payload),
        });
        if (!retry.ok) { errors++; processed++; continue; }
        const retryData = await retry.json();
        const lev = parseLeverage(retryData.leverage);
        if (lev >= MIN_LEV && lev <= MAX_LEV) {
          results.push({
            securityId: s.securityId,
            exchangeSegment: s.exchangeSegment,
            tradingSymbol: s.tradingSymbol,
            leverage: lev,
            totalMargin: Number(retryData.totalMargin || 0),
            price: s.lastPrice,
          });
        }
        processed++;
        continue;
      }

      if (!res.ok) {
        errors++;
        processed++;
        continue;
      }

      const data = await res.json();
      const lev = parseLeverage(data.leverage);

      if (lev >= MIN_LEV && lev <= MAX_LEV) {
        results.push({
          securityId: s.securityId,
          exchangeSegment: s.exchangeSegment,
          tradingSymbol: s.tradingSymbol,
          leverage: lev,
          totalMargin: Number(data.totalMargin || 0),
          price: s.lastPrice,
        });
      }
    } catch (e: any) {
      errors++;
    }

    processed++;
    if (processed % 100 === 0) {
      console.log(
        `  ${processed}/${scripts.length} (${results.length} matches, ${errors} err)`
      );
    }
  }

  console.log(
    `\nComplete: ${results.length} stocks with ${MIN_LEV}-${MAX_LEV}x leverage (${errors} errors)`
  );
  return results;
}

// ─────────────────────────────────────────────
// Main
// ─────────────────────────────────────────────
async function main() {
  const t0 = Date.now();

  const scripts = await fetchScripMaster();

  console.log(
    `\nFinding stocks with ${MIN_LEV}–${MAX_LEV}x intraday margin...`
  );
  const matched = await findMarginStocks(scripts);

  // Sort by leverage desc
  matched.sort((a, b) => b.leverage - a.leverage);

  // Write output
  const output = {
    generatedAt: new Date().toISOString(),
    filter: { minLeverage: MIN_LEV, maxLeverage: MAX_LEV },
    count: matched.length,
    stocks: matched,
  };

  writeFileSync(OUT_PATH, JSON.stringify(output, null, 2));
  console.log(`\nWrote ${matched.length} stocks to ${OUT_PATH}`);

  // Print summary
  console.log("\n── SUMMARY ──");
  console.log(`Total NSE equity: ${scripts.length}`);
  console.log(`${MIN_LEV}–${MAX_LEV}x leverage: ${matched.length}`);

  // Leverage distribution
  const buckets: Record<string, number> = {};
  for (const m of matched) {
    const k = `${Math.floor(m.leverage)}x`;
    buckets[k] = (buckets[k] || 0) + 1;
  }
  console.log("Leverage distribution:");
  for (const [k, v] of Object.entries(buckets).sort()) {
    console.log(`  ${k}: ${v} stocks`);
  }

  // Top 20 by leverage
  console.log(`\nTop 20 by leverage:`);
  for (const m of matched.slice(0, 20)) {
    console.log(
      `  ${m.tradingSymbol.padEnd(20)} ${m.leverage}x  price=₹${m.price}  margin=₹${m.totalMargin}`
    );
  }

  console.log(`\nDone in ${((Date.now() - t0) / 1000).toFixed(1)}s`);
}

main().catch((e) => {
  console.error("Fatal:", e);
  process.exit(1);
});
