import { ComponentChildren } from 'preact';

interface ButtonProps {
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost';
  size?: 'sm' | 'md';
  disabled?: boolean;
  onClick?: () => void | Promise<void>;
  children: ComponentChildren;
  class?: string;
}

const SIZE_CLASSES = {
  sm: 'px-3 py-1.5 text-xs gap-1',
  md: 'px-4 py-2 text-sm gap-1.5',
};

const VARIANT_CLASSES = {
  primary: 'bg-gradient-to-r from-green-500 to-emerald-600 hover:from-green-600 hover:to-emerald-700 text-white shadow-sm shadow-green-500/25 hover:shadow-md hover:shadow-green-500/30 focus:ring-green-500/40 dark:focus:ring-offset-gray-800 disabled:from-gray-300 disabled:to-gray-400 disabled:shadow-none',
  secondary: 'bg-gray-100 dark:bg-gray-700/80 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 border border-gray-200 dark:border-gray-600 focus:ring-gray-400/40 dark:focus:ring-offset-gray-800 disabled:opacity-50',
  danger: 'bg-gradient-to-r from-red-500 to-rose-600 hover:from-red-600 hover:to-rose-700 text-white shadow-sm shadow-red-500/25 hover:shadow-md hover:shadow-red-500/30 focus:ring-red-500/40 dark:focus:ring-offset-gray-800 disabled:from-gray-300 disabled:to-gray-400 disabled:shadow-none',
  ghost: 'bg-transparent hover:bg-gray-100 dark:hover:bg-gray-700/50 text-gray-600 dark:text-gray-300 focus:ring-gray-400/40 dark:focus:ring-offset-gray-800',
};

export function Button({ variant = 'primary', size = 'md', disabled = false, onClick, children, class: className = '' }: ButtonProps) {
  const baseClasses = 'rounded-lg transition-all duration-200 font-medium focus:outline-none focus:ring-2 focus:ring-offset-2 btn-press inline-flex items-center justify-center';
  return (
    <button
      class={`${baseClasses} ${SIZE_CLASSES[size]} ${VARIANT_CLASSES[variant]} ${className}`}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
