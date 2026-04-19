use std::collections::HashSet;

const MIS5_URL: &str =
    "https://docs.google.com/spreadsheets/d/1fLTsNpFJPK349RTjs0GRSXJZD-5soCUkZt9eSMTJ2m4/export?format=csv";

/// Fetch the Zerodha MIS leverage sheet daily and return symbols with MIS Multiplier >= 5.
/// CSV columns: ISIN, Symbol, BSE Symbol, Var+ELM+Adhoc, MIS Margin(%), MIS Multiplier, ...
/// Column index 5 (0-based) = MIS Multiplier. Value "5" = 5x leverage.
pub async fn fetch_mis5_symbols() -> HashSet<String> {
    let client = match reqwest::ClientBuilder::new()
        .timeout(std::time::Duration::from_secs(20))
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            tracing::error!("[MIS5] Failed to build HTTP client: {}", e);
            return HashSet::new();
        }
    };

    let text = match client.get(MIS5_URL).send().await {
        Ok(resp) => match resp.text().await {
            Ok(t) => t,
            Err(e) => {
                tracing::error!("[MIS5] Failed to read response body: {}", e);
                return HashSet::new();
            }
        },
        Err(e) => {
            tracing::error!("[MIS5] Failed to fetch MIS leverage sheet: {}", e);
            return HashSet::new();
        }
    };

    let mut symbols = HashSet::new();
    let mut skipped = 0u32;

    for line in text.lines() {
        let cols: Vec<&str> = line.split(',').collect();
        // Need at least 6 columns; skip header rows (ISIN column = "ISIN" or empty)
        if cols.len() < 6 { continue; }
        let isin = cols[0].trim();
        let symbol = cols[1].trim();
        let multiplier = cols[5].trim();

        // Skip header/meta rows
        if isin == "ISIN" || isin.is_empty() || symbol.is_empty() { continue; }
        // Skip rows with N/A or non-numeric multiplier
        if multiplier == "#N/A" || multiplier.is_empty() { skipped += 1; continue; }

        match multiplier.parse::<f32>() {
            Ok(m) if m >= 5.0 => { symbols.insert(symbol.to_string()); }
            Ok(_) => { skipped += 1; } // < 5x — exclude
            Err(_) => { skipped += 1; }
        }
    }

    tracing::info!("[MIS5] ✅ {} symbols with 5x MIS margin ({} excluded/lower leverage)", symbols.len(), skipped);
    symbols
}
