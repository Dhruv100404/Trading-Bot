from dataclasses import dataclass
from typing import Any

from swing_atlas.repositories.clickhouse import ClickHouseRepo


@dataclass
class _FakeQueryResult:
    column_names: tuple[str, ...]
    result_rows: list[tuple[Any, ...]]


class _FakeClient:
    def __init__(self) -> None:
        self.last_sql: str | None = None
        self.last_parameters: dict[str, Any] | None = None

    async def query(self, sql: str, parameters: dict[str, Any] | None = None) -> _FakeQueryResult:
        self.last_sql = sql
        self.last_parameters = parameters
        return _FakeQueryResult(column_names=("symbol",), result_rows=[("RELIANCE",)])


async def test_query_rows_escapes_format_specifiers_when_parameters_present() -> None:
    fake_client = _FakeClient()
    repo = ClickHouseRepo(fake_client)  # type: ignore[arg-type]

    sql = (
        "SELECT formatDateTime(fetched_at, '%Y-%m-%d %H:%i:%S') "
        "WHERE symbol = %(symbol)s LIMIT %(limit)s"
    )
    rows = await repo.query_rows(sql, {"symbol": "RELIANCE", "limit": 10})

    assert fake_client.last_sql == (
        "SELECT formatDateTime(fetched_at, '%%Y-%%m-%%d %%H:%%i:%%S') "
        "WHERE symbol = %(symbol)s LIMIT %(limit)s"
    )
    assert fake_client.last_parameters == {"symbol": "RELIANCE", "limit": 10}
    assert rows == [{"symbol": "RELIANCE"}]


async def test_query_rows_leaves_sql_untouched_without_parameters() -> None:
    fake_client = _FakeClient()
    repo = ClickHouseRepo(fake_client)  # type: ignore[arg-type]

    sql = "SELECT formatDateTime(now(), '%Y-%m-%d')"
    await repo.query_rows(sql)

    assert fake_client.last_sql == sql
