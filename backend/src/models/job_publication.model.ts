import mongoose, { Document, Schema } from "mongoose";

export interface IJobPublication extends Document {
  job_id: string;
  published_at: Date | null;
}

const jobPublicationSchema = new Schema<IJobPublication>(
  {
    job_id: { type: String, required: true, unique: true },
    published_at: { type: Date, default: null },
  },
  { timestamps: true }
);

export default mongoose.model<IJobPublication>(
  "JobPublication",
  jobPublicationSchema,
  "job_publications_scrappe"
);
