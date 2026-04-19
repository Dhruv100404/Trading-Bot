use reqwest::{Client, RequestBuilder};

/// Zerodha Kite Connect client for order placement.
/// Auth format: `Authorization: token <api_key>:<access_token>`
pub struct ZerodhaClient {
    inner: Client,
    pub base_url: String,
    pub api_key: String,
    pub access_token: String,
}

impl ZerodhaClient {
    pub fn new(api_key: &str, access_token: &str) -> Self {
        Self {
            inner: Client::builder()
                .timeout(std::time::Duration::from_secs(15))
                .build()
                .expect("Failed to build HTTP client"),
            base_url: "https://api.kite.trade".to_string(),
            api_key: api_key.to_string(),
            access_token: access_token.to_string(),
        }
    }

    fn auth_header(&self) -> String {
        format!("token {}:{}", self.api_key, self.access_token)
    }

    pub fn post(&self, path: &str) -> RequestBuilder {
        let url = format!("{}{}", self.base_url, path);
        self.inner.post(&url)
            .header("Authorization", self.auth_header())
            .header("X-Kite-Version", "3")
    }

    pub fn get(&self, path: &str) -> RequestBuilder {
        let url = format!("{}{}", self.base_url, path);
        self.inner.get(&url)
            .header("Authorization", self.auth_header())
            .header("X-Kite-Version", "3")
    }

    pub fn delete(&self, path: &str) -> RequestBuilder {
        let url = format!("{}{}", self.base_url, path);
        self.inner.delete(&url)
            .header("Authorization", self.auth_header())
            .header("X-Kite-Version", "3")
    }
}
