from pydantic import BaseModel


class BrokerStatus(BaseModel):
    provider: str
    configured: bool
    state: str
    message: str
    credential_source: str
    client_id: str | None
    issued_at_utc: str | None
    expires_at_utc: str | None
    live_quotes: bool
