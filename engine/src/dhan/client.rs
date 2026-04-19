use crate::config::Config;
use reqwest::{Client, RequestBuilder};

pub struct DhanClient {
    inner: Client,
    pub base_url: String,
    pub access_token: String,
    pub client_id: String,
    pub debug: bool,
}

impl DhanClient {
    pub fn new(config: &Config) -> Self {
        Self {
            inner: Client::builder()
                .timeout(std::time::Duration::from_secs(15))
                .build()
                .expect("Failed to build HTTP client"),
            base_url: config.dhan_base_url.trim_end_matches('/').to_string(),
            access_token: config.dhan_access_token.clone(),
            client_id: config.dhan_client_id.clone(),
            debug: config.debug,
        }
    }

    pub fn post(&self, path: &str) -> RequestBuilder {
        let url = format!("{}{}", self.base_url, path);
        self.inner.post(&url)
            .header("access-token", &self.access_token)
            .header("client-id", &self.client_id)
            .header("Content-Type", "application/json")
    }

    #[allow(dead_code)]
    pub fn get(&self, path: &str) -> RequestBuilder {
        let url = format!("{}{}", self.base_url, path);
        self.inner.get(&url)
            .header("access-token", &self.access_token)
            .header("client-id", &self.client_id)
    }

    #[allow(dead_code)]
    pub fn delete(&self, path: &str) -> RequestBuilder {
        let url = format!("{}{}", self.base_url, path);
        self.inner.delete(&url)
            .header("access-token", &self.access_token)
            .header("client-id", &self.client_id)
    }
}
