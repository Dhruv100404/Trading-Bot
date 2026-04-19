use serde::Deserialize;
use anyhow::Result;
use crate::zerodha::client::ZerodhaClient;
use crate::types::Direction;

/// Zerodha Kite Connect order response.
/// Successful: {"status": "success", "data": {"order_id": "..."}}
/// Failed:    {"status": "error", "message": "...", "error_type": "..."}
#[derive(Deserialize, Debug)]
struct KiteResponse {
    status: String,
    data: Option<KiteOrderData>,
    message: Option<String>,
}

#[derive(Deserialize, Debug)]
struct KiteOrderData {
    order_id: Option<String>,
}

/// Place a MARKET INTRADAY (MIS) order on Zerodha.
/// Uses form-encoded body (NOT JSON — Kite API requires this).
/// `tradingsymbol` is the NSE symbol (e.g., "RELIANCE", "INFY").
pub async fn place_order(
    client: &ZerodhaClient,
    tradingsymbol: &str,
    direction: &Direction,
    quantity: u32,
) -> Result<String> {
    let tx_type = match direction { Direction::Buy => "BUY", Direction::Sell => "SELL" };

    let resp = client.post("/orders/regular")
        .form(&[
            ("tradingsymbol", tradingsymbol),
            ("exchange", "NSE"),
            ("transaction_type", tx_type),
            ("order_type", "MARKET"),
            ("quantity", &quantity.to_string()),
            ("product", "MIS"),
            ("validity", "DAY"),
            ("market_protection", "-1"),
        ])
        .send().await?;

    let status_code = resp.status();
    let body: KiteResponse = resp.json().await
        .map_err(|e| anyhow::anyhow!("Zerodha response parse error (HTTP {}): {}", status_code, e))?;

    if body.status == "success" {
        body.data
            .and_then(|d| d.order_id)
            .ok_or_else(|| anyhow::anyhow!("Zerodha: success but no order_id"))
    } else {
        Err(anyhow::anyhow!("Zerodha order failed: {}", body.message.unwrap_or_else(|| "unknown error".into())))
    }
}

#[derive(Deserialize, Debug)]
pub struct KiteOrderDetail {
    pub order_id: Option<String>,
    /// Status: COMPLETE, REJECTED, CANCELLED, OPEN, TRIGGER_PENDING
    pub status: Option<String>,
    /// Average fill price
    #[serde(default)]
    pub average_price: f32,
}

/// Zerodha order status response.
/// Round a price to NSE equity tick size (₹0.05).
fn round_tick(price: f32) -> f32 {
    (price * 20.0).round() / 20.0
}

