"""Symbol-mention resolution -- whole-word alias matching, not ML.

Mirrors engine/src/news.rs::{build_aliases,resolve_mentions}. Deliberately does not
add a company's first word alone as an alias (e.g. bare "ICICI", "TATA", "BANK OF")
-- group prefixes routinely identify a broker, parent, or a different listed company
in the same headline, so precision is favored over recall here.
"""

from __future__ import annotations

from dataclasses import dataclass

from swing_atlas.domain.news.models import NewsArticle, NewsMention, WatchlistCompany
from swing_atlas.domain.news.text_utils import normalize_for_match

_GENERIC_ALIASES = frozenset(
    {
        "INDIA",
        "INDIAN",
        "BANK",
        "FINANCE",
        "FINANCIAL",
        "CAPITAL",
        "STEEL",
        "POWER",
        "ENERGY",
        "GLOBAL",
        "TECH",
        "TECHNOLOGIES",
        "SYSTEMS",
        "INDUSTRIES",
        "MARKETS",
        "STOCK",
        "STOCKS",
        "NIFTY",
        "SENSEX",
    }
)

_COMPANY_SUFFIX_WORDS = frozenset(
    {"LTD", "LIMITED", "PVT", "PRIVATE", "THE", "COMPANY", "CO", "CORP", "CORPORATION", "INC"}
)

_CONNECTIVE_WORDS = frozenset({"AND", "FOR", "FROM", "OF", "WITH"})

MANUAL_ALIASES: list[tuple[str, list[str]]] = [
    ("RELIANCE", ["RIL", "JIO", "JIO PLATFORMS"]),
    ("HDFCBANK", ["HDFC BANK"]),
    ("ICICIBANK", ["ICICI BANK"]),
    ("SBIN", ["SBI", "STATE BANK OF INDIA"]),
    ("INFY", ["INFOSYS"]),
    ("TCS", ["TATA CONSULTANCY SERVICES"]),
    ("LT", ["LARSEN AND TOUBRO", "L T"]),
    ("TATAMOTORS", ["TATA MOTORS"]),
    ("TATASTEEL", ["TATA STEEL"]),
    ("BAJFINANCE", ["BAJAJ FINANCE"]),
    ("BAJAJFINSV", ["BAJAJ FINSERV"]),
    ("BHARTIARTL", ["BHARTI AIRTEL", "AIRTEL"]),
    ("HINDUNILVR", ["HUL", "HINDUSTAN UNILEVER"]),
]


@dataclass(frozen=True, slots=True)
class SymbolAlias:
    symbol: str
    security_id: str
    company_name: str
    matched_text: str
    normalized: str
    confidence: float


def clean_company_name(company_name: str) -> str:
    words = normalize_for_match(company_name).split()
    return " ".join(word for word in words if word not in _COMPANY_SUFFIX_WORDS)


def is_generic_alias(alias: str) -> bool:
    return alias in _GENERIC_ALIASES


def is_distinctive_alias_word(word: str) -> bool:
    return len(word) >= 3 and not is_generic_alias(word) and word not in _CONNECTIVE_WORDS


def contains_alias(wrapped_text: str, normalized_alias: str) -> bool:
    return f" {normalized_alias} " in wrapped_text


def _add_alias(
    out: list[SymbolAlias], company: WatchlistCompany, alias: str, confidence: float
) -> None:
    normalized = normalize_for_match(alias)
    if len(normalized) < 3 or is_generic_alias(normalized):
        return
    out.append(
        SymbolAlias(
            symbol=company.symbol.upper(),
            security_id=company.security_id,
            company_name=company.company_name,
            matched_text=alias,
            normalized=normalized,
            confidence=confidence,
        )
    )


def build_aliases(companies: list[WatchlistCompany]) -> list[SymbolAlias]:
    out: list[SymbolAlias] = []
    by_symbol = {company.symbol.upper(): company for company in companies}

    for company in companies:
        _add_alias(out, company, company.symbol.upper(), 0.88)

        cleaned_company = clean_company_name(company.company_name)
        if len(cleaned_company) >= 5:
            _add_alias(out, company, cleaned_company, 0.82)

        words = cleaned_company.split()
        if len(words) >= 2 and all(is_distinctive_alias_word(w) for w in words[:2]):
            _add_alias(out, company, " ".join(words[:2]), 0.72)
        # Do not add a company's first word by itself -- see module docstring.

    for symbol, manual_alias_texts in MANUAL_ALIASES:
        matched_company = by_symbol.get(symbol)
        if matched_company is None:
            continue
        for alias_text in manual_alias_texts:
            _add_alias(out, matched_company, alias_text, 0.9)

    seen: set[tuple[str, str]] = set()
    deduped: list[SymbolAlias] = []
    for symbol_alias in out:
        key = (symbol_alias.symbol, symbol_alias.normalized)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(symbol_alias)
    return deduped


def resolve_mentions(
    articles: list[NewsArticle], companies: list[WatchlistCompany]
) -> list[NewsMention]:
    aliases = build_aliases(companies)
    mentions: list[NewsMention] = []

    for article in articles:
        text_wrapped = f" {normalize_for_match(f'{article.title} {article.summary}')} "
        title_wrapped = f" {normalize_for_match(article.title)} "

        best_by_symbol: dict[str, NewsMention] = {}
        for alias in aliases:
            if not contains_alias(text_wrapped, alias.normalized):
                continue
            confidence = alias.confidence
            if contains_alias(title_wrapped, alias.normalized):
                confidence = min(confidence + 0.07, 0.98)

            mention = NewsMention(
                article_id=article.article_id,
                symbol=alias.symbol,
                security_id=alias.security_id,
                company_name=alias.company_name,
                match_confidence=confidence,
                matched_text=alias.matched_text,
            )
            existing = best_by_symbol.get(alias.symbol)
            if existing is None or mention.match_confidence > existing.match_confidence:
                best_by_symbol[alias.symbol] = mention

        article_mentions = sorted(
            best_by_symbol.values(), key=lambda m: (-m.match_confidence, m.symbol)
        )
        mentions.extend(article_mentions[:8])

    return mentions
