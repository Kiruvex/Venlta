import { useEffect, useRef } from 'preact/hooks';
import { useTranslation } from '../i18n/useTranslation';
import { ComponentChildren } from 'preact';
import { Button } from './Button';

interface ModalProps {
  title: string;
  open: boolean;
  onClose: () => void;
  onConfirm?: () => void;
  children: ComponentChildren;
}

export function Modal({ title, open, onClose, onConfirm, children }: ModalProps) {
  const backdropRef = useRef<HTMLDivElement>(null);
  const { t } = useTranslation();

  // ESC 键关闭
  useEffect(() => {
    if (!open) return;
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleEsc);
    return () => document.removeEventListener('keydown', handleEsc);
  }, [open, onClose]);

  // 点击背景关闭
  const handleBackdropClick = (e: MouseEvent) => {
    if (e.target === backdropRef.current) onClose();
  };

  if (!open) return null;

  return (
    <div
      ref={backdropRef}
      class="fixed inset-0 bg-black/40 dark:bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 animate-fade-in"
      onClick={handleBackdropClick}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl w-full max-w-md mx-4 sm:mx-0 animate-scale-in border border-gray-200/50 dark:border-gray-700/50 overflow-hidden">
        <div class="px-6 pt-5 pb-0">
          <h3 class="text-lg font-semibold text-gray-900 dark:text-gray-100">{title}</h3>
        </div>
        <div class="px-6 py-4">{children}</div>
        <div class="px-6 py-4 bg-gray-50 dark:bg-gray-900/50 flex justify-end gap-2 border-t border-gray-100 dark:border-gray-700/50">
          <Button variant="secondary" onClick={onClose}>{t('action.cancel')}</Button>
          {onConfirm && <Button variant="primary" onClick={onConfirm}>{t('action.confirm')}</Button>}
        </div>
      </div>
    </div>
  );
}
