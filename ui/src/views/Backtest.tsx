import { useState, useEffect } from 'react'
import { getConfig, putConfig, DEFAULT_GAP15_CONFIG, type Gap15Config } from '../api'
import { Save, RotateCcw, CheckCircle, AlertCircle, Loader2 } from 'lucide-react'

// ─── Field Definitions ────────────────────────────────────────────────────────

interface FieldDef {
  key: keyof Gap15Config
  label: string
  description: string
  unit?: string
  min: number
  max: number
  step: number
  isInt?: boolean
}

const FIELDS: FieldDef[] = [
  {
    key: 'total_capital',
    label: 'Total Capital',
    description: 'Total capital available (before leverage)',
    unit: '₹',
    min: 10000,
    max: 10000000,
    step: 10000,
    isInt: true,
  },
  {
    key: 'leverage',
    label: 'Leverage',
    description: 'MIS leverage multiplier (e.g. 5x = ₹50k × 5 = ₹2.5L margin)',
    unit: 'x',
    min: 1,
    max: 20,
    step: 1,
    isInt: true,
  },
  {
    key: 'top_n',
    label: 'Top N Stocks',
    description: 'Maximum number of stocks to trade per day (sorted by gap % descending)',
    unit: 'stocks',
    min: 1,
    max: 50,
    step: 1,
    isInt: true,
  },
  {
    key: 'tp_pct',
    label: 'Take Profit %',
    description: 'Exit SELL when price drops this % from entry (gap-down fade)',
    unit: '%',
    min: 0.1,
    max: 10,
    step: 0.1,
  },
  {
    key: 'sl_pct',
    label: 'Stop Loss %',
    description: 'Exit SELL when price rises this % from entry (stop out)',
    unit: '%',
    min: 0.1,
    max: 5,
    step: 0.1,
  },
  {
    key: 'exit_bucket',
    label: 'Force Exit Bucket',
    description: 'Close all positions at this bucket regardless (bucket 45 = 9:59 AM)',
    unit: 'bucket',
    min: 10,
    max: 375,
    step: 1,
    isInt: true,
  },
  {
    key: 'gap_min_pct',
    label: 'Min Gap %',
    description: 'Minimum gap-up % required to trade a stock (from prev close)',
    unit: '%',
    min: 0.5,
    max: 10,
    step: 0.1,
  },
  {
    key: 'gap_max_pct',
    label: 'Max Gap %',
    description: 'Maximum gap % allowed — filters out corporate actions (splits/bonus)',
    unit: '%',
    min: 5,
    max: 50,
    step: 1,
  },
  {
    key: 'price_max',
    label: 'Max Price',
    description: 'Only trade stocks with LTP below this price at entry',
    unit: '₹',
    min: 100,
    max: 5000,
    step: 50,
  },
  {
    key: 'cap_mult',
    label: 'Capital Multiplier',
    description: 'Max position = (total_margin / top_n) × cap_mult. Caps per-stock allocation.',
    unit: 'x',
    min: 1,
    max: 5,
    step: 0.5,
  },
]

// ─── Component ────────────────────────────────────────────────────────────────

