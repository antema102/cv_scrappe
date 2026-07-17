import { useEffect, useState } from 'react';
import { fetchCompanies } from '../api/companies';
import { fetchJobs } from '../api/jobs';
import { getUniqueCountries, getUniqueSectors } from '../services/companiesService';

export interface Stats {
  totalCompanies: number;
  totalJobs: number;
  totalCountries: number;
  totalSectors: number;
}

export function useStats() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([fetchCompanies(1, 500), fetchJobs(1, 1)])
      .then(([companiesRes, jobsRes]) => {
        setStats({
          totalCompanies: companiesRes.total,
          totalJobs: jobsRes.total,
          totalCountries: getUniqueCountries(companiesRes.companies).length,
          totalSectors: getUniqueSectors(companiesRes.companies).length,
        });
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  return { stats, loading };
}
