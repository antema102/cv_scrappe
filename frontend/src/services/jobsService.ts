import { fetchJobs } from '../api/jobs';
import type { Job } from '../types/job';

export async function getRecentJobs(limit = 15): Promise<Job[]> {
  const data = await fetchJobs(1, limit);
  return data.jobs;
}

export function getJobLocation(job: Job): string {
  return (
    job.detail?.criteria?.['Région de'] ??
    job.detail?.criteria?.['Region'] ??
    job.company_profile?.city ??
    ''
  );
}

export function getContractType(job: Job): string {
  return (
    job.detail?.criteria?.['Contrat proposé'] ??
    job.detail?.criteria?.['Contract'] ??
    ''
  );
}
