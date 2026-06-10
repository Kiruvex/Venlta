interface CardProps {
  title?: string;
  children: any;
  class?: string;
  variant?: 'default' | 'glass' | 'bordered';
}

export function Card({ title, children, class: className = '', variant = 'default' }: CardProps) {
  const variantClasses = {
    default: 'bg-white dark:bg-gray-800/90 shadow-sm',
    glass: 'glass border border-white/20 dark:border-gray-700/50 shadow-sm',
    bordered: 'bg-white dark:bg-gray-800 border-2 border-gray-100 dark:border-gray-700',
  };

  return (
    <div class={`rounded-xl ${variantClasses[variant]} card-hover animate-fade-in ${className}`}>
      {title && (
        <div class="px-5 py-3.5 border-b border-gray-100/80 dark:border-gray-700/50">
          <h3 class="text-xs font-semibold uppercase tracking-widest text-gray-400 dark:text-gray-500">{title}</h3>
        </div>
      )}
      <div class="p-5">
        {children}
      </div>
    </div>
  );
}
