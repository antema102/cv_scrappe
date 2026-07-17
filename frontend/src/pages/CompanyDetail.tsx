import {
  ArrowLeft, Building2, MapPin, Globe, AlignLeft, Hash,
  Calendar, ExternalLink, Briefcase, Tag, Link2,
  CheckCircle2, ChevronRight, Loader2, AlertCircle,
} from 'lucide-react';
import { useCompanyJobs } from '../hooks/useJobs';
import type { Company } from '../types/company';
import type { Job } from '../types/job';

interface CompanyDetailProps {
  company: Company;
  onBack: () => void;
}

/* ── helpers ── */
function InfoRow({ icon, label, value, link }: {
  icon: React.ReactNode;
  label: string;
  value?: string | null;
  link?: boolean;
}) {
  if (!value) return null;
  return (
    <div className="flex items-start gap-3 py-2.5 border-b border-slate-100 last:border-0">
      <span className="text-slate-400 mt-0.5 shrink-0">{icon}</span>
      <div className="min-w-0">
        <p className="text-xs text-slate-400 mb-0.5">{label}</p>
        {link ? (
          <a href={value} target="_blank" rel="noopener noreferrer"
            className="text-sm text-indigo-600 hover:underline break-all">{value}</a>
        ) : (
          <p className="text-sm text-slate-800 wrap-break-word">{value}</p>
        )}
      </div>
    </div>
  );
}

