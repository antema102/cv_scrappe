import mongoose, { Document, Schema } from "mongoose";

export interface ICompany extends Document {
  company_id: string;
  company_url?: string;
  name?: string;
  city?: string;
  country?: string;
  sector?: string;
  website?: string;
  description?: string;
  logo_url?: string;
}

const companySchema = new Schema<ICompany>(
  {
    company_id: { type: String, required: true, unique: true },
    company_url: String,
    name: String,
    city: String,
    country: String,
    sector: String,
    website: String,
    description: String,
    logo_url: String,
  },
  { timestamps: true }
);

export default mongoose.model<ICompany>("Company", companySchema);
