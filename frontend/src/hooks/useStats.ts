import { useEffect, useState } from 'react';
import { fetchCompanies } from '../api/companies';
import { fetchJobs } from '../api/jobs';
import { getUniqueCountries, getUniqueSectors } from '../services/companiesService';

export interface Stats {
  totalCompanies: number;
  totalJobs: number;
  totalCountries: number;
  totalSectors: number;
  companiesMissingEmail: number;
}

export function useStats() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      fetchCompanies(1, 500),
      fetchJobs(1, 1),
      fetchCompanies(1, 1, { missingEmail: true }),
    ])
      .then(([companiesRes, jobsRes, missingEmailRes]) => {
        setStats({
          totalCompanies: companiesRes.total,
          totalJobs: jobsRes.total,
          totalCountries: getUniqueCountries(companiesRes.companies).length,
          totalSectors: getUniqueSectors(companiesRes.companies).length,
          companiesMissingEmail: missingEmailRes.total,
        });
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  return { stats, loading };
}
