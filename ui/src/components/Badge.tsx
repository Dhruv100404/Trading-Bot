
interface BadgeProps {
  label: string
  variant: 'buy' | 'sell' | 'tp' | 'sl' | 'time' | 'open' | 'neutral'
  className?: string
}

const variantClasses: Record<BadgeProps['variant'], string> = {
  buy:     'bg-[#00E676]/10 text-[#00E676] border border-[#00E676]/25',
  sell:    'bg-[#FF5252]/10 text-[#FF5252] border border-[#FF5252]/25',
  tp:      'bg-[#2979FF]/10 text-[#2979FF] border border-[#2979FF]/25',
  sl:      'bg-[#FF5252]/10 text-[#FF5252] border border-[#FF5252]/20',
  time:    'bg-[#FFD740]/10 text-[#FFD740] border border-[#FFD740]/25',
  open:    'bg-[#1A1F2E] text-[#5A6478] border border-[#2A3045]',
  neutral: 'bg-[#1A1F2E] text-[#5A6478] border border-[#2A3045]',
}

export function Badge({ label, variant, className = '' }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold uppercase tracking-wider ${variantClasses[variant]} ${className}`}
    >
      {label}
    </span>
  )
}
