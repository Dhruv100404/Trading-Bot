use std::env;

#[derive(Clone, Debug)]
pub struct Config {
    pub dhan_base_url: String,
    pub dhan_quote_endpoint: String,
    pub dhan_orders_endpoint: String,
    #[allow(dead_code)]
    pub dhan_positions_endpoint: String,
    pub dhan_access_token: String,
    pub dhan_client_id: String,
    pub clickhouse_url: String,
    pub debug: bool,
    pub ws_subscribe_fno_oi: bool,
    pub gemini_api_key: String,
    pub telegram_bot_token: String,
    pub telegram_chat_id: String,
}

impl Config {
    pub fn from_env() -> anyhow::Result<Self> {
        Ok(Self {
            dhan_base_url: env::var("DHAN_BASE_URL")
                .unwrap_or_else(|_| "https://api.dhan.co/v2".into()),
            dhan_quote_endpoint: env::var("DHAN_QUOTE_ENDPOINT")
                .unwrap_or_else(|_| "/marketfeed/quote".into()),
            dhan_orders_endpoint: env::var("DHAN_ORDERS_ENDPOINT")
                .unwrap_or_else(|_| "/orders".into()),
            dhan_positions_endpoint: env::var("DHAN_POSITIONS_ENDPOINT")
                .unwrap_or_else(|_| "/positions".into()),
            // Token/client_id are optional in .env — managed via UI (system_settings + accounts)
            dhan_access_token: env::var("DHAN_ACCESS_TOKEN").unwrap_or_default(),
            dhan_client_id: env::var("DHAN_CLIENT_ID").unwrap_or_default(),
            clickhouse_url: env::var("CLICKHOUSE_URL")
                .unwrap_or_else(|_| "http://localhost:8123".into()),
            debug: env::var("DEBUG").unwrap_or_default() == "true",
            ws_subscribe_fno_oi: env::var("WS_SUBSCRIBE_FNO_OI").unwrap_or_default() == "true",
            gemini_api_key: env::var("GEMINI_API_KEY").unwrap_or_default(),
            telegram_bot_token: env::var("TELEGRAM_BOT_TOKEN").unwrap_or_default(),
            telegram_chat_id: env::var("TELEGRAM_CHAT_ID").unwrap_or_default(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    #[test]
    fn test_config_missing_token_defaults_empty() {
        let _guard = ENV_LOCK.lock().unwrap();
        env::remove_var("DHAN_ACCESS_TOKEN");
        env::remove_var("DHAN_CLIENT_ID");
        let result = Config::from_env();
        assert!(result.is_ok());
        let cfg = result.unwrap();
        assert!(cfg.dhan_access_token.is_empty());
        assert!(cfg.dhan_client_id.is_empty());
    }

    #[test]
    fn test_config_defaults() {
        let _guard = ENV_LOCK.lock().unwrap();
        env::set_var("DHAN_ACCESS_TOKEN", "tok");
        env::set_var("DHAN_CLIENT_ID", "cid");
        env::remove_var("DHAN_BASE_URL");
        let cfg = Config::from_env().unwrap();
        assert_eq!(cfg.dhan_base_url, "https://api.dhan.co/v2");
        assert!(!cfg.debug);
        env::remove_var("DHAN_ACCESS_TOKEN");
        env::remove_var("DHAN_CLIENT_ID");
    }
}