/// Place a protective stop-loss order on Zerodha.
///
/// Tries SL-M first. If rejected, falls back to SL (stop-loss limit)
/// with a limit price 0.5% worse than the trigger.
///
/// `position_direction` is the direction of the open position (not the SL order).
pub async fn place_sl_order(
    client: &ZerodhaClient,
    tradingsymbol: &str,
    position_direction: &Direction,
    quantity: u32,
    trigger_price: f32,
) -> Result<String> {
    let tx_type = match position_direction {
        Direction::Buy  => "SELL",
        Direction::Sell => "BUY",
    };
    let trigger = round_tick(trigger_price);

    // Attempt 1: SL-M
    let resp = client.post("/orders/regular")
        .form(&[
            ("tradingsymbol", tradingsymbol),
            ("exchange", "NSE"),
            ("transaction_type", tx_type),
            ("order_type", "SL-M"),
            ("quantity", &quantity.to_string()),
            ("product", "MIS"),
            ("validity", "DAY"),
            ("trigger_price", &format!("{:.2}", trigger)),
        ])
        .send().await?;

    let status_code = resp.status();
    let body: KiteResponse = resp.json().await
        .map_err(|e| anyhow::anyhow!("Zerodha SL-M parse error (HTTP {}): {}", status_code, e))?;

    if body.status == "success" {
        if let Some(oid) = body.data.and_then(|d| d.order_id) {
            tracing::info!("[SL] Zerodha SL-M placed: {} trigger={:.2} order_id={}", tradingsymbol, trigger, oid);
            return Ok(oid);
        }
    }

    let slm_err = body.message.as_deref().unwrap_or("unknown");
    tracing::warn!("[SL] Zerodha SL-M rejected for {}: {} — trying SL-Limit", tradingsymbol, slm_err);

    // Attempt 2: SL (stop-loss limit) with a limit price 0.5% worse than trigger
    let limit_price = match position_direction {
        Direction::Sell => round_tick(trigger * 1.005), // BUY SL: limit above trigger
        Direction::Buy  => round_tick(trigger * 0.995), // SELL SL: limit below trigger
    };

    let resp = client.post("/orders/regular")
        .form(&[
            ("tradingsymbol", tradingsymbol),
            ("exchange", "NSE"),
            ("transaction_type", tx_type),
            ("order_type", "SL"),
            ("quantity", &quantity.to_string()),
            ("product", "MIS"),
            ("validity", "DAY"),
            ("trigger_price", &format!("{:.2}", trigger)),
            ("price", &format!("{:.2}", limit_price)),
        ])
        .send().await?;

    let status_code = resp.status();
    let body: KiteResponse = resp.json().await
        .map_err(|e| anyhow::anyhow!("Zerodha SL parse error (HTTP {}): {}", status_code, e))?;

    if body.status == "success" {
        let oid = body.data
            .and_then(|d| d.order_id)
            .ok_or_else(|| anyhow::anyhow!("Zerodha SL: success but no order_id"))?;
        tracing::info!("[SL] Zerodha SL-Limit placed: {} trigger={:.2} limit={:.2} order_id={}", tradingsymbol, trigger, limit_price, oid);
        return Ok(oid);
    }

    Err(anyhow::anyhow!("Zerodha SL-Limit also rejected for {}: {}", tradingsymbol,
        body.message.unwrap_or_else(|| "unknown error".into())))
}

/// Cancel a pending order on Zerodha.
/// Returns Ok(true) if cancelled, Ok(false) if order was already traded/gone.
pub async fn cancel_order(client: &ZerodhaClient, order_id: &str) -> Result<bool> {
    let path = format!("/orders/regular/{}", order_id);
    let resp = client.delete(&path).send().await?;
    let status_code = resp.status();
    let body: KiteResponse = resp.json().await
        .map_err(|e| anyhow::anyhow!("Zerodha cancel parse error (HTTP {}): {}", status_code, e))?;

    if body.status == "success" {
        tracing::info!("[SL] Zerodha order {} cancelled", order_id);
        return Ok(true);
    }

    let msg = body.message.as_deref().unwrap_or("unknown");
    // "Order is already complete" or similar means it was already traded
    if msg.contains("complete") || msg.contains("traded") || msg.contains("cancelled") {
        tracing::info!("[SL] Zerodha order {} cancel skipped (already done): {}", order_id, msg);
        return Ok(false);
    }

    Err(anyhow::anyhow!("Zerodha cancel order {} failed: {}", order_id, msg))
}

#[derive(Deserialize, Debug)]
struct KiteOrderStatusResponse {
    status: String,
    data: Option<Vec<KiteOrderDetail>>,
    message: Option<String>,
}

pub struct OrderStatusResult {
    pub order_status: String,
    pub average_traded_price: f32,
}

/// Fetch order status from Zerodha.
pub async fn get_order_status(client: &ZerodhaClient, order_id: &str) -> Result<OrderStatusResult> {
    let path = format!("/orders/{}", order_id);
    let resp = client.get(&path).send().await?;
    let body: KiteOrderStatusResponse = resp.json().await?;

    if body.status != "success" {
        return Err(anyhow::anyhow!("Zerodha status check failed: {}", body.message.unwrap_or_default()));
    }

    let detail = body.data
        .and_then(|v| v.into_iter().last())
        .ok_or_else(|| anyhow::anyhow!("Zerodha: no order details returned"))?;

    Ok(OrderStatusResult {
        order_status: detail.status.unwrap_or_else(|| "UNKNOWN".into()),
        average_traded_price: detail.average_price,
    })
}
