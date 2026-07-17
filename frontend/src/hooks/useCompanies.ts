import { useEffect, useState } from 'react';
import { fetchCompanies } from '../api/companies';
import { filterCompanies } from '../services/companiesService';
import type { Company } from '../types/company';

interface Options {
  search?: string;
  country?: string;
  sector?: string;
  page?: number;
}

export const COMPANIES_PER_PAGE = 24;

export function useCompanies(options: Options = {}) {
  const { search, country, sector, page = 1 } = options;

  // Données paginées + filtrées côté serveur (pour la grille)
  const [companies, setCompanies] = useState<Company[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  // Toutes les entreprises (une seule fois) pour alimenter les dropdowns
  const [allCompanies, setAllCompanies] = useState<Company[]>([]);

  // Chargement initial pour les filtres
  useEffect(() => {
    fetchCompanies(1, 1000)
      .then(res => setAllCompanies(res.companies))
      .catch(() => {});
  }, []);

  // Rechargement à chaque changement de filtre ou de page
  useEffect(() => {
    setLoading(true);
    fetchCompanies(page, COMPANIES_PER_PAGE, { search, country, sector })
      .then(res => { setCompanies(res.companies); setTotal(res.total); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [page, search, country, sector]);

  const totalPages = Math.max(1, Math.ceil(total / COMPANIES_PER_PAGE));

  return { companies, allCompanies, total, totalPages, loading };
}

export { filterCompanies };

