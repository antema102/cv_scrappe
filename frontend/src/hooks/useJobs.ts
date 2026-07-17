import { useEffect, useState } from 'react';
import { getRecentJobs } from '../services/jobsService';
import { fetchJobsByCompany } from '../api/jobs';
import type { Job } from '../types/job';

export function useRecentJobs(limit = 15) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getRecentJobs(limit)
      .then(setJobs)
      .catch(err => setError((err as Error).message))
      .finally(() => setLoading(false));
  }, [limit]);

  return { jobs, loading, error };
}

export function useCompanyJobs(company_id: string | null, country?: string) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!company_id) return;
    setLoading(true);
    fetchJobsByCompany(company_id, country)
      .then(res => { setJobs(res.jobs); setTotal(res.total); })
      .catch(err => setError((err as Error).message))
      .finally(() => setLoading(false));
  }, [company_id, country]);

  return { jobs, total, loading, error };
}
