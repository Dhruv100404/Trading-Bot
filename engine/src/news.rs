use anyhow::{anyhow, Context, Result};
use chrono::{DateTime, Duration as ChronoDuration, FixedOffset, NaiveDate};
use reqwest::header::{
    HeaderMap, HeaderValue, ACCEPT, ACCEPT_LANGUAGE, CACHE_CONTROL, COOKIE, REFERER, SET_COOKIE,
    USER_AGENT,
};
use scraper::{Html, Selector};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NewsSourceKind {
    Rss,
    MoneycontrolHtml,
}

impl NewsSourceKind {
    fn from_str(value: &str) -> Option<Self> {
        match value.trim().to_ascii_lowercase().as_str() {
            "rss" => Some(Self::Rss),
            "html" | "moneycontrol_html" | "moneycontrol" => Some(Self::MoneycontrolHtml),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Rss => "rss",
            Self::MoneycontrolHtml => "moneycontrol_html",
        }
    }
}

#[derive(Debug, Clone)]
pub struct NewsSource {
    pub source: String,
    pub kind: NewsSourceKind,
    pub category: String,
    pub url: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NewsArticle {
    pub article_id: String,
    pub source: String,
    pub source_kind: String,
    pub category: String,
    pub title: String,
    pub url: String,
    pub summary: String,
    pub published_at: Option<String>,
    pub fetched_at: String,
}

#[derive(Debug, Clone)]
pub struct WatchlistCompany {
    pub symbol: String,
    pub security_id: String,
    pub company_name: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NewsMention {
    pub article_id: String,
    pub symbol: String,
    pub security_id: String,
    pub company_name: String,
    pub match_confidence: f32,
    pub matched_text: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NewsScore {
    pub article_id: String,
    pub symbol: String,
    pub sentiment: f32,
    pub impact_score: f32,
    pub direction: String,
    pub horizon: String,
    pub confidence: f32,
    pub reason: String,
    pub model: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NseLargeDeal {
    pub deal_id: String,
    pub deal_type: String,
    pub deal_date: String,
    pub deal_date_raw: String,
    pub symbol: String,
    pub security_name: String,
    pub client_name: String,
    pub side: String,
    pub quantity: f64,
    pub price: f64,
    pub value_lakh: f64,
    pub source_url: String,
    pub fetched_at: String,
}

#[derive(Debug, Clone)]
struct SymbolAlias {
    symbol: String,
    security_id: String,
    company_name: String,
    matched_text: String,
    normalized: String,
    confidence: f32,
}

const NSE_ARCHIVES_URL: &str = "https://www.nseindia.com/report-detail/display-bulk-and-block-deals";
const NSE_LARGE_DEALS_URL: &str = "https://www.nseindia.com/market-data/large-deals";
const NSE_BULK_DEALS_API: &str = "https://www.nseindia.com/api/historical/bulk-deals";
const NSE_BLOCK_DEALS_API: &str = "https://www.nseindia.com/api/historical/block-deals";
const NSE_BULK_BLOCK_HISTORY_API: &str =
    "https://www.nseindia.com/api/historicalOR/bulk-block-short-deals";
const NSE_BULK_REPORT_CSV: &str = "https://nsearchives.nseindia.com/content/equities/bulk.csv";
const NSE_BLOCK_REPORT_CSV: &str = "https://nsearchives.nseindia.com/content/equities/block.csv";

pub fn default_sources() -> Vec<NewsSource> {
    vec![
        NewsSource {
            source: "moneycontrol".to_string(),
            kind: NewsSourceKind::MoneycontrolHtml,
            category: "markets".to_string(),
            url: "https://www.moneycontrol.com/news/business/markets/".to_string(),
        },
        NewsSource {
            source: "moneycontrol".to_string(),
            kind: NewsSourceKind::MoneycontrolHtml,
            category: "stocks".to_string(),
            url: "https://www.moneycontrol.com/news/business/stocks/".to_string(),
        },
        NewsSource {
            source: "yahoo_finance".to_string(),
            kind: NewsSourceKind::Rss,
            category: "latest".to_string(),
            url: "https://finance.yahoo.com/news/rssindex".to_string(),
        },
    ]
}

pub fn configured_sources() -> Vec<NewsSource> {
    let Ok(raw) = std::env::var("NEWS_SOURCES") else {
        return default_sources();
    };
    if raw.trim().is_empty() {
        return default_sources();
    }

    let sources: Vec<NewsSource> = raw
        .split(';')
        .filter_map(|entry| {
            let parts: Vec<&str> = entry.split('|').map(str::trim).collect();
            if parts.len() != 4 {
                return None;
            }
            Some(NewsSource {
                source: parts[0].to_string(),
                kind: NewsSourceKind::from_str(parts[1])?,
                category: parts[2].to_string(),
                url: parts[3].to_string(),
            })
        })
        .collect();

    if sources.is_empty() {
        tracing::warn!("[NEWS] NEWS_SOURCES was set but no valid sources parsed; using defaults");
        default_sources()
    } else {
        sources
    }
}

pub async fn fetch_articles(max_per_source: usize) -> Result<Vec<NewsArticle>> {
    let client = reqwest::Client::builder()
        .user_agent("swing-atlas-news/1.0")
        .timeout(std::time::Duration::from_secs(15))
        .build()?;
    fetch_articles_from_sources(&client, &configured_sources(), max_per_source).await
}

pub async fn fetch_articles_from_sources(
    client: &reqwest::Client,
    sources: &[NewsSource],
    max_per_source: usize,
) -> Result<Vec<NewsArticle>> {
    let mut all = Vec::new();
    for source in sources {
        let fetched = match source.kind {
            NewsSourceKind::Rss => fetch_rss_source(client, source, max_per_source).await,
            NewsSourceKind::MoneycontrolHtml => {
                fetch_moneycontrol_html_source(client, source, max_per_source).await
            }
        };

        match fetched {
            Ok(mut rows) => all.append(&mut rows),
            Err(err) => tracing::warn!(
                "[NEWS] source={} category={} fetch failed: {}",
                source.source,
                source.category,
                err
            ),
        }
    }

    let mut seen = HashSet::new();
    all.retain(|article| seen.insert(article.url.clone()));
    Ok(all)
}

pub async fn fetch_nse_large_deals(lookback_days: i64) -> Result<Vec<NseLargeDeal>> {
    let lookback_days = lookback_days.clamp(0, 30);
    let today = crate::types::now_ist().date_naive();
    let from = today - ChronoDuration::days(lookback_days);
    let from_s = from.format("%d-%m-%Y").to_string();
    let to_s = today.format("%d-%m-%Y").to_string();
    let fetched_at = now_sql();

    let mut headers = HeaderMap::new();
    headers.insert(
        USER_AGENT,
        HeaderValue::from_static(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 \
             (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        ),
    );
    headers.insert(ACCEPT, HeaderValue::from_static("application/json,text/plain,*/*"));
    headers.insert(ACCEPT_LANGUAGE, HeaderValue::from_static("en-US,en;q=0.9"));
    headers.insert(CACHE_CONTROL, HeaderValue::from_static("no-cache"));
    headers.insert(REFERER, HeaderValue::from_static(NSE_ARCHIVES_URL));

    let client = reqwest::Client::builder()
        .default_headers(headers)
        .timeout(std::time::Duration::from_secs(20))
        .build()?;

    let warmup = client
        .get(NSE_ARCHIVES_URL)
        .send()
        .await
        .context("NSE cookie warm-up failed")?;
    if !warmup.status().is_success() {
        return Err(anyhow!("NSE cookie warm-up returned {}", warmup.status()));
    }
    let cookie_header = warmup
        .headers()
        .get_all(SET_COOKIE)
        .iter()
        .filter_map(|value| value.to_str().ok())
        .filter_map(|value| value.split(';').next())
        .filter(|value| !value.trim().is_empty())
        .collect::<Vec<_>>()
        .join("; ");

    let mut rows = Vec::new();
    let mut historical_errors = Vec::new();
    for (deal_type, option_type) in [
        ("BULK", "bulk_deals"),
        ("BLOCK", "block_deals"),
    ] {
        match fetch_nse_historical_deal_csv(
            &client,
            deal_type,
            option_type,
            &from_s,
            &to_s,
            &fetched_at,
            &cookie_header,
        )
        .await
        {
            Ok(mut fetched) => rows.append(&mut fetched),
            Err(err) => historical_errors.push(format!("{deal_type}: {err}")),
        }
    }

    // Keep the latest-day archive files as an emergency fallback. The primary
    // historicalOR endpoint is requested with csv=true because its JSON view is
    // capped, while the CSV contains the complete selected date range.
    if rows.is_empty() || !historical_errors.is_empty() {
        let mut fallback_rows = Vec::new();
        let mut fallback_errors = Vec::new();
        for (deal_type, csv_url) in [
            ("BULK", NSE_BULK_REPORT_CSV),
            ("BLOCK", NSE_BLOCK_REPORT_CSV),
        ] {
            match fetch_nse_daily_report(&client, deal_type, csv_url, &fetched_at).await {
                Ok(mut fetched) => fallback_rows.append(&mut fetched),
                Err(err) => fallback_errors.push(format!("{deal_type}: {err}")),
            }
        }
        if !fallback_rows.is_empty() {
            tracing::info!(
                "[NEWS] NSE historical endpoint unavailable; loaded {} rows from official daily CSV reports",
                fallback_rows.len()
            );
            rows.extend(fallback_rows);
        } else if rows.is_empty() && !fallback_errors.is_empty() {
            return Err(anyhow!(
                "NSE historical fetch failed ({}); daily report fallback failed ({})",
                historical_errors.join("; "),
                fallback_errors.join("; ")
            ));
        }
    }

    let mut seen = HashSet::new();
    rows.retain(|deal| seen.insert(deal.deal_id.clone()));
    Ok(rows)
}

#[derive(Debug, Deserialize)]
struct NseDailyReportRow {
    #[serde(rename = "Date", alias = "Date ", default)]
    date: String,
    #[serde(rename = "Symbol", alias = "Symbol ", default)]
    symbol: String,
    #[serde(rename = "Security Name", alias = "Security Name ", default)]
    security_name: String,
    #[serde(rename = "Client Name", alias = "Client Name ", default)]
    client_name: String,
    #[serde(rename = "Buy/Sell", alias = "Buy / Sell", alias = "Buy / Sell ", default)]
    side: String,
    #[serde(rename = "Quantity Traded", alias = "Quantity Traded ", default)]
    quantity: String,
    #[serde(
        rename = "Trade Price / Wght. Avg. Price",
        alias = "Trade Price / Wght. Avg. Price ",
        default
    )]
    price: String,
}

async fn fetch_nse_historical_deal_csv(
    client: &reqwest::Client,
    deal_type: &str,
    option_type: &str,
    from: &str,
    to: &str,
    fetched_at: &str,
    cookie_header: &str,
) -> Result<Vec<NseLargeDeal>> {
    let mut request = client
        .get(NSE_BULK_BLOCK_HISTORY_API)
        .header(ACCEPT, "text/csv,*/*;q=0.8")
        .query(&[
            ("optionType", option_type),
            ("from", from),
            ("to", to),
            ("csv", "true"),
        ]);
    if !cookie_header.is_empty() {
        request = request.header(COOKIE, cookie_header);
    }
    let body = request
        .send()
        .await?
        .error_for_status()?
        .text()
        .await?;
    parse_nse_daily_report(&body, deal_type, fetched_at)
}

async fn fetch_nse_daily_report(
    client: &reqwest::Client,
    deal_type: &str,
    csv_url: &str,
    fetched_at: &str,
) -> Result<Vec<NseLargeDeal>> {
    let body = client
        .get(csv_url)
        .header(ACCEPT, "text/csv,*/*;q=0.8")
        .send()
        .await?
        .error_for_status()?
        .text()
        .await?;
    parse_nse_daily_report(&body, deal_type, fetched_at)
}

fn parse_nse_daily_report(
    body: &str,
    deal_type: &str,
    fetched_at: &str,
) -> Result<Vec<NseLargeDeal>> {
    let body = body.trim_start_matches('\u{feff}');
    let mut reader = csv::ReaderBuilder::new()
        .flexible(true)
        .trim(csv::Trim::All)
        .from_reader(body.as_bytes());
    let mut out = Vec::new();
    for parsed in reader.deserialize::<NseDailyReportRow>() {
        let row = parsed?;
        let symbol = row.symbol.trim().to_ascii_uppercase();
        if symbol.is_empty() || symbol == "NO RECORDS" {
            continue;
        }
        let deal_date_raw = row.date.trim().to_string();
        let Some(parsed_date) = parse_nse_date(&deal_date_raw) else {
            continue;
        };
        let quantity = parse_number(&row.quantity).unwrap_or(0.0);
        let price = parse_number(&row.price).unwrap_or(0.0);
        let side = row.side.trim().to_ascii_uppercase();
        let quantity_key = format!("{quantity:.0}");
        let price_key = format!("{price:.4}");
        let deal_id = hash_id(&[
            deal_type,
            &deal_date_raw,
            &symbol,
            row.client_name.trim(),
            &side,
            &quantity_key,
            &price_key,
        ]);
        out.push(NseLargeDeal {
            deal_id,
            deal_type: deal_type.to_string(),
            deal_date: parsed_date.format("%Y-%m-%d").to_string(),
            deal_date_raw,
            symbol,
            security_name: row.security_name.trim().to_string(),
            client_name: row.client_name.trim().to_string(),
            side,
            quantity,
            price,
            value_lakh: round2_f64(quantity * price / 100_000.0),
            source_url: NSE_ARCHIVES_URL.to_string(),
            fetched_at: fetched_at.to_string(),
        });
    }
    Ok(out)
}

async fn fetch_nse_deal_endpoint(
    client: &reqwest::Client,
    deal_type: &str,
    api_url: &str,
    from: &str,
    to: &str,
    fetched_at: &str,
    cookie_header: &str,
) -> Result<Vec<NseLargeDeal>> {
    let mut request = client.get(api_url).query(&[("from", from), ("to", to)]);
    if !cookie_header.is_empty() {
        request = request.header(COOKIE, cookie_header);
    }

    let json = request
        .send()
        .await?
        .error_for_status()?
        .json::<Value>()
        .await?;
    Ok(parse_nse_large_deals_json(
        &json,
        deal_type,
        NSE_LARGE_DEALS_URL,
        fetched_at,
    ))
}

fn parse_nse_large_deals_json(
    json: &Value,
    deal_type: &str,
    source_url: &str,
    fetched_at: &str,
) -> Vec<NseLargeDeal> {
    let mut row_values = Vec::new();
    collect_json_object_rows(json, &mut row_values);

    let today = crate::types::now_ist().date_naive();
    let mut out = Vec::new();
    for row in row_values {
        let symbol = json_string(
            row,
            &["BD_SYMBOL", "SYMBOL", "symbol", "securitySymbol", "SECURITY_SYMBOL"],
        )
        .unwrap_or_default()
        .trim()
        .to_ascii_uppercase();
        if symbol.is_empty() {
            continue;
        }

        let deal_date_raw = json_string(
            row,
            &["BD_DT_DATE", "DATE", "date", "tradeDate", "TRADE_DATE", "mTIMESTAMP"],
        )
        .unwrap_or_default();
        let deal_date = parse_nse_date(&deal_date_raw)
            .unwrap_or(today)
            .format("%Y-%m-%d")
            .to_string();
        let security_name = json_string(
            row,
            &["BD_SCRIP_NAME", "SCRIP_NAME", "securityName", "SECURITY_NAME", "companyName", "name"],
        )
        .unwrap_or_default();
        let client_name = json_string(
            row,
            &["BD_CLIENT_NAME", "CLIENT_NAME", "clientName", "client", "CLIENT"],
        )
        .unwrap_or_default();
        let side = json_string(row, &["BD_BUY_SELL", "BUY_SELL", "buySell", "side", "SIDE"])
            .unwrap_or_else(|| "NA".to_string())
            .trim()
            .to_ascii_uppercase();
        let quantity = json_f64(
            row,
            &["BD_QTY_TRD", "QTY_TRD", "quantity", "quantityTraded", "QUANTITY_TRADED", "trdQty"],
        )
        .unwrap_or(0.0);
        let price = json_f64(
            row,
            &["BD_TP_WATP", "TP_WATP", "price", "tradePrice", "AVG_PRICE", "wtdAvgPrice"],
        )
        .unwrap_or(0.0);
        let supplied_value = json_f64(
            row,
            &["BD_VAL_TRADED", "value", "valueLakh", "VALUE_LAKH", "turnoverLacs", "TURNOVER_LACS"],
        );
        let value_lakh = supplied_value
            .map(|value| if value > 1_000_000.0 { value / 100_000.0 } else { value })
            .unwrap_or_else(|| quantity * price / 100_000.0);
        let quantity_key = format!("{:.0}", quantity);
        let price_key = format!("{:.4}", price);
        let deal_id = hash_id(&[
            deal_type,
            &deal_date_raw,
            &symbol,
            &client_name,
            &side,
            &quantity_key,
            &price_key,
        ]);

        out.push(NseLargeDeal {
            deal_id,
            deal_type: deal_type.to_string(),
            deal_date,
            deal_date_raw,
            symbol,
            security_name,
            client_name,
            side,
            quantity,
            price,
            value_lakh: round2_f64(value_lakh),
            source_url: source_url.to_string(),
            fetched_at: fetched_at.to_string(),
        });
    }

    out
}

async fn fetch_rss_source(
    client: &reqwest::Client,
    source: &NewsSource,
    max_per_source: usize,
) -> Result<Vec<NewsArticle>> {
    let body = client
        .get(&source.url)
        .send()
        .await?
        .error_for_status()?
        .text()
        .await?;
    let channel = rss::Channel::read_from(body.as_bytes())?;
    let fetched_at = now_sql();

    let mut out = Vec::new();
    for item in channel.items().iter().take(max_per_source) {
        let title = clean_text(item.title().unwrap_or_default());
        let url = item
            .link()
            .or_else(|| item.guid().map(|guid| guid.value()))
            .unwrap_or_default()
            .to_string();
        let url = canonical_url(&url);
        if title.len() < 8 || url.is_empty() {
            continue;
        }
        let summary = clean_text(item.description().unwrap_or_default());
        let published_at = item.pub_date().and_then(parse_pub_date);
        out.push(NewsArticle {
            article_id: article_id(&source.source, &url),
            source: source.source.clone(),
            source_kind: source.kind.as_str().to_string(),
            category: source.category.clone(),
            title,
            url,
            summary: truncate_chars(&summary, 700),
            published_at,
            fetched_at: fetched_at.clone(),
        });
    }
    Ok(out)
}

async fn fetch_moneycontrol_html_source(
    client: &reqwest::Client,
    source: &NewsSource,
    max_per_source: usize,
) -> Result<Vec<NewsArticle>> {
    let body = client
        .get(&source.url)
        .send()
        .await?
        .error_for_status()?
        .text()
        .await?;
    let document = Html::parse_document(&body);
    let selector =
        Selector::parse("a[href]").map_err(|err| anyhow!("selector parse failed: {err:?}"))?;
    let fetched_at = now_sql();
    let mut seen = HashSet::new();
    let mut out = Vec::new();

    for element in document.select(&selector) {
        let Some(href) = element.value().attr("href") else {
            continue;
        };
        let Some(url) = resolve_url(&source.url, href) else {
            continue;
        };
        let url = canonical_url(&url);
        if !url.contains("moneycontrol.com/news/")
            || !url.ends_with(".html")
            || url.contains("/photos/")
        {
            continue;
        }

        let title = clean_text(&element.text().collect::<Vec<_>>().join(" "));
        if title.len() < 25 || !seen.insert(url.clone()) {
            continue;
        }

        out.push(NewsArticle {
            article_id: article_id(&source.source, &url),
            source: source.source.clone(),
            source_kind: source.kind.as_str().to_string(),
            category: source.category.clone(),
            title,
            url,
            summary: String::new(),
            published_at: None,
            fetched_at: fetched_at.clone(),
        });

        if out.len() >= max_per_source {
            break;
        }
    }

    Ok(out)
}

pub fn resolve_mentions(
    articles: &[NewsArticle],
    companies: &[WatchlistCompany],
) -> Vec<NewsMention> {
    let aliases = build_aliases(companies);
    let mut mentions = Vec::new();

    for article in articles {
        let text = normalize_for_match(&format!("{} {}", article.title, article.summary));
        let title = normalize_for_match(&article.title);
        let text_wrapped = format!(" {} ", text);
        let title_wrapped = format!(" {} ", title);
        let mut best_by_symbol: HashMap<String, NewsMention> = HashMap::new();

        for alias in &aliases {
            if !contains_alias(&text_wrapped, &alias.normalized) {
                continue;
            }
            let mut confidence = alias.confidence;
            if contains_alias(&title_wrapped, &alias.normalized) {
                confidence = (confidence + 0.07).min(0.98);
            }
            let mention = NewsMention {
                article_id: article.article_id.clone(),
                symbol: alias.symbol.clone(),
                security_id: alias.security_id.clone(),
                company_name: alias.company_name.clone(),
                match_confidence: confidence,
                matched_text: alias.matched_text.clone(),
            };
            best_by_symbol
                .entry(alias.symbol.clone())
                .and_modify(|existing| {
                    if mention.match_confidence > existing.match_confidence {
                        *existing = mention.clone();
                    }
                })
                .or_insert(mention);
        }

        let mut article_mentions: Vec<NewsMention> = best_by_symbol.into_values().collect();
        article_mentions.sort_by(|a, b| {
            b.match_confidence
                .partial_cmp(&a.match_confidence)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| a.symbol.cmp(&b.symbol))
        });
        article_mentions.truncate(8);
        mentions.extend(article_mentions);
    }

    mentions
}

pub fn score_mentions(articles: &[NewsArticle], mentions: &[NewsMention]) -> Vec<NewsScore> {
    let article_by_id: HashMap<&str, &NewsArticle> = articles
        .iter()
        .map(|article| (article.article_id.as_str(), article))
        .collect();

    mentions
        .iter()
        .filter_map(|mention| {
            let article = article_by_id.get(mention.article_id.as_str())?;
            Some(score_article_for_symbol(article, mention))
        })
        .collect()
}

fn score_article_for_symbol(article: &NewsArticle, mention: &NewsMention) -> NewsScore {
    let text = normalize_for_match(&format!("{} {}", article.title, article.summary));
    let bullish = keyword_hits(&text, BULLISH_KEYWORDS);
    let bearish = keyword_hits(&text, BEARISH_KEYWORDS);
    let impact = keyword_hits(&text, IMPACT_KEYWORDS);
    let intraday = keyword_hits(&text, INTRADAY_KEYWORDS);
    let swing = keyword_hits(&text, SWING_KEYWORDS);

    let bull_count = bullish.len() as f32;
    let bear_count = bearish.len() as f32;
    let signed = bull_count - bear_count;
    let total_directional = (bull_count + bear_count).max(1.0);
    let sentiment = (signed / total_directional).clamp(-1.0, 1.0);

    let impact_score = (1.0
        + impact.len() as f32 * 0.38
        + (bull_count + bear_count) * 0.24
        + mention.match_confidence * 0.75)
        .clamp(0.0, 5.0);
    let confidence = (0.32
        + mention.match_confidence * 0.35
        + impact.len() as f32 * 0.05
        + (bull_count + bear_count) * 0.04)
        .clamp(0.0, 0.95);

    let direction = if sentiment > 0.18 {
        "BULLISH"
    } else if sentiment < -0.18 {
        "BEARISH"
    } else if impact_score >= 2.2 {
        "WATCH"
    } else {
        "NEUTRAL"
    };
    let horizon = if !intraday.is_empty() {
        "intraday"
    } else if !swing.is_empty() {
        "swing"
    } else {
        "1d"
    };

    NewsScore {
        article_id: article.article_id.clone(),
        symbol: mention.symbol.clone(),
        sentiment: round2(sentiment),
        impact_score: round2(impact_score),
        direction: direction.to_string(),
        horizon: horizon.to_string(),
        confidence: round2(confidence),
        reason: build_reason(&bullish, &bearish, &impact),
        model: "news_heuristic_v1".to_string(),
    }
}

fn build_aliases(companies: &[WatchlistCompany]) -> Vec<SymbolAlias> {
    let mut out = Vec::new();
    let by_symbol: HashMap<String, &WatchlistCompany> = companies
        .iter()
        .map(|company| (company.symbol.to_ascii_uppercase(), company))
        .collect();

    for company in companies {
        let symbol = company.symbol.to_ascii_uppercase();
        add_alias(&mut out, company, &symbol, 0.88);

        let cleaned_company = clean_company_name(&company.company_name);
        if cleaned_company.len() >= 5 {
            add_alias(&mut out, company, &cleaned_company, 0.82);
        }

        let words = cleaned_company.split_whitespace().collect::<Vec<_>>();
        if words.len() >= 2 && words[..2].iter().all(|word| is_distinctive_alias_word(word)) {
            add_alias(&mut out, company, &words[..2].join(" "), 0.72);
        }
        // Do not add a company's first word by itself. Group prefixes such as
        // ICICI, TATA, BAJAJ, ADANI, ORACLE, and BANK OF routinely identify a
        // broker, parent, or different listed company in the same headline.
    }

    for (symbol, aliases) in manual_aliases() {
        let Some(company) = by_symbol.get(symbol) else {
            continue;
        };
        for alias in aliases {
            add_alias(&mut out, company, alias, 0.9);
        }
    }

    let mut seen = HashSet::new();
    out.retain(|alias| seen.insert((alias.symbol.clone(), alias.normalized.clone())));
    out
}

fn add_alias(out: &mut Vec<SymbolAlias>, company: &WatchlistCompany, alias: &str, confidence: f32) {
    let normalized = normalize_for_match(alias);
    if normalized.len() < 3 || is_generic_alias(&normalized) {
        return;
    }
    out.push(SymbolAlias {
        symbol: company.symbol.to_ascii_uppercase(),
        security_id: company.security_id.clone(),
        company_name: company.company_name.clone(),
        matched_text: alias.to_string(),
        normalized,
        confidence,
    });
}

fn manual_aliases() -> Vec<(&'static str, Vec<&'static str>)> {
    vec![
        ("RELIANCE", vec!["RIL", "JIO", "JIO PLATFORMS"]),
        ("HDFCBANK", vec!["HDFC BANK"]),
        ("ICICIBANK", vec!["ICICI BANK"]),
        ("SBIN", vec!["SBI", "STATE BANK OF INDIA"]),
        ("INFY", vec!["INFOSYS"]),
        ("TCS", vec!["TATA CONSULTANCY SERVICES"]),
        ("LT", vec!["LARSEN AND TOUBRO", "L T"]),
        ("TATAMOTORS", vec!["TATA MOTORS"]),
        ("TATASTEEL", vec!["TATA STEEL"]),
        ("BAJFINANCE", vec!["BAJAJ FINANCE"]),
        ("BAJAJFINSV", vec!["BAJAJ FINSERV"]),
        ("BHARTIARTL", vec!["BHARTI AIRTEL", "AIRTEL"]),
        ("HINDUNILVR", vec!["HUL", "HINDUSTAN UNILEVER"]),
    ]
}

fn clean_company_name(company_name: &str) -> String {
    normalize_for_match(company_name)
        .split_whitespace()
        .filter(|word| {
            !matches!(
                *word,
                "LTD" | "LIMITED" | "PVT" | "PRIVATE" | "THE" | "COMPANY" | "CO" | "CORP"
                    | "CORPORATION" | "INC"
            )
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn is_generic_alias(alias: &str) -> bool {
    matches!(
        alias,
        "INDIA" | "INDIAN" | "BANK" | "FINANCE" | "FINANCIAL" | "CAPITAL" | "STEEL"
            | "POWER" | "ENERGY" | "GLOBAL" | "TECH" | "TECHNOLOGIES" | "SYSTEMS"
            | "INDUSTRIES" | "MARKETS" | "STOCK" | "STOCKS" | "NIFTY" | "SENSEX"
    )
}

fn is_distinctive_alias_word(word: &str) -> bool {
    word.len() >= 3
        && !is_generic_alias(word)
        && !matches!(word, "AND" | "FOR" | "FROM" | "OF" | "WITH")
}

fn contains_alias(wrapped_text: &str, normalized_alias: &str) -> bool {
    let wrapped_alias = format!(" {} ", normalized_alias);
    wrapped_text.contains(&wrapped_alias)
}

fn normalize_for_match(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for ch in value.chars() {
        if ch == '&' {
            out.push_str(" AND ");
        } else if ch.is_ascii_alphanumeric() {
            out.push(ch.to_ascii_uppercase());
        } else {
            out.push(' ');
        }
    }
    out.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn keyword_hits(text: &str, keywords: &[&'static str]) -> Vec<&'static str> {
    keywords
        .iter()
        .copied()
        .filter(|keyword| text.contains(&normalize_for_match(keyword)))
        .collect()
}

fn build_reason(
    bullish: &[&'static str],
    bearish: &[&'static str],
    impact: &[&'static str],
) -> String {
    let mut parts = Vec::new();
    if !bullish.is_empty() {
        parts.push(format!("bullish: {}", bullish.iter().take(3).copied().collect::<Vec<_>>().join(", ")));
    }
    if !bearish.is_empty() {
        parts.push(format!("bearish: {}", bearish.iter().take(3).copied().collect::<Vec<_>>().join(", ")));
    }
    if !impact.is_empty() {
        parts.push(format!("impact: {}", impact.iter().take(3).copied().collect::<Vec<_>>().join(", ")));
    }
    if parts.is_empty() {
        "symbol mention with no strong directional keyword".to_string()
    } else {
        parts.join(" | ")
    }
}

fn collect_json_object_rows<'a>(value: &'a Value, rows: &mut Vec<&'a Value>) {
    match value {
        Value::Array(items) => {
            if items.iter().any(Value::is_object) {
                rows.extend(items.iter().filter(|item| item.is_object()));
            } else {
                for item in items {
                    collect_json_object_rows(item, rows);
                }
            }
        }
        Value::Object(map) => {
            for child in map.values() {
                collect_json_object_rows(child, rows);
            }
        }
        _ => {}
    }
}

fn json_field<'a>(row: &'a Value, keys: &[&str]) -> Option<&'a Value> {
    let object = row.as_object()?;
    for key in keys {
        if let Some(value) = object.get(*key) {
            return Some(value);
        }
    }
    object.iter().find_map(|(actual, value)| {
        keys.iter()
            .any(|key| actual.eq_ignore_ascii_case(key))
            .then_some(value)
    })
}

fn json_string(row: &Value, keys: &[&str]) -> Option<String> {
    let value = json_field(row, keys)?;
    let raw = match value {
        Value::String(value) => value.clone(),
        Value::Number(value) => value.to_string(),
        Value::Bool(value) => value.to_string(),
        _ => return None,
    };
    let cleaned = raw
        .replace('\u{00a0}', " ")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    (!cleaned.is_empty() && cleaned != "-").then_some(cleaned)
}

fn json_f64(row: &Value, keys: &[&str]) -> Option<f64> {
    let value = json_field(row, keys)?;
    match value {
        Value::Number(value) => value.as_f64(),
        Value::String(value) => parse_number(value),
        _ => None,
    }
}

fn parse_number(value: &str) -> Option<f64> {
    let cleaned: String = value
        .chars()
        .filter(|ch| ch.is_ascii_digit() || matches!(*ch, '.' | '-'))
        .collect();
    if cleaned.is_empty() || cleaned == "-" {
        None
    } else {
        cleaned.parse::<f64>().ok()
    }
}

fn parse_nse_date(value: &str) -> Option<NaiveDate> {
    let cleaned = value.trim().replace('/', "-");
    if cleaned.is_empty() {
        return None;
    }
    for fmt in ["%d-%m-%Y", "%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"] {
        if let Ok(date) = NaiveDate::parse_from_str(&cleaned, fmt) {
            return Some(date);
        }
    }

    let parts = cleaned.split('-').collect::<Vec<_>>();
    if parts.len() == 3 {
        let day = parts[0].parse::<u32>().ok()?;
        let month = month_number(parts[1])?;
        let year = parts[2].parse::<i32>().ok()?;
        return NaiveDate::from_ymd_opt(year, month, day);
    }
    None
}

fn month_number(value: &str) -> Option<u32> {
    match value.trim().to_ascii_uppercase().get(0..3)? {
        "JAN" => Some(1),
        "FEB" => Some(2),
        "MAR" => Some(3),
        "APR" => Some(4),
        "MAY" => Some(5),
        "JUN" => Some(6),
        "JUL" => Some(7),
        "AUG" => Some(8),
        "SEP" => Some(9),
        "OCT" => Some(10),
        "NOV" => Some(11),
        "DEC" => Some(12),
        _ => None,
    }
}

fn parse_pub_date(value: &str) -> Option<String> {
    DateTime::parse_from_rfc2822(value)
        .or_else(|_| DateTime::parse_from_rfc3339(value))
        .ok()
        .map(sql_time)
}

fn sql_time(dt: DateTime<FixedOffset>) -> String {
    dt.with_timezone(&chrono_tz::Asia::Kolkata)
        .format("%Y-%m-%d %H:%M:%S")
        .to_string()
}

fn now_sql() -> String {
    crate::types::now_ist()
        .format("%Y-%m-%d %H:%M:%S")
        .to_string()
}

fn article_id(source: &str, url: &str) -> String {
    hash_id(&[source, url])
}

fn hash_id(parts: &[&str]) -> String {
    let mut hasher = Sha256::new();
    for part in parts {
        hasher.update(part.as_bytes());
        hasher.update(b"|");
    }
    hasher
        .finalize()
        .iter()
        .take(16)
        .map(|byte| format!("{:02x}", byte))
        .collect()
}

fn canonical_url(url: &str) -> String {
    url.trim()
        .split('#')
        .next()
        .unwrap_or_default()
        .split('?')
        .next()
        .unwrap_or_default()
        .trim_end_matches('/')
        .to_string()
}

fn resolve_url(base: &str, href: &str) -> Option<String> {
    if href.starts_with("http://") || href.starts_with("https://") {
        return Some(href.to_string());
    }
    reqwest::Url::parse(base)
        .ok()
        .and_then(|base_url| base_url.join(href).ok())
        .map(|url| url.to_string())
}

fn clean_text(raw: &str) -> String {
    let fragment = Html::parse_fragment(raw);
    let text = fragment.root_element().text().collect::<Vec<_>>().join(" ");
    let text = if text.trim().is_empty() { raw } else { &text };
    text.replace('\u{00a0}', " ")
        .replace("&amp;", "&")
        .replace("&quot;", "\"")
        .replace("&#039;", "'")
        .replace("&#8217;", "'")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

fn truncate_chars(value: &str, max_chars: usize) -> String {
    if value.chars().count() <= max_chars {
        return value.to_string();
    }
    value.chars().take(max_chars).collect()
}

fn round2(value: f32) -> f32 {
    (value * 100.0).round() / 100.0
}

fn round2_f64(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

const BULLISH_KEYWORDS: &[&str] = &[
    "gain",
    "gains",
    "rally",
    "surges",
    "jumps",
    "beats",
    "profit jumps",
    "record high",
    "wins order",
    "order win",
    "approval",
    "upgrade",
    "raises target",
    "buyback",
    "dividend",
    "strong demand",
    "stake buy",
    "expansion",
];

const BEARISH_KEYWORDS: &[&str] = &[
    "falls",
    "fall",
    "drops",
    "plunges",
    "slumps",
    "loss",
    "losses",
    "misses",
    "downgrade",
    "cuts target",
    "probe",
    "penalty",
    "fine",
    "fraud",
    "default",
    "resigns",
    "weak demand",
    "selloff",
    "stake sale",
];

const IMPACT_KEYWORDS: &[&str] = &[
    "earnings",
    "results",
    "profit",
    "revenue",
    "margin",
    "order",
    "merger",
    "acquisition",
    "stake",
    "block deal",
    "bulk deal",
    "sebi",
    "rbi",
    "ipo",
    "buyback",
    "dividend",
    "approval",
    "guidance",
    "promoter",
];

const INTRADAY_KEYWORDS: &[&str] = &[
    "block deal",
    "bulk deal",
    "intraday",
    "today",
    "opening",
    "pre market",
    "volume",
];

const SWING_KEYWORDS: &[&str] = &[
    "ipo",
    "merger",
    "acquisition",
    "capacity",
    "expansion",
    "capex",
    "guidance",
    "tariff",
];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_current_nse_historical_csv_headers() {
        let csv = concat!(
            "\u{feff}\"Date \",\"Symbol \",\"Security Name \",\"Client Name \",",
            "\"Buy / Sell \",\"Quantity Traded \",",
            "\"Trade Price / Wght. Avg. Price \",\"Remarks \"\n",
            "\"10-JUL-2026\",\"DEMO\",\"Demo Limited\",\"Buyer LLP\",",
            "\"BUY\",\"1,00,000\",\"125.50\",\"-\"\n",
        );
        let rows = parse_nse_daily_report(&csv, "BULK", "2026-07-11 10:00:00")
            .expect("CSV should parse");
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].symbol, "DEMO");
        assert_eq!(rows[0].deal_date, "2026-07-10");
        assert_eq!(rows[0].side, "BUY");
        assert_eq!(rows[0].quantity, 100_000.0);
        assert_eq!(rows[0].value_lakh, 125.5);
    }

    #[test]
    fn rejects_ambiguous_family_and_bank_prefix_aliases() {
        let companies = vec![
            WatchlistCompany {
                symbol: "MAHABANK".to_string(),
                security_id: "1".to_string(),
                company_name: "BANK OF MAHARASHTRA".to_string(),
            },
            WatchlistCompany {
                symbol: "ICICIPRULI".to_string(),
                security_id: "2".to_string(),
                company_name: "ICICI PRUDENTIAL LIFE INSURANCE COMPANY LIMITED".to_string(),
            },
            WatchlistCompany {
                symbol: "OFSS".to_string(),
                security_id: "3".to_string(),
                company_name: "ORACLE FINANCIAL SERVICES SOFTWARE LIMITED".to_string(),
            },
        ];
        let aliases = build_aliases(&companies);
        assert!(!aliases.iter().any(|alias| alias.normalized == "BANK OF"));
        assert!(!aliases.iter().any(|alias| alias.normalized == "ICICI"));
        assert!(!aliases.iter().any(|alias| alias.normalized == "ORACLE"));
        assert!(aliases
            .iter()
            .any(|alias| alias.normalized == "ICICI PRUDENTIAL"));
    }
}
