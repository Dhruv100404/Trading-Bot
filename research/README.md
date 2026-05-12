# Python Research Lane

This folder is for strategy research that should not be squeezed into app JSON too early.

Use Python strategy modules under `research/strategies/` when you want the AI or a human researcher to express richer logic, parameter experiments, market filters, and exits. The app-facing `strategies/*.json` files should stay as registry/promotional metadata until a strategy has survived research, costs, out-of-sample checks, and paper trading.

Run the Python lane with:

```powershell
python scripts\research_backtest.py
```

Useful options:

```powershell
python scripts\research_backtest.py --strategy atr_stretch_liquid_only_python
python scripts\research_backtest.py --refresh-cache
python scripts\research_backtest.py --out-dir docs\python_research_outputs
```

The runner reuses the existing daily parquet cache and feature/backtest helpers from `scripts/quant_research_pipeline.py`, so it keeps the current system intact while giving research code full Python expressiveness.
