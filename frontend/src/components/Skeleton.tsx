/**
 * 骨架屏组件：加载状态占位，使用 shimmer 动画
 */
interface SkeletonProps {
  width?: string;
  height?: string;
  class?: string;
  circle?: boolean;
}

export function Skeleton({ width = '100%', height = '1rem', class: className = '', circle = false }: SkeletonProps) {
  return (
    <div
      class={`bg-gradient-to-r from-gray-200 via-gray-100 to-gray-200 dark:from-gray-700 dark:via-gray-600 dark:to-gray-700 bg-[length:200%_100%] animate-shimmer ${circle ? 'rounded-full' : 'rounded-lg'} ${className}`}
      style={{ width, height }}
      aria-hidden="true"
    />
  );
}

/** 多行骨架屏 */
export function SkeletonLines({ lines = 3, gap = '0.5rem' }: { lines?: number; gap?: string }) {
  return (
    <div class="space-y-3" style={{ gap }}>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} width={i === lines - 1 ? '60%' : '100%'} height="0.75rem" />
      ))}
    </div>
  );
}
