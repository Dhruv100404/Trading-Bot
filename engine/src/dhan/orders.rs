use serde::{Deserialize, Serialize};
use anyhow::Result;
use crate::dhan::client::DhanClient;
use crate::types::Direction;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct OrderRequest {
    dhan_client_id: String,
    transaction_type: &'static str,
    exchange_segment: &'static str,
    product_type: &'static str,
    order_type: String,
    security_id: String,
    quantity: u32,
    price: f32,
    #[serde(skip_serializing_if = "is_zero")]
    trigger_price: f32,
    validity: &'static str,
}

fn is_zero(v: &f32) -> bool { *v == 0.0 }

#[derive(Deserialize, Debug)]
#[serde(rename_all = "camelCase")]
pub struct OrderResponse {
    pub order_id: Option<String>,
    #[allow(dead_code)]
    pub order_status: Option<String>,
    pub remarks: Option<String>,
}

/// Place an entry order on Dhan.
///
/// `limit_price == 0.0` → MARKET order (Dhan converts to MPP-limited LIMIT internally).
/// `limit_price  > 0.0` → explicit LIMIT order at that price (used for fallback retries).
///
/// Happy path: caller passes 0.0 (MARKET). If the MARKET order stays PENDING after
/// quick polling, the caller cancels it and retries with an explicit limit_price at
/// ±0.3% slippage — controlling worst-case fill price on the retry only.
pub async fn place_order(
    client: &DhanClient,
    security_id: &str,
    direction: &Direction,
    quantity: u32,
    limit_price: f32,
    endpoint: &str,
) -> Result<String> {
    let tx_type = match direction { Direction::Buy => "BUY", Direction::Sell => "SELL" };
    // Use MARKET when no explicit price supplied; LIMIT for fallback retries.
    let (order_type, price) = if limit_price == 0.0 {
        ("MARKET".to_string(), 0.0f32)
    } else {
        ("LIMIT".to_string(), limit_price)
    };
    let req = OrderRequest {
        dhan_client_id: client.client_id.clone(),
        transaction_type: tx_type,
        exchange_segment: "NSE_EQ",
        product_type: "INTRADAY",
        order_type,
        security_id: security_id.to_string(),
        quantity,
        price,
        trigger_price: 0.0,
        validity: "DAY",
    };

    let resp: OrderResponse = client.post(endpoint)
        .json(&req)
        .send().await?
        .json().await?;

    resp.order_id.ok_or_else(|| anyhow::anyhow!("No order_id in response: {:?}", resp.remarks))
}

#[derive(Deserialize, Debug)]
#[serde(rename_all = "camelCase")]
pub struct OrderStatusResponse {
    #[allow(dead_code)] pub order_id: Option<String>,
    pub order_status: Option<String>,
    /// Average fill price — populated once order is TRADED
    #[serde(default)]
    pub average_traded_price: f32,
    #[allow(dead_code)] pub remarks: Option<String>,
}

/// Round a price to NSE equity tick size (₹0.05).
fn round_tick(price: f32) -> f32 {
    (price * 20.0).round() / 20.0
}

