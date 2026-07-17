import { Globe } from 'lucide-react';

export function Header() {
  return (
    <header className="sticky top-0 z-20 bg-white/80 backdrop-blur-sm border-b border-slate-200">
      <div className="max-w-screen-2xl mx-auto px-6 h-14 flex items-center gap-3">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center shadow-sm">
            <Globe className="w-4 h-4 text-white" />
          </div>
          <span className="font-bold text-slate-900">AfriJobs</span>
        </div>
        <span className="hidden sm:block text-slate-300">·</span>
        <span className="hidden sm:block text-sm text-slate-500">Dashboard emplois africains</span>
      </div>
    </header>
  );
}
