import { MapPin, Clock, ExternalLink } from 'lucide-react';
import { Badge } from '../ui/Badge';
import { timeAgo } from '../../utils/formatDate';
import { getJobLocation, getContractType } from '../../services/jobsService';
import type { Job } from '../../types/job';
import type { BadgeProps } from './types';

const contractBadge: Record<string, BadgeProps['variant']> = {
  CDI: 'success',
  CDD: 'info',
  Stage: 'warning',
  Freelance: 'default',
  Anapec: 'info',
};

interface JobCardProps {
  job: Job;
}

export function JobCard({ job }: JobCardProps) {
  const location = getJobLocation(job);
  const contract = getContractType(job);

  return (
    <div className="flex items-start justify-between gap-3 px-4 py-3.5 rounded-lg hover:bg-slate-50 transition-colors group">
      <div className="flex-1 min-w-0">
        <h4 className="font-medium text-slate-900 text-sm truncate leading-snug group-hover:text-indigo-600 transition-colors mb-0.5">
          {job.title}
        </h4>
        <p className="text-xs text-slate-500 mb-2 truncate">{job.company_name}</p>
        <div className="flex flex-wrap items-center gap-2">
          {contract && (
            <Badge variant={contractBadge[contract] ?? 'default'}>{contract}</Badge>
          )}
          {location && (
            <span className="flex items-center gap-1 text-xs text-slate-400">
              <MapPin className="w-3 h-3" />
              <span className="max-w-[120px] truncate">{location}</span>
            </span>
          )}
          <span className="flex items-center gap-1 text-xs text-slate-400">
            <Clock className="w-3 h-3" />
            {timeAgo(job.createdAt)}
          </span>
        </div>
      </div>
      {job.job_url && (
        <a
          href={job.job_url}
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0 flex items-center gap-1 text-xs text-slate-400 hover:text-indigo-600 transition-colors border border-slate-200 rounded-lg px-2.5 py-1.5 hover:border-indigo-300 hover:bg-indigo-50"
        >
          <ExternalLink className="w-3.5 h-3.5" />
          Voir
        </a>
      )}
    </div>
  );
}
