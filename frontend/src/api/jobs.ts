import { API_BASE_URL } from './config';
import type { JobsResponse } from '../types/api';

export async function fetchJobs(page = 1, limit = 20): Promise<JobsResponse> {
    const res = await fetch(`${API_BASE_URL}/api/jobs?page=${page}&limit=${limit}`);
    if (!res.ok) throw new Error(`Erreur API jobs: HTTP ${res.status}`);
    return res.json();
}

export async function fetchJobsByCompany(company_id: string, country?: string): Promise<JobsResponse> {
    const params = new URLSearchParams({ company_id, limit: '500' });
    if (country) params.set('country', country);
    const res = await fetch(`${API_BASE_URL}/api/jobs?${params.toString()}`);
    if (!res.ok) throw new Error(`Erreur API jobs: HTTP ${res.status}`);
    return res.json();
}
