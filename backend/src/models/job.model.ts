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

// Qualité d'une offre extraite par l'IA (script Facebook, facebook_script/ai_checks.py) ; absente pour les autres
// sources. verified = intitulé retrouvé dans la publication ; checks = contacts/champs retirés car absents de la
// publication, éléments non vérifiables ; description_source = publication | ia_generee | publication_brute | assemblee.
interface IJobQuality {
  verified?: boolean;
  translated?: boolean;
  description_generated?: boolean;
  description_source?: string;
  checks?: string[];
}

export interface IJob extends Document {
  job_id: string;
  title?: string;
  job_url?: string;
  company_name?: string;
  company_url?: string;
  company_id?: string;
  country?: string; // nom du pays (même valeur que company_profile.country, filtre du frontend)
  language?: string; // langue du texte enregistré : fr, en ("" = inconnue)
  source_language?: string; // langue d'origine de l'annonce (ISO 639-1 : ar, mg, pt...) avant traduction
  quality?: IJobQuality;
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

const jobQualitySchema = new Schema<IJobQuality>(
  {
    verified: Boolean,
    translated: Boolean,
    description_generated: Boolean,
    description_source: String,
    checks: [String],
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
    company_id: { type: String, index: true },
    country: { type: String, index: true },
    language: String,
    source_language: String,
    quality: jobQualitySchema,
    detail: jobDetailSchema,
    company_profile: Schema.Types.Mixed,
  },
  { timestamps: true }
);

export default mongoose.model<IJob>("Job", jobSchema, "jobs_scrappe");
