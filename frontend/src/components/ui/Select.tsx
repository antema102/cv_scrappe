import type { SelectHTMLAttributes } from 'react';

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {}

export function Select({ className = '', children, ...props }: SelectProps) {
  return (
    <select
      className={`px-3 py-2.5 rounded-lg border border-slate-200 text-sm bg-white text-slate-700
        focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition ${className}`}
      {...props}
    >
      {children}
    </select>
  );
}
