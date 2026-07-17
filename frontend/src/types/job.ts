import type { Company } from './company';

export interface JobDetail {
  job_url?: string;
  headline?: string;
  description?: string;
  qualifications?: string[];
  criteria?: Record<string, string>;
  skills?: string[];
}

export interface Job {
  _id: string;
  job_id: string;
  title: string;
  job_url: string;
  company_name: string;
  company_url: string;
  company_id: string;
  detail?: JobDetail;
  company_profile?: Partial<Company>;
  createdAt: string;
  updatedAt: string;
}
