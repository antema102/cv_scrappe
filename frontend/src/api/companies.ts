import { API_BASE_URL } from './config';
import type { CompaniesResponse } from '../types/api';

interface CompanyFilters {
  search?: string;
  country?: string;
  sector?: string;
}

export async function fetchCompanies(
  page = 1,
  limit = 24,
  filters: CompanyFilters = {}
): Promise<CompaniesResponse> {
  const params = new URLSearchParams({ page: String(page), limit: String(limit) });
  if (filters.search) params.set('search', filters.search);
  if (filters.country) params.set('country', filters.country);
  if (filters.sector) params.set('sector', filters.sector);
  const res = await fetch(`${API_BASE_URL}/api/companies?${params.toString()}`);
  if (!res.ok) throw new Error(`Erreur API companies: HTTP ${res.status}`);
  return res.json();
}