/// Place a protective stop-loss order on Dhan.
///
/// Tries SL-M first. If the exchange rejects it (some stocks don't support SL-M),
/// falls back to SL-Limit with a limit price 0.5% worse than the trigger.
///
/// `position_direction` is the direction of the open position (not the SL order).
/// For a SELL position the SL is a BUY order, and vice versa.
pub async fn place_sl_order(
    client: &DhanClient,
    security_id: &str,
    position_direction: &Direction,
    quantity: u32,
    trigger_price: f32,
    endpoint: &str,
) -> Result<String> {
    let tx_type = match position_direction {
        Direction::Buy  => "SELL",
        Direction::Sell => "BUY",
    };
    let trigger = round_tick(trigger_price);

    // Attempt 1: SL-M (stop-loss market)
    let req = OrderRequest {
        dhan_client_id: client.client_id.clone(),
        transaction_type: tx_type,
        exchange_segment: "NSE_EQ",
        product_type: "INTRADAY",
        order_type: "STOP_LOSS_MARKET".to_string(),
        security_id: security_id.to_string(),
        quantity,
        price: 0.0,
        trigger_price: trigger,
        validity: "DAY",
    };

    let resp_bytes = client.post(endpoint)
        .json(&req)
        .send().await?
        .bytes().await?;

    // Try to parse response; check if order was placed or rejected
    if let Ok(resp) = serde_json::from_slice::<OrderResponse>(&resp_bytes) {
        if let Some(ref oid) = resp.order_id {
            tracing::info!("[SL] Dhan SL-M placed: {} trigger={:.2} order_id={}", security_id, trigger, oid);
            return Ok(oid.clone());
        }
        let remarks = resp.remarks.as_deref().unwrap_or("unknown");
        tracing::warn!("[SL] Dhan SL-M rejected for {}: {} — trying SL-Limit", security_id, remarks);
    } else {
        let body = String::from_utf8_lossy(&resp_bytes);
        tracing::warn!("[SL] Dhan SL-M parse failed for {}: {} — trying SL-Limit", security_id, body);
    }

    // Attempt 2: SL-Limit — price 0.5% worse than trigger to ensure execution
    let limit_price = match position_direction {
        Direction::Sell => round_tick(trigger * 1.005), // BUY SL: limit above trigger
        Direction::Buy  => round_tick(trigger * 0.995), // SELL SL: limit below trigger
    };

    let req_limit = OrderRequest {
        dhan_client_id: client.client_id.clone(),
        transaction_type: tx_type,
        exchange_segment: "NSE_EQ",
        product_type: "INTRADAY",
        order_type: "STOP_LOSS".to_string(),
        security_id: security_id.to_string(),
        quantity,
        price: limit_price,
        trigger_price: trigger,
        validity: "DAY",
    };

    let resp: OrderResponse = client.post(endpoint)
        .json(&req_limit)
        .send().await?
        .json().await?;

    match resp.order_id {
        Some(oid) => {
            tracing::info!("[SL] Dhan SL-Limit placed: {} trigger={:.2} limit={:.2} order_id={}", security_id, trigger, limit_price, oid);
            Ok(oid)
        }
        None => Err(anyhow::anyhow!("Dhan SL-Limit also rejected for {}: {:?}", security_id, resp.remarks)),
    }
}

/// Cancel a pending order on Dhan.
/// Returns Ok(true) if cancelled, Ok(false) if order was already traded/gone.
pub async fn cancel_order(client: &DhanClient, order_id: &str) -> Result<bool> {
    let path = format!("/orders/{}", order_id);
    let resp = client.delete(&path).send().await?;
    let status = resp.status();
    if status.is_success() {
        tracing::info!("[SL] Dhan order {} cancelled", order_id);
        return Ok(true);
    }
    // 400/404 typically means order already traded or not found
    let body: serde_json::Value = resp.json().await.unwrap_or(serde_json::json!({}));
    let remarks = body.get("remarks").and_then(|r| r.as_str())
        .or_else(|| body.get("message").and_then(|m| m.as_str()))
        .unwrap_or("unknown");
    if status.as_u16() == 400 || status.as_u16() == 404 {
        tracing::info!("[SL] Dhan order {} cancel skipped (already traded/gone): {}", order_id, remarks);
        return Ok(false);
    }
    Err(anyhow::anyhow!("Dhan cancel order {} failed (HTTP {}): {}", order_id, status, remarks))
}

/// Fetch the current status of a placed order from Dhan.
/// Dhan returns a JSON array for this endpoint; we pick the first element.
/// order_status values: TRANSIT, PENDING, PARTIALLY_TRADED, TRADED, REJECTED, CANCELLED, EXPIRED
pub async fn get_order_status(client: &DhanClient, order_id: &str) -> Result<OrderStatusResponse> {
    let path = format!("/orders/{}", order_id);
    let bytes = client.get(&path).send().await?.bytes().await?;

    // Try array first (Dhan returns [{...}] for this endpoint)
    if let Ok(mut arr) = serde_json::from_slice::<Vec<OrderStatusResponse>>(&bytes) {
        if let Some(first) = arr.pop() {
            return Ok(first);
        }
        return Err(anyhow::anyhow!("Empty order status array for order_id={}", order_id));
    }

    // Fallback: single object
    let resp: OrderStatusResponse = serde_json::from_slice(&bytes)
        .map_err(|e| anyhow::anyhow!("order status decode failed for {}: {} — body: {}", order_id, e, String::from_utf8_lossy(&bytes)))?;
    Ok(resp)
}
