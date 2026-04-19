
interface ToggleProps {
  checked: boolean
  onChange: (value: boolean) => void
  disabled?: boolean
}

export function Toggle({ checked, onChange, disabled = false }: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full
        border-2 transition-colors duration-200
        focus:outline-none focus:ring-2 focus:ring-[#2979FF]/40 focus:ring-offset-1 focus:ring-offset-[#0D0F14]
        ${disabled ? 'opacity-40 cursor-not-allowed' : ''}
        ${checked ? 'border-[#2979FF] bg-[#2979FF]' : 'border-[#2A3045] bg-[#1A1F2E]'}`}
    >
      <span
        className={`pointer-events-none inline-block h-3.5 w-3.5 rounded-full bg-white shadow
          transform transition-transform duration-200 ease-in-out
          ${checked ? 'translate-x-3.5' : 'translate-x-0.5'}`}
      />
    </button>
  )
}
