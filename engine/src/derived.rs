/// Per-symbol running state for VWAP computation
#[derive(Default, Clone, Debug)]
pub struct RunningState {
    pub vwap_num: f64,    // Σ(ltp * volume_delta)
    pub vwap_den: f64,    // Σ(volume_delta)
    pub prev_ltp: f32,
    pub prev_volume_cum: u64,
}

#[derive(Debug)]
pub struct DerivedFields {
    pub vwap: f32,
    pub volume_rate: f32,
    pub candle_body_ratio: f32,
    pub volume_delta: u32,
}

pub fn compute(
    ltp: f32,
    candle_open: f32,
    candle_high: f32,
    candle_low: f32,
    volume_cum: u64,
    state: &mut RunningState,
) -> DerivedFields {
    let volume_delta = volume_cum.saturating_sub(state.prev_volume_cum) as u32;

    // VWAP: running Σ(ltp * vol) / Σ(vol)
    let vol_for_vwap = if state.prev_volume_cum == 0 { volume_cum } else { volume_delta as u64 };
    state.vwap_num += ltp as f64 * vol_for_vwap as f64;
    state.vwap_den += vol_for_vwap as f64;
    let vwap = if state.vwap_den > 0.0 { (state.vwap_num / state.vwap_den) as f32 } else { ltp };

    let volume_rate = volume_delta as f32 / 60.0;

    let range = candle_high - candle_low;
    let candle_body_ratio = if range > 0.0 { (ltp - candle_open).abs() / range } else { 0.0 };

    state.prev_ltp = ltp;
    state.prev_volume_cum = volume_cum;

    DerivedFields { vwap, volume_rate, candle_body_ratio, volume_delta }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_vwap_single_bucket() {
        let mut state = RunningState::default();
        let d = compute(100.0, 99.0, 101.0, 98.0, 1000, &mut state);
        assert!((d.vwap - 100.0).abs() < 0.01);
    }

    #[test]
    fn test_volume_delta() {
        let mut state = RunningState::default();
        compute(100.0, 99.0, 101.0, 98.0, 1000, &mut state);
        let d = compute(101.0, 99.0, 102.0, 98.0, 1500, &mut state);
        assert_eq!(d.volume_delta, 500);
    }

    #[test]
    fn test_candle_body_ratio_full_body() {
        let mut state = RunningState::default();
        let d = compute(101.0, 99.0, 101.0, 99.0, 1000, &mut state);
        assert!((d.candle_body_ratio - 1.0).abs() < 0.01);
    }

    #[test]
    fn test_vwap_multi_bucket() {
        let mut state = RunningState::default();
        compute(100.0, 99.0, 101.0, 98.0, 1000, &mut state);
        let d = compute(102.0, 100.0, 103.0, 99.0, 1500, &mut state);
        let expected = (100.0 * 1000.0 + 102.0 * 500.0) / 1500.0;
        assert!((d.vwap - expected).abs() < 0.01);
    }
}
