interface InputProps {
  value: string;
  onInput: (e: Event) => void;
  placeholder?: string;
  type?: string;
  disabled?: boolean;
  class?: string;
}

export function Input({ value, onInput, placeholder = '', type = 'text', disabled = false, class: className = '' }: InputProps) {
  return (
    <input
      type={type}
      value={value}
      onInput={onInput}
      placeholder={placeholder}
      disabled={disabled}
      class={`w-full px-3.5 py-2 rounded-lg border border-gray-200 dark:border-gray-600
        bg-gray-50 dark:bg-gray-700/50 text-gray-900 dark:text-gray-100 text-sm
        placeholder:text-gray-400 dark:placeholder:text-gray-500
        focus:ring-2 focus:ring-green-500/30 focus:border-green-500 focus:bg-white dark:focus:bg-gray-700
        transition-all duration-200
        disabled:opacity-50 disabled:cursor-not-allowed ${className}`}
    />
  );
}
