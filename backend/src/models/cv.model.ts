import mongoose, { Document, Schema } from "mongoose";

export interface ICv extends Document {
  id: string;
  country: string;
  cv_id: number;
  filename: string;
  filepath: string;
  nom: string;
  emails: string[];
  telephones: string[];
  texte: string;
  methode: string;
  sha256: string;
  downloaded_at: Date;
  analyzed_at: Date;
  statut: string;
  // Campagne emailing commerciale — écrits par l'outil d'emailing, pas par le scraper
  commercial_email_wave?: number;
  commercial_email_unsubscribed?: unknown;
  // Envoi vers l'IA WipWork (POST /parse/resume) — écrits par scrapers/ia_cv_uploader.py
  ia_sent?: boolean;
  ia_sent_at?: Date;
  ia_point_id?: string;
  ia_user_id?: string;
  ia_email?: string;
  ia_operation_type?: string;
  ia_storage_path?: string;
  ia_country_ids?: string[];
  ia_enterprise_ids?: string[];
  ia_duplicate_replaced?: boolean;
  ia_attempts?: number;
  ia_last_attempt_at?: Date;
  ia_last_error?: string;
  ia_skip_reason?: string;
}

const cvSchema = new Schema<ICv>(
  {
    id: { type: String, required: true, unique: true },
    country: { type: String, required: true },
    cv_id: { type: Number, required: true },
    filename: String,
    filepath: String,
    nom: String,
    emails: [String],
    telephones: [String],
    texte: String,
    methode: String,
    sha256: String,
    downloaded_at: Date,
    analyzed_at: Date,
    statut: { type: String, default: "ok" },

    // --- Campagne emailing commerciale ---------------------------------
    // Renseignés par l'outil d'emailing (écriture Mongo directe) ; déclarés
    // ici uniquement pour documenter les champs servant à la sélection IA.
    commercial_email_wave: Number,
    commercial_email_unsubscribed: Schema.Types.Mixed,

    // --- Envoi vers l'IA WipWork (/parse/resume) -----------------------
    ia_sent: { type: Boolean, default: false },
    ia_sent_at: Date,
    ia_point_id: String,
    ia_user_id: String,
    ia_email: String,
    ia_operation_type: String, // "created" | "updated"
    ia_storage_path: String,
    ia_country_ids: [String],
    ia_enterprise_ids: [String],
    ia_duplicate_replaced: Boolean,
    ia_attempts: { type: Number, default: 0 },
    ia_last_attempt_at: Date,
    ia_last_error: String,
    ia_skip_reason: String,
  },
  {
    timestamps: true,
    collection: "cvs_scrappe",
  }
);

// Contrainte d'unicité sur (country, cv_id) — évite les doublons inter-pays
cvSchema.index({ country: 1, cv_id: 1 }, { unique: true });

// Sélection des CV à envoyer à l'IA (GET /api/cvs/ia/pending)
cvSchema.index({ commercial_email_wave: 1, ia_sent: 1 });

export default mongoose.model<ICv>("Cv", cvSchema);
