import { Building2, MapPin, ExternalLink, Globe, AlignLeft, Calendar, Briefcase } from 'lucide-react';
import { Card } from '../ui/Card';
import type { Company } from '../../types/company';

interface CompanyCardProps {
  company: Company;
  onSelect?: (company: Company) => void;
}

export function CompanyCard({ company, onSelect }: CompanyCardProps) {
  const location = [company.city, company.country].filter(Boolean).join(', ');

  return (
    <Card
      className="p-4 hover:shadow-md transition-all duration-200 group cursor-pointer"
      onClick={() => onSelect?.(company)}
    >
      {/* Header: logo + nom */}
      <div className="flex items-start gap-3 mb-4">
        {company.logo_url ? (
          <img
            src={company.logo_url}
            alt={company.name}
            className="w-11 h-11 rounded-lg object-contain border border-slate-100 bg-slate-50 shrink-0 p-0.5"
            onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
          />
        ) : (
          <div className="w-11 h-11 rounded-lg bg-indigo-50 flex items-center justify-center shrink-0">
            <Building2 className="w-5 h-5 text-indigo-400" />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold text-slate-900 text-sm leading-tight truncate group-hover:text-indigo-600 transition-colors">
            {company.name || '—'}
          </h3>
          {company.sector && (
            <p className="text-xs text-slate-500 truncate mt-0.5 leading-tight">{company.sector}</p>
          )}
        </div>
      </div>

      {/* Infos */}
      <div className="space-y-1.5 mb-4">
        {location && (
          <div className="flex items-center gap-1.5 text-xs text-slate-500">
            <MapPin className="w-3.5 h-3.5 shrink-0 text-slate-400" />
            <span className="truncate">{location}</span>
          </div>
        )}
        <div className="flex items-center gap-1.5 text-xs text-slate-500">
          <Briefcase className="w-3.5 h-3.5 shrink-0 text-slate-400" />
          {company.job_count != null
            ? <span><strong className="text-slate-700">{company.job_count}</strong> offre{company.job_count > 1 ? 's' : ''}</span>
            : <span>{company.has_jobs ? 'Offres disponibles' : 'Pas d\'offres actives'}</span>
          }
        </div>
        {company.website && (
          <div className="flex items-center gap-1.5 text-xs text-slate-500">
            <Globe className="w-3.5 h-3.5 shrink-0 text-slate-400" />
            <a
              href={company.website}
              target="_blank"
              rel="noopener noreferrer"
              className="truncate hover:text-indigo-500 transition-colors"
              onClick={e => e.stopPropagation()}
            >
              {company.website}
            </a>
          </div>
        )}
        {company.description && (
          <div className="flex items-start gap-1.5 text-xs text-slate-500">
            <AlignLeft className="w-3.5 h-3.5 shrink-0 text-slate-400 mt-0.5" />
            <p className="line-clamp-3 leading-relaxed">{company.description}</p>
          </div>
        )}
        {company.createdAt && (
          <div className="flex items-center gap-1.5 text-xs text-slate-400">
            <Calendar className="w-3.5 h-3.5 shrink-0" />
            <span>Ajouté le {new Date(company.createdAt).toLocaleDateString('fr-FR')}</span>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between">
        {company.company_url && (
          <a
            href={company.company_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-slate-300 hover:text-indigo-500 transition-colors"
            onClick={e => e.stopPropagation()}
          >
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
        )}
      </div>
    </Card>
  );
}
