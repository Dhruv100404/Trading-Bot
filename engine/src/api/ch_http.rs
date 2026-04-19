use anyhow::Result;
use serde_json::Value;
use std::collections::HashMap;

/// Query ClickHouse via its HTTP interface and return each row as a `serde_json::Value`.
///
/// * `ch_url`  – base URL of the ClickHouse HTTP endpoint, e.g. `http://localhost:8123`
/// * `sql`     – SQL query; may contain `{param_name:Type}` placeholders
/// * `params`  – values for those placeholders; passed as `param_<name>=<value>` query params
///
/// `FORMAT JSONEachRow` is appended automatically so every response line is one JSON object.
/// Execute a ClickHouse statement that returns no rows (DELETE, ALTER, TRUNCATE, etc.).
pub async fn ch_exec(
    client: &reqwest::Client,
    ch_url: &str,
    sql: &str,
    params: HashMap<String, String>,
) -> Result<()> {
    let mut url = reqwest::Url::parse(ch_url)?;
    for (k, v) in &params {
        url.query_pairs_mut().append_pair(&format!("param_{}", k), v);
    }
    client.post(url).body(sql.to_string()).send().await?.error_for_status()?;
    Ok(())
}

pub async fn query_json(
    client: &reqwest::Client,
    ch_url: &str,
    sql: &str,
    params: HashMap<String, String>,
) -> Result<Vec<Value>> {
    let full_sql = format!("{} FORMAT JSONEachRow", sql);

    let mut url = reqwest::Url::parse(ch_url)?;
    for (k, v) in &params {
        url.query_pairs_mut().append_pair(&format!("param_{}", k), v);
    }

    let resp = client.post(url).body(full_sql).send().await?.error_for_status()?;
    let body = resp.text().await?;

    let rows = body
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str::<Value>(l).map_err(anyhow::Error::from))
        .collect::<Result<Vec<_>>>()?;

    Ok(rows)
}
