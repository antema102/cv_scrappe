import { Building2 } from 'lucide-react';
import { CompanyCard } from './CompanyCard';
import { Skeleton } from '../ui/Skeleton';
import type { Company } from '../../types/company';

interface CompanyGridProps {
  companies: Company[];
  loading?: boolean;
  onSelect?: (company: Company) => void;
}

function SkeletonCard() {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4">
      <div className="flex items-start gap-3 mb-4">
        <Skeleton className="w-11 h-11 rounded-lg shrink-0" />
        <div className="flex-1">
          <Skeleton className="h-4 w-3/4 mb-2" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      </div>
      <Skeleton className="h-3 w-2/3 mb-2" />
      <Skeleton className="h-3 w-1/2 mb-4" />
      <Skeleton className="h-5 w-16 rounded-md" />
    </div>
  );
}

export function CompanyGrid({ companies, loading, onSelect }: CompanyGridProps) {
  if (loading) {
    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {Array.from({ length: 8 }, (_, i) => <SkeletonCard key={i} />)}
      </div>
    );
  }

  if (companies.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-slate-400">
        <Building2 className="w-10 h-10 mb-3 opacity-25" />
        <p className="text-sm">Aucune entreprise trouvée</p>
        <p className="text-xs mt-1">Essayez d'ajuster vos filtres</p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
      {companies.map(company => (
        <CompanyCard key={company.company_id || company._id} company={company} onSelect={onSelect} />
      ))}
    </div>
  );
}
