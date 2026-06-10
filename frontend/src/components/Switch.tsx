interface SwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  label?: string;
}

export function Switch({ checked, onChange, disabled = false, label }: SwitchProps) {
  return (
    <label class="inline-flex items-center gap-2 cursor-pointer">
      <button
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        class={`relative inline-flex h-5 w-9 items-center rounded-full transition-all duration-200
          ${checked
            ? 'bg-gradient-to-r from-green-500 to-emerald-600 shadow-sm shadow-green-500/30'
            : 'bg-gray-300 dark:bg-gray-600'}
          ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
        onClick={() => !disabled && onChange(!checked)}
      >
        <span class={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow-sm transition-all duration-200
          ${checked ? 'translate-x-[18px]' : 'translate-x-[3px]'}`} />
      </button>
      {label && <span class="text-sm text-gray-700 dark:text-gray-300">{label}</span>}
    </label>
  );
}
