export interface Company {
  _id: string;
  company_id: string;
  company_url: string;
  name: string;
  city: string;
  country: string;
  sector: string;
  website: string;
  description: string;
  logo_url: string;
  has_jobs?: boolean;
  job_count?: number;
  createdAt: string;
  updatedAt: string;
}
