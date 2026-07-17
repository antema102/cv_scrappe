import type { Company } from './company';
import type { Job } from './job';

export interface CompaniesResponse {
  total: number;
  page: number;
  limit: number;
  companies: Company[];
}

export interface JobsResponse {
  total: number;
  page: number;
  limit: number;
  jobs: Job[];
}
