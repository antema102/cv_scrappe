import { Briefcase } from 'lucide-react';
import { Card } from '../ui/Card';
import { JobCard } from './JobCard';
import { Skeleton } from '../ui/Skeleton';
import type { Job } from '../../types/job';

interface RecentJobsProps {
  jobs: Job[];
  total?: number;
  loading?: boolean;
}

export function RecentJobs({ jobs, loading }: RecentJobsProps) {
  return (
    <Card className="overflow-hidden">
      <div className="px-4 py-3.5 border-b border-slate-100 flex items-center gap-2">
        <Briefcase className="w-4 h-4 text-indigo-500" />
        <h2 className="font-semibold text-slate-900 text-sm">Offres récentes</h2>
        {!loading && (
          <span className="ml-auto text-xs text-slate-400 bg-slate-100 px-2 py-0.5 rounded-full">
            {jobs.length}
          </span>
        )}
      </div>

      <div className="py-1">
        {loading ? (
          Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="flex gap-3 px-4 py-3.5">
              <div className="flex-1">
                <Skeleton className="h-4 w-3/4 mb-2" />
                <Skeleton className="h-3 w-1/3 mb-2.5" />
                <Skeleton className="h-5 w-16 rounded-md" />
              </div>
              <Skeleton className="h-8 w-14 rounded-lg shrink-0" />
            </div>
          ))
        ) : jobs.length === 0 ? (
          <div className="text-center py-12 text-slate-400">
            <Briefcase className="w-8 h-8 mx-auto mb-2 opacity-25" />
            <p className="text-sm">Aucune offre disponible</p>
          </div>
        ) : (
          jobs.map(job => <JobCard key={job.job_id || job._id} job={job} />)
        )}
      </div>
    </Card>
  );
}
