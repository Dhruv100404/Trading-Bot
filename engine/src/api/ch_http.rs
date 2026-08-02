use anyhow::Result;
use serde_json::Value;
use std::collections::HashMap;

pub async fn query_json(
    client: &reqwest::Client,
    ch_url: &str,
    sql: &str,
    params: HashMap<String, String>,
) -> Result<Vec<Value>> {
    let full_sql = format!("{} FORMAT JSONEachRow", sql);
    let mut url = reqwest::Url::parse(ch_url)?;
    for (key, value) in &params {
        url.query_pairs_mut()
            .append_pair(&format!("param_{}", key), value);
    }

    let resp = client
        .post(url)
        .body(full_sql)
        .send()
        .await?
        .error_for_status()?;
    let body = resp.text().await?;

    body.lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str::<Value>(line).map_err(anyhow::Error::from))
        .collect::<Result<Vec<_>>>()
}
