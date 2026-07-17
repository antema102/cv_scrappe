import { useState } from 'react';
import { Building2, Briefcase, Globe, Layers } from 'lucide-react';
import { StatsCard } from '../components/dashboard/StatsCard';
import { SearchFilters } from '../components/dashboard/SearchFilters';
import { CompanyGrid } from '../components/dashboard/CompanyGrid';
import { RecentJobs } from '../components/dashboard/RecentJobs';
import { CompanyDetail } from './CompanyDetail';
import { Pagination } from '../components/ui/Pagination';
import { useCompanies, COMPANIES_PER_PAGE } from '../hooks/useCompanies';
import { useRecentJobs } from '../hooks/useJobs';
import { useStats } from '../hooks/useStats';
import { getUniqueCountries, getUniqueSectors } from '../services/companiesService';
import type { Company } from '../types/company';

export function Dashboard() {
  const [search, setSearch] = useState('');
  const [country, setCountry] = useState('');
  const [sector, setSector] = useState('');
  const [page, setPage] = useState(1);
  const [selectedCompany, setSelectedCompany] = useState<Company | null>(null);

  const { companies, allCompanies, total, totalPages, loading: loadingCompanies } = useCompanies({ search, country, sector, page });
  const { jobs, loading: loadingJobs } = useRecentJobs(15);
  const { stats, loading: loadingStats } = useStats();

  const countries = getUniqueCountries(allCompanies);
  const sectors = getUniqueSectors(allCompanies);

  // Réinitialiser la page quand les filtres changent
  function handleSearch(v: string) { setSearch(v); setPage(1); }
  function handleCountry(v: string) { setCountry(v); setPage(1); }
  function handleSector(v: string) { setSector(v); setPage(1); }

  if (selectedCompany) {
    return <CompanyDetail company={selectedCompany} onBack={() => setSelectedCompany(null)} />;
  }

  return (
    <main className="max-w-screen-2xl mx-auto px-4 sm:px-6 py-8 space-y-8">

      {/* Cartes statistiques */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatsCard
          title="Entreprises"
          value={stats?.totalCompanies ?? 0}
          icon={<Building2 className="w-5 h-5 text-indigo-600" />}
          iconBg="bg-indigo-50"
          loading={loadingStats}
        />
        <StatsCard
          title="Offres d'emploi"
          value={stats?.totalJobs ?? 0}
          icon={<Briefcase className="w-5 h-5 text-emerald-600" />}
          iconBg="bg-emerald-50"
          loading={loadingStats}
        />
        <StatsCard
          title="Pays couverts"
          value={stats?.totalCountries ?? 0}
          icon={<Globe className="w-5 h-5 text-violet-600" />}
          iconBg="bg-violet-50"
          loading={loadingStats}
        />
        <StatsCard
          title="Secteurs"
          value={stats?.totalSectors ?? 0}
          icon={<Layers className="w-5 h-5 text-amber-600" />}
          iconBg="bg-amber-50"
          loading={loadingStats}
        />
      </div>

      {/* Corps : entreprises + offres récentes */}
      <div className="grid grid-cols-1 xl:grid-cols-[1fr_380px] gap-6 items-start">

        {/* Colonne gauche : entreprises */}
        <div className="space-y-4">
          <div className="flex items-baseline gap-2">
            <h2 className="font-semibold text-slate-900">Entreprises</h2>
            {!loadingCompanies && (
              <span className="text-sm text-slate-400">
                {total.toLocaleString('fr-FR')} résultat{total > 1 ? 's' : ''}
              </span>
            )}
          </div>
          <SearchFilters
            search={search}
            country={country}
            sector={sector}
            countries={countries}
            sectors={sectors}
            onSearch={handleSearch}
            onCountry={handleCountry}
            onSector={handleSector}
          />
          <CompanyGrid companies={companies} loading={loadingCompanies} onSelect={setSelectedCompany} />
          <Pagination
            page={page}
            totalPages={totalPages}
            total={total}
            perPage={COMPANIES_PER_PAGE}
            onPage={setPage}
          />
        </div>

        {/* Colonne droite : offres récentes */}
        <div className="xl:sticky xl:top-20">
          <div className="flex items-baseline gap-2 mb-4">
            <h2 className="font-semibold text-slate-900">Offres récentes</h2>
          </div>
          <RecentJobs jobs={jobs} loading={loadingJobs} />
        </div>

      </div>
    </main>
  );
}