export function Backtest() {
  const [config, setConfig] = useState<Gap15Config>(DEFAULT_GAP15_CONFIG)
  const [saved, setSaved] = useState<Gap15Config>(DEFAULT_GAP15_CONFIG)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState<'idle' | 'saved' | 'error'>('idle')
  const [errorMsg, setErrorMsg] = useState('')

  useEffect(() => {
    setLoading(true)
    getConfig()
      .then((cfg) => {
        setConfig(cfg)
        setSaved(cfg)
      })
      .catch(() => {
        // fallback to defaults
      })
      .finally(() => setLoading(false))
  }, [])

  const isDirty = Object.keys(config).some(
    (k) => config[k as keyof Gap15Config] !== saved[k as keyof Gap15Config]
  )

  function handleChange(key: keyof Gap15Config, value: string, isInt?: boolean) {
    const parsed = isInt ? parseInt(value, 10) : parseFloat(value)
    if (!isNaN(parsed)) {
      setConfig((prev) => ({ ...prev, [key]: parsed }))
    }
    setStatus('idle')
  }

  function handleReset() {
    setConfig(saved)
    setStatus('idle')
  }

  async function handleSave() {
    setSaving(true)
    setStatus('idle')
    try {
      const updated = await putConfig(config)
      setSaved(updated)
      setConfig(updated)
      setStatus('saved')
      setTimeout(() => setStatus('idle'), 3000)
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : 'Save failed')
      setStatus('error')
    } finally {
      setSaving(false)
    }
  }

  // Derived stats
  const totalMargin = config.total_capital * config.leverage
  const basePosValue = Math.floor(totalMargin / config.top_n)
  const maxPosValue = Math.floor(basePosValue * config.cap_mult)

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={24} className="animate-spin text-[#2979FF]" />
        <span className="ml-3 text-[#5A6478] text-sm">Loading config…</span>
      </div>
    )
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-lg font-semibold text-white">Gap15 Strategy Config</h1>
        <p className="text-xs text-[#5A6478] mt-1">
          SELL-only gap-fade strategy · Entry at 9:16 AM (bucket 2) · LARGE+MEGA cap only
        </p>
      </div>

      {/* Summary card */}
      <div className="bg-[#0F1117] border border-[#1E2330] rounded-xl p-4 grid grid-cols-3 gap-4 text-center">
        <div>
          <p className="text-[10px] text-[#5A6478] uppercase tracking-wider mb-1">Total Margin</p>
          <p className="text-base font-semibold text-white">
            ₹{(totalMargin / 1000).toFixed(0)}k
          </p>
          <p className="text-[10px] text-[#5A6478]">
            {config.total_capital / 1000}k × {config.leverage}x
          </p>
        </div>
        <div>
          <p className="text-[10px] text-[#5A6478] uppercase tracking-wider mb-1">Base Position</p>
          <p className="text-base font-semibold text-white">
            ₹{(basePosValue / 1000).toFixed(1)}k
          </p>
          <p className="text-[10px] text-[#5A6478]">margin / top_n</p>
        </div>
        <div>
          <p className="text-[10px] text-[#5A6478] uppercase tracking-wider mb-1">Max Position</p>
          <p className="text-base font-semibold text-white">
            ₹{(maxPosValue / 1000).toFixed(1)}k
          </p>
          <p className="text-[10px] text-[#5A6478]">base × {config.cap_mult}x</p>
        </div>
      </div>

      {/* Fields */}
      <div className="bg-[#0F1117] border border-[#1E2330] rounded-xl overflow-hidden">
        {FIELDS.map((field, i) => {
          const val = config[field.key]
          const defaultVal = DEFAULT_GAP15_CONFIG[field.key]
          const changed = val !== saved[field.key]
          const isDefault = val === defaultVal

          return (
            <div
              key={field.key}
              className={`flex items-center gap-4 px-5 py-4 ${
                i < FIELDS.length - 1 ? 'border-b border-[#1E2330]' : ''
              } ${changed ? 'bg-[#2979FF]/5' : ''}`}
            >
              {/* Label */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-gray-200">{field.label}</span>
                  {changed && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#2979FF]/20 text-[#2979FF] font-medium">
                      modified
                    </span>
                  )}
                  {!isDefault && !changed && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#1E2330] text-[#5A6478]">
                      custom
                    </span>
                  )}
                </div>
                <p className="text-[11px] text-[#5A6478] mt-0.5 leading-relaxed">
                  {field.description}
                </p>
                <p className="text-[10px] text-[#3A4255] mt-0.5">
                  default: {defaultVal}{field.unit ? ` ${field.unit}` : ''}
                </p>
              </div>

              {/* Input */}
              <div className="flex items-center gap-2 shrink-0">
                <input
                  type="number"
                  value={val}
                  min={field.min}
                  max={field.max}
                  step={field.step}
                  onChange={(e) => handleChange(field.key, e.target.value, field.isInt)}
                  className={`w-24 bg-[#141720] border rounded-lg px-3 py-1.5 text-sm text-right font-mono tabular-nums outline-none transition-colors ${
                    changed
                      ? 'border-[#2979FF]/50 text-[#2979FF]'
                      : 'border-[#1E2330] text-gray-200'
                  } focus:border-[#2979FF]/70`}
                />
                {field.unit && (
                  <span className="text-xs text-[#5A6478] w-10">{field.unit}</span>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* Action bar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 h-8">
          {status === 'saved' && (
            <div className="flex items-center gap-1.5 text-[#00E676] text-xs font-medium">
              <CheckCircle size={14} />
              <span>Saved successfully</span>
            </div>
          )}
          {status === 'error' && (
            <div className="flex items-center gap-1.5 text-[#FF5252] text-xs">
              <AlertCircle size={14} />
              <span>{errorMsg}</span>
            </div>
          )}
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleReset}
            disabled={!isDirty || saving}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium text-[#5A6478] hover:text-gray-200 hover:bg-[#141720] disabled:opacity-40 disabled:cursor-not-allowed transition-all"
          >
            <RotateCcw size={13} />
            Reset
          </button>
          <button
            onClick={handleSave}
            disabled={!isDirty || saving}
            className="flex items-center gap-1.5 px-5 py-2 rounded-lg text-xs font-semibold bg-[#2979FF] text-white hover:bg-[#2979FF]/90 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
          >
            {saving ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <Save size={13} />
            )}
            Save Config
          </button>
        </div>
      </div>
    </div>
  )
}