function JobCard({ job }: { job: Job }) {
  const criteria = job.detail?.criteria
    ? Object.entries(job.detail.criteria)
    : [];
  const sections = (job.detail as { sections?: { title?: string; content?: string }[] })?.sections ?? [];

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4">

      {/* titre + lien */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold text-slate-900 text-base leading-tight">
            {job.title || '(Sans titre)'}
          </h3>
          <p className="text-xs text-slate-400 mt-0.5 font-mono">{job.job_id}</p>
        </div>
        {job.job_url && (
          <a href={job.job_url} target="_blank" rel="noopener noreferrer"
            className="shrink-0 text-slate-300 hover:text-indigo-500 transition-colors mt-0.5">
            <ExternalLink className="w-4 h-4" />
          </a>
        )}
      </div>

      {/* headline */}
      {job.detail?.headline && (
        <p className="text-sm text-slate-600 italic border-l-2 border-indigo-200 pl-3">
          {job.detail.headline}
        </p>
      )}

      {/* description */}
      {job.detail?.description && (
        <div>
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1.5">Description</p>
          <p className="text-sm text-slate-700 whitespace-pre-line leading-relaxed">
            {job.detail.description}
          </p>
        </div>
      )}

      {/* critères */}
      {criteria.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Critères</p>
          <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1.5">
            {criteria.map(([k, v]) => (
              <div key={k} className="flex items-start gap-1.5">
                <ChevronRight className="w-3.5 h-3.5 text-slate-300 shrink-0 mt-0.5" />
                <div className="min-w-0">
                  <dt className="text-xs text-slate-400">{k}</dt>
                  <dd className="text-sm text-slate-700">{String(v)}</dd>
                </div>
              </div>
            ))}
          </dl>
        </div>
      )}

      {/* qualifications */}
      {(job.detail?.qualifications?.length ?? 0) > 0 && (
        <div>
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Qualifications</p>
          <ul className="space-y-1">
            {job.detail!.qualifications!.map((q, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-slate-700">
                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
                {q}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* compétences */}
      {(job.detail?.skills?.length ?? 0) > 0 && (
        <div>
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Compétences</p>
          <div className="flex flex-wrap gap-1.5">
            {job.detail!.skills!.map((s, i) => (
              <span key={i}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 text-xs font-medium">
                <Tag className="w-2.5 h-2.5" />{s}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* sections libres */}
      {sections.map((sec, i) => (
        <div key={i}>
          {sec.title && (
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1.5">{sec.title}</p>
          )}
          {sec.content && (
            <p className="text-sm text-slate-700 whitespace-pre-line leading-relaxed">{sec.content}</p>
          )}
        </div>
      ))}

      {/* dates */}
      <div className="flex items-center gap-4 pt-2 border-t border-slate-100">
        {job.createdAt && (
          <span className="text-xs text-slate-400">
            Ajouté le {new Date(job.createdAt).toLocaleDateString('fr-FR')}
          </span>
        )}
        {job.updatedAt && job.updatedAt !== job.createdAt && (
          <span className="text-xs text-slate-400">
            Mis à jour le {new Date(job.updatedAt).toLocaleDateString('fr-FR')}
          </span>
        )}
      </div>
    </div>
  );
}

/* ── page principale ── */
export function CompanyDetail({ company, onBack }: CompanyDetailProps) {
  const { jobs, total, loading, error } = useCompanyJobs(company.company_id, company.country || undefined);

  return (
    <main className="max-w-7xl mx-auto px-4 sm:px-6 py-8 space-y-8">

      {/* bouton retour */}
      <button
        onClick={onBack}
        className="inline-flex items-center gap-2 text-sm text-slate-500 hover:text-indigo-600 transition-colors"
      >
        <ArrowLeft className="w-4 h-4" />
        Retour au tableau de bord
      </button>

      {/* ── Fiche entreprise ── */}
      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">

        {/* en-tête coloré */}
        <div className="bg-linear-to-r from-indigo-50 to-violet-50 px-6 py-6 flex items-start gap-5">
          {company.logo_url ? (
            <img
              src={company.logo_url}
              alt={company.name}
              className="w-16 h-16 rounded-xl object-contain border border-white bg-white shadow-sm shrink-0 p-1"
              onError={e => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
            />
          ) : (
            <div className="w-16 h-16 rounded-xl bg-white border border-white shadow-sm flex items-center justify-center shrink-0">
              <Building2 className="w-8 h-8 text-indigo-300" />
            </div>
          )}
          <div>
            <h1 className="text-xl font-bold text-slate-900 leading-tight">
              {company.name || '—'}
            </h1>
            {company.sector && (
              <p className="text-sm text-slate-500 mt-0.5">{company.sector}</p>
            )}
            <span className={`inline-block mt-2 px-2.5 py-0.5 rounded-full text-xs font-semibold
              ${company.has_jobs ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>
              {company.has_jobs ? 'Recrute actuellement' : 'Inactif'}
            </span>
          </div>
        </div>

        {/* détails */}
        <div className="px-6 py-4 divide-y divide-slate-100">
          <InfoRow icon={<Hash className="w-4 h-4" />} label="ID entreprise" value={company.company_id} />
          <InfoRow icon={<MapPin className="w-4 h-4" />} label="Localisation"
            value={[company.city, company.country].filter(Boolean).join(', ')} />
          <InfoRow icon={<Link2 className="w-4 h-4" />} label="Profil recruteur"
            value={company.company_url} link />
          <InfoRow icon={<Globe className="w-4 h-4" />} label="Site web"
            value={company.website} link />
          <InfoRow icon={<AlignLeft className="w-4 h-4" />} label="Description"
            value={company.description} />
          <InfoRow icon={<Calendar className="w-4 h-4" />} label="Ajouté le"
            value={company.createdAt ? new Date(company.createdAt).toLocaleDateString('fr-FR') : null} />
          <InfoRow icon={<Calendar className="w-4 h-4" />} label="Mis à jour le"
            value={company.updatedAt ? new Date(company.updatedAt).toLocaleDateString('fr-FR') : null} />
        </div>
      </div>

      {/* ── Offres d'emploi ── */}
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <Briefcase className="w-5 h-5 text-slate-400" />
          <h2 className="font-semibold text-slate-900">Offres d'emploi</h2>
          {!loading && (
            <span className="text-sm text-slate-400">
              {total.toLocaleString('fr-FR')} offre{total > 1 ? 's' : ''}
            </span>
          )}
        </div>

        {loading && (
          <div className="flex items-center justify-center py-16 text-slate-400 gap-2">
            <Loader2 className="w-5 h-5 animate-spin" />
            <span className="text-sm">Chargement des offres…</span>
          </div>
        )}

        {error && (
          <div className="flex items-center gap-2 p-4 bg-red-50 rounded-lg text-red-600 text-sm">
            <AlertCircle className="w-4 h-4 shrink-0" />
            {error}
          </div>
        )}

        {!loading && !error && jobs.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-slate-400">
            <Briefcase className="w-8 h-8 mb-2 opacity-30" />
            <p className="text-sm">Aucune offre trouvée pour cette entreprise</p>
          </div>
        )}

        {!loading && jobs.map(job => (
          <JobCard key={job._id} job={job} />
        ))}
      </div>
    </main>
  );
}
