import mongoose, { Document, Schema } from "mongoose";

interface IJobDetail {
  job_url?: string;
  headline?: string;
  description?: string;
  qualifications?: string[];
  criteria?: Record<string, unknown>;
  skills?: string[];
  sections?: Record<string, unknown>[];
}

export interface IJob extends Document {
  job_id: string;
  title?: string;
  job_url?: string;
  company_name?: string;
  company_url?: string;
  company_id?: string;
  detail?: IJobDetail;
  company_profile?: Record<string, unknown>;
}

const jobDetailSchema = new Schema<IJobDetail>(
  {
    job_url: String,
    headline: String,
    description: String,
    qualifications: [String],
    criteria: Schema.Types.Mixed,
    skills: [String],
    sections: [Schema.Types.Mixed],
  },
  { _id: false }
);

const jobSchema = new Schema<IJob>(
  {
    job_id: { type: String, required: true, unique: true },
    title: String,
    job_url: String,
    company_name: String,
    company_url: String,
    company_id: String,
    detail: jobDetailSchema,
    company_profile: Schema.Types.Mixed,
  },
  { timestamps: true }
);

export default mongoose.model<IJob>("Job", jobSchema, "jobs_scrappe");
