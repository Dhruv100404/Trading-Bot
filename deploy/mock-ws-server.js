#!/usr/bin/env bun
// Mock Dhan WebSocket server for testing signal flow after market hours
// Sends fake Type 8 (Full Quote, 162 bytes) ticks for a few stocks
// Simulates: stock opens at price X, then moves up/down to trigger signals

const PORT = 9999;

// Fake stocks with security IDs matching real F&O stocks in watchlist
const STOCKS = [
  { secId: 1333, symbol: 'RELIANCE', basePrice: 790, direction: 1 },   // BUY signal
  { secId: 2885, symbol: 'TCS', basePrice: 3950, direction: -1 },      // SELL signal
  { secId: 11536, symbol: 'INFY', basePrice: 1800, direction: 1 },     // BUY signal
  { secId: 5258, symbol: 'ETERNAL', basePrice: 245, direction: -1 },   // SELL signal
  { secId: 694, symbol: 'SBIN', basePrice: 780, direction: 1 },        // BUY signal
];

let tickCount = 0;

function buildFullQuotePacket(secId, ltp, open, high, low, close, volume, atp, buyQty, sellQty) {
  const buf = Buffer.alloc(162);

  // Header
  buf.writeUInt8(8, 0);           // Type 8 = Full Quote
  buf.writeUInt16BE(162, 1);      // Message length
  buf.writeUInt8(1, 3);           // Exchange = NSE_EQ
  buf.writeUInt32LE(secId, 4);    // Security ID

  // Quote data
  buf.writeFloatLE(ltp, 8);       // LTP
  buf.writeUInt16LE(1, 12);       // LTQ
  buf.writeUInt32LE(Math.floor(Date.now()/1000), 14); // LTT
  buf.writeFloatLE(atp, 18);      // ATP
  buf.writeUInt32LE(volume, 22);  // Volume
  buf.writeUInt32LE(sellQty, 26); // Sell Qty
  buf.writeUInt32LE(buyQty, 30);  // Buy Qty
  buf.writeUInt32LE(0, 34);       // OI
  buf.writeUInt32LE(0, 38);       // High OI

  // Padding + OHLC
  buf.writeFloatLE(0, 42);        // padding
  buf.writeFloatLE(open, 46);     // Day Open
  buf.writeFloatLE(close, 50);    // Day Close (prev)
  buf.writeFloatLE(high, 54);     // Day High
  buf.writeFloatLE(low, 58);      // Day Low

  // 5-level depth (simplified — just L1)
  for (let i = 0; i < 5; i++) {
    const off = 62 + i * 20;
    const spread = (i + 1) * 0.05;
    buf.writeUInt32LE(1000 - i * 200, off);        // bid qty
    buf.writeUInt16LE(5 - i, off + 4);              // bid orders
    buf.writeFloatLE(ltp - spread, off + 6);         // bid price
    buf.writeFloatLE(ltp + spread, off + 10);        // ask price
    buf.writeUInt16LE(5 - i, off + 14);              // ask orders
    buf.writeUInt32LE(1000 - i * 200, off + 16);    // ask qty
  }

  return buf;
}

console.log(`Starting mock Dhan WebSocket server on port ${PORT}...`);
console.log(`Stocks: ${STOCKS.map(s => s.symbol).join(', ')}`);
console.log(`Will send ticks that gradually move prices to trigger signals`);
console.log('');

Bun.serve({
  port: PORT,
  fetch(req, server) {
    // Accept WebSocket upgrade
    if (server.upgrade(req)) return;
    return new Response('Mock Dhan WS Server', { status: 200 });
  },
  websocket: {
    open(ws) {
      console.log('[Mock] Client connected!');

      // Send ticks every 500ms
      const interval = setInterval(() => {
        tickCount++;

        for (const stock of STOCKS) {
          // Simulate price movement: starts at basePrice, moves 0.5% per tick in direction
          const movePct = tickCount * 0.15 * stock.direction; // 0.15% per tick
          const ltp = stock.basePrice * (1 + movePct / 100);
          const open = stock.basePrice;
          const high = stock.direction > 0 ? ltp : open;
          const low = stock.direction > 0 ? open : ltp;
          const close = stock.basePrice * 0.99; // prev close slightly below
          const volume = tickCount * 50000;
          const atp = (open + ltp) / 2;
          const buyQty = stock.direction > 0 ? 500000 : 200000;
          const sellQty = stock.direction > 0 ? 200000 : 500000;

          const packet = buildFullQuotePacket(
            stock.secId, ltp, open, high, low, close, volume, atp, buyQty, sellQty
          );

          try {
            ws.sendBinary(packet);
          } catch (e) {
            clearInterval(interval);
            return;
          }

          if (tickCount <= 3 || tickCount % 10 === 0) {
            const move = ((ltp - open) / open * 100).toFixed(2);
            console.log(`  [Tick ${tickCount}] ${stock.symbol}: ${ltp.toFixed(2)} (${move}%)`);
          }
        }
      }, 500);

      ws.data = { interval };
    },
    message(ws, message) {
      // Parse subscription messages
      try {
        const sub = JSON.parse(message);
        console.log(`[Mock] Subscription: RequestCode=${sub.RequestCode}, ${sub.InstrumentCount} instruments`);
      } catch {
        console.log(`[Mock] Binary message: ${message.length} bytes`);
      }
    },
    close(ws) {
      console.log('[Mock] Client disconnected');
      if (ws.data?.interval) clearInterval(ws.data.interval);
    },
  },
});

console.log(`Mock WS server running on ws://localhost:${PORT}`);
console.log('Point your engine to ws://localhost:9999/?version=2&token=test&clientId=test&authType=2');
