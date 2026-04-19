# WebSocket 400 Bad Request — Debug Report

## Problem
Connecting to `wss://api-feed.dhan.co?version=2&token=<JWT>&clientId=1100896497&authType=2` from Rust using `tokio-tungstenite` returns **HTTP 400 Bad Request**.

The **exact same URL** works perfectly from:
- **Bun/Node.js** `new WebSocket(url)` — connects, receives ticks ✅
- **Browser JavaScript** — connects ✅

But fails from:
- **Rust `tokio-tungstenite` v0.23** with `native-tls` feature — 400 ❌
- **Rust `tokio-tungstenite` v0.23** with `rustls-tls-native-roots` feature — 400 ❌
- Both `connect_async(url)` and `connect_async_tls_with_config(url, None, false, None)` — 400 ❌

## Error Details
```
HTTP error: 400 Bad Request
Response headers: {"content-type": "text/plain; charset=utf-8", "connection": "close"}
Response body: "400 Bad Request" (15 bytes)
```

## What's Verified Working
- Token is valid (303 chars, JWT, exp=2026-03-25T23:53 IST)
- REST API works with same token (`/v2/marketfeed/ltp` returns 200)
- WebSocket from Bun on same EC2 machine with same token — CONNECTED and received 36 ticks in 10 seconds
- Token has no trailing whitespace (verified via hex dump)
- Client ID is correct: `1100896497`

## URL Being Sent
```
wss://api-feed.dhan.co?version=2&token=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuI...W2tQ&clientId=1100896497&authType=2
```

## Rust Code
```rust
use tokio_tungstenite::connect_async;
let url = format!("wss://api-feed.dhan.co?version=2&token={}&clientId={}&authType=2", token, client_id);
match connect_async(&url).await { ... }
```

## Hypothesis: `tokio-tungstenite` sends different HTTP upgrade request than browsers

### Browser/Bun sends:
```http
GET /?version=2&token=...&clientId=...&authType=2 HTTP/1.1
Host: api-feed.dhan.co
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Version: 13
Sec-WebSocket-Key: <random>
Origin: <none or browser origin>
```

### `tokio-tungstenite` might send:
- Different `Host` header format
- URL-encoded query parameters (encoding `=` or `&` characters)
- Missing or different `Origin` header
- Different HTTP version
- The path might be sent as the full URL instead of just `/?version=2&...`

## Questions for Perplexity

1. **Does `tokio-tungstenite` URL-encode query parameters differently than browsers?** Specifically, does it percent-encode the JWT token's `+`, `/`, or `=` characters?

2. **Has anyone reported `tokio-tungstenite` 400 errors when connecting to WebSocket servers that work fine from JavaScript?** Look for GitHub issues on `tokio-tungstenite` or `tungstenite` repos.

3. **Is there an alternative Rust WebSocket client that works better with financial API WebSockets?** Options: `async-tungstenite`, `websocket` crate, `reqwest` with WebSocket upgrade, `fastwebsockets`.

4. **Does Dhan's WebSocket server (api-feed.dhan.co) have specific requirements for the HTTP upgrade request?** Check DhanHQ GitHub repos, community forums, or Discord.

5. **Would using `reqwest` to do the HTTP upgrade manually (instead of `tokio-tungstenite`) work?** `reqwest` v0.12 supports WebSocket upgrade via `reqwest::Client::get(url).upgrade()`.

## Cargo.toml Context
```toml
tokio-tungstenite = { version = "0.23", features = ["rustls-tls-native-roots"] }
# Also tried: features = ["native-tls"]
```

## Environment
- EC2 Ubuntu 24.04, Rust 1.85, Docker Alpine 3.20
- Same machine where Bun WebSocket works fine
