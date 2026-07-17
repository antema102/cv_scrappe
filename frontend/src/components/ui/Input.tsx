import type { InputHTMLAttributes } from 'react';
import { Search } from 'lucide-react';

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  icon?: boolean;
}

export function Input({ icon, className = '', ...props }: InputProps) {
  const base =
    'w-full py-2.5 rounded-lg border border-slate-200 text-sm bg-white text-slate-900 ' +
    'placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent transition';

  if (icon) {
    return (
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 w-4 h-4 pointer-events-none" />
        <input className={`${base} pl-10 pr-4 ${className}`} {...props} />
      </div>
    );
  }
  return <input className={`${base} px-4 ${className}`} {...props} />;
}
