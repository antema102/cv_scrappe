import type { ReactNode } from 'react';
import { Card } from '../ui/Card';
import { Skeleton } from '../ui/Skeleton';

interface StatsCardProps {
  title: string;
  value: number;
  icon: ReactNode;
  iconBg: string;
  loading?: boolean;
}

export function StatsCard({ title, value, icon, iconBg, loading }: StatsCardProps) {
  if (loading) {
    return (
      <Card className="p-5">
        <Skeleton className="w-10 h-10 rounded-xl mb-4" />
        <Skeleton className="h-7 w-20 mb-1.5" />
        <Skeleton className="h-4 w-28" />
      </Card>
    );
  }

  return (
    <Card className="p-5">
      <div className={`w-10 h-10 rounded-xl flex items-center justify-center mb-4 ${iconBg}`}>
        {icon}
      </div>
      <p className="text-2xl font-bold text-slate-900 tracking-tight">
        {value.toLocaleString('fr-FR')}
      </p>
      <p className="text-sm text-slate-500 mt-0.5">{title}</p>
    </Card>
  );
}
