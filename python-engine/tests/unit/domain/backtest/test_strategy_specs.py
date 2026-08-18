from swing_atlas.domain.backtest.strategy_specs import (
    BacktestStrategySpec,
    build_entries_cte,
    dynamic_exit_pct,
    escape_sql,
    is_deprecated_backtest_strategy,
    load_backtest_strategy_specs,
    strategy_entry_select,
    strategy_method_family,
)


def test_escape_sql_doubles_single_quotes() -> None:
    assert escape_sql("O'Brien") == "O''Brien"
    assert escape_sql("plain") == "plain"


def test_dynamic_exit_pct_prefers_atr_multiple_when_valid() -> None:
    expr = dynamic_exit_pct(2.5, 10.0)
    assert "2.5" in expr
    assert "sig.atr14" in expr


def test_dynamic_exit_pct_falls_back_to_flat_pct_when_atr_multiple_missing() -> None:
    assert dynamic_exit_pct(None, 7.5) == "7.5"


def test_dynamic_exit_pct_falls_back_when_atr_multiple_not_positive() -> None:
    assert dynamic_exit_pct(0.0, 7.5) == "7.5"
    assert dynamic_exit_pct(-1.0, 7.5) == "7.5"


def test_strategy_entry_select_embeds_escaped_strategy_id_and_condition() -> None:
    spec = BacktestStrategySpec(
        strategy_id="weird'strategy",
        strategy_name="Weird",
        setup_family="Weird Family",
        min_score=77,
        tp_pct=5.0,
        sl_pct=2.0,
        target_atr=None,
        stop_atr=None,
        max_hold_sessions=10,
        max_positions_per_day=3,
        capital_per_trade=10_000.0,
        entry_condition_sql=None,
    )

    sql = strategy_entry_select(spec)

    assert "weird''strategy" in sql
    assert "sig.score >= 77" in sql
    # Falls back to matching on setup_family since no known strategy_id or explicit condition.
    assert "sig.setup_family = 'Weird Family'" in sql


def test_build_entries_cte_joins_all_specs_with_union_all() -> None:
    specs = load_backtest_strategy_specs()
    cte = build_entries_cte(specs)

    assert cte.count("UNION ALL") == len(specs) - 1
    for spec in specs:
        assert f"'{spec.strategy_id}'" in cte


def test_load_backtest_strategy_specs_excludes_deprecated_ids() -> None:
    specs = load_backtest_strategy_specs()

    for spec in specs:
        assert not is_deprecated_backtest_strategy(spec.strategy_id)


def test_strategy_method_family_matches_by_substring_priority() -> None:
    assert strategy_method_family("regime-mean-reversion-v1") == "Regime Mean Reversion"
    assert strategy_method_family("weekly-supertrend-10-3") == "Weekly Supertrend"
    assert strategy_method_family("near-52w-high-runner-v2") == "52W Momentum"
    assert strategy_method_family("something-unrecognized") == "Other"
