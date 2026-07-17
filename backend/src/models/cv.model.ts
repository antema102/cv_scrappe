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
  },
  {
    timestamps: true,
    collection: "cvs",
  }
);

// Contrainte d'unicité sur (country, cv_id) — évite les doublons inter-pays
cvSchema.index({ country: 1, cv_id: 1 }, { unique: true });

export default mongoose.model<ICv>("Cv", cvSchema);
