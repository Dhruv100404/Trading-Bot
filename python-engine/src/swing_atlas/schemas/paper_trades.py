from pydantic import BaseModel


class PaperTradeInput(BaseModel):
    symbol: str
    company_name: str
    setup_family: str
    bias: str | None = None
    entry_price: float
    quantity: int | None = None
    max_sessions: int | None = None
    capital_allocated: float | None = None
    stop_loss: float
    target_price: float
    expected_hold: str | None = None
    thesis: str | None = None
    notes: str | None = None


class PaperTradeCloseInput(BaseModel):
    exit_price: float
    close_reason: str | None = None


class PaperBudgetInput(BaseModel):
    total_budget: float
