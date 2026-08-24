from swing_atlas.domain.news.aliases import build_aliases, resolve_mentions
from swing_atlas.domain.news.models import NewsArticle, WatchlistCompany


def test_rejects_ambiguous_family_and_bank_prefix_aliases() -> None:
    """Port of engine/src/news.rs's rejects_ambiguous_family_and_bank_prefix_aliases test."""
    companies = [
        WatchlistCompany(symbol="MAHABANK", security_id="1", company_name="BANK OF MAHARASHTRA"),
        WatchlistCompany(
            symbol="ICICIPRULI",
            security_id="2",
            company_name="ICICI PRUDENTIAL LIFE INSURANCE COMPANY LIMITED",
        ),
        WatchlistCompany(
            symbol="OFSS",
            security_id="3",
            company_name="ORACLE FINANCIAL SERVICES SOFTWARE LIMITED",
        ),
    ]

    aliases = build_aliases(companies)
    normalized = {alias.normalized for alias in aliases}

    assert "BANK OF" not in normalized
    assert "ICICI" not in normalized
    assert "ORACLE" not in normalized
    assert "ICICI PRUDENTIAL" in normalized


def test_symbol_and_manual_aliases_present() -> None:
    companies = [
        WatchlistCompany(symbol="RELIANCE", security_id="1", company_name="Reliance Industries Ltd")
    ]

    aliases = build_aliases(companies)
    normalized = {alias.normalized for alias in aliases}

    assert "RELIANCE" in normalized  # bare symbol, 0.88
    assert "RIL" in normalized  # manual alias, 0.9
    assert "JIO" in normalized


def test_resolve_mentions_prefers_title_match_confidence_bump() -> None:
    companies = [
        WatchlistCompany(symbol="RELIANCE", security_id="1", company_name="Reliance Industries Ltd")
    ]
    article = NewsArticle(
        article_id="a1",
        source="test",
        source_kind="rss",
        category="markets",
        url="https://example.com/a1",
        title="Reliance shares surge on strong earnings",
        summary="Analysts raise target price",
        published_at=None,
        fetched_at="2026-01-01 00:00:00",
    )

    [mention] = resolve_mentions([article], companies)

    assert mention.symbol == "RELIANCE"
    # Symbol alias base confidence 0.88, present in title -> +0.07 = 0.95
    assert mention.match_confidence == 0.95


def test_resolve_mentions_no_match_returns_empty() -> None:
    companies = [
        WatchlistCompany(symbol="TCS", security_id="1", company_name="Tata Consultancy Services")
    ]
    article = NewsArticle(
        article_id="a1",
        source="test",
        source_kind="rss",
        category="markets",
        url="https://example.com/a1",
        title="Gold prices rally on global uncertainty",
        summary="",
        published_at=None,
        fetched_at="2026-01-01 00:00:00",
    )

    assert resolve_mentions([article], companies) == []
