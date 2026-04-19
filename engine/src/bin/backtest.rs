// CLI backtest binary — gap15 strategy
// Run via: cargo run --bin backtest -- --help
//
// This is a placeholder. The gap15 strategy backtest is implemented in Python:
//   data/s_gap15_p1k_deep.py
// For live API-based backtest, use POST /api/backtest/compute.

fn main() {
    eprintln!("Gap15 backtest: use data/s_gap15_p1k_deep.py for full analysis");
    eprintln!("Or POST /api/backtest/compute while the engine is running");
    std::process::exit(0);
}
