import { fetchCompanies } from '../api/companies';
import type { Company } from '../types/company';

export async function getCompanies(limit = 200): Promise<Company[]> {
  const data = await fetchCompanies(1, limit);
  return data.companies;
}

export function filterCompanies(
  companies: Company[],
  { search, country, sector }: { search?: string; country?: string; sector?: string },
): Company[] {
  return companies.filter(c => {
    if (search) {
      const q = search.toLowerCase();
      const match =
        c.name?.toLowerCase().includes(q) ||
        c.sector?.toLowerCase().includes(q) ||
        c.country?.toLowerCase().includes(q) ||
        c.city?.toLowerCase().includes(q);
      if (!match) return false;
    }
    if (country && c.country !== country) return false;
    if (sector && c.sector !== sector) return false;
    return true;
  });
}

export function getUniqueCountries(companies: Company[]): string[] {
  return [...new Set(companies.map(c => c.country).filter(Boolean))].sort();
}

export function getUniqueSectors(companies: Company[]): string[] {
  return [...new Set(companies.map(c => c.sector).filter(Boolean))].sort();
}
