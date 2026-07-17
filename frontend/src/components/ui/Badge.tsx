import type { ReactNode } from 'react';

type Variant = 'default' | 'success' | 'warning' | 'info' | 'error';

const styles: Record<Variant, string> = {
  default: 'bg-slate-100 text-slate-600',
  success: 'bg-emerald-50 text-emerald-700',
  warning: 'bg-amber-50 text-amber-700',
  info: 'bg-indigo-50 text-indigo-700',
  error: 'bg-red-50 text-red-700',
};

interface BadgeProps {
  children: ReactNode;
  variant?: Variant;
}

export function Badge({ children, variant = 'default' }: BadgeProps) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium ${styles[variant]}`}>
      {children}
    </span>
  );
}
