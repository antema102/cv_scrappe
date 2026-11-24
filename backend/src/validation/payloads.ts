// Validation des documents reçus par POST /api/jobs et POST /api/companies, AVANT l'écriture en base.
// Mongoose supprime en silence un champ hors modèle et accepte une chaîne vide : sans ce contrôle, une offre sans
// intitulé ou un champ du mauvais type (liste au lieu de texte) entrait en base sans erreur.
// Réponse 400 avec la liste des problèmes : les scrapers (backend_push.py, ApiClient) affichent l'erreur et ne
// marquent pas le document comme envoyé — il repart au lancement suivant une fois corrigé.

type Json = Record<string, unknown>;

const isObject = (value: unknown): value is Json =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isStringArray = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((item) => typeof item === "string");

function requireText(body: Json, field: string, errors: string[], label = field): void {
  const value = body[field];
  if (typeof value !== "string" || !value.trim()) {
    errors.push(`${label} est requis (texte non vide)`);
  }
}

function optionalTexts(body: Json, fields: string[], errors: string[], prefix = ""): void {
  for (const field of fields) {
    const value = body[field];
    if (value !== undefined && value !== null && typeof value !== "string") {
      errors.push(`${prefix}${field} doit être un texte`);
    }
  }
}

function optionalTextLists(body: Json, fields: string[], errors: string[], prefix = ""): void {
  for (const field of fields) {
    const value = body[field];
    if (value !== undefined && value !== null && !isStringArray(value)) {
      errors.push(`${prefix}${field} doit être une liste de textes`);
    }
  }
}

function optionalObject(body: Json, field: string, errors: string[], prefix = ""): Json | undefined {
  const value = body[field];
  if (value === undefined || value === null) return undefined;
  if (!isObject(value)) {
    errors.push(`${prefix}${field} doit être un objet`);
    return undefined;
  }
  return value;
}

export function validateJob(body: unknown): string[] {
  if (!isObject(body)) return ["corps JSON attendu (objet)"];
  const errors: string[] = [];
  requireText(body, "job_id", errors);
  requireText(body, "title", errors, "title (intitulé de l'offre)");
  optionalTexts(body, ["job_url", "company_name", "company_url", "company_id", "country", "language", "source_language"], errors);

  const detail = optionalObject(body, "detail", errors);
  if (detail) {
    optionalTexts(detail, ["job_url", "headline", "description"], errors, "detail.");
    optionalTextLists(detail, ["qualifications", "skills"], errors, "detail.");
    optionalObject(detail, "criteria", errors, "detail.");
    const sections = detail.sections;
    if (sections !== undefined && sections !== null && !(Array.isArray(sections) && sections.every(isObject))) {
      errors.push("detail.sections doit être une liste d'objets");
    }
  }
  optionalObject(body, "company_profile", errors);

  const quality = optionalObject(body, "quality", errors);
  if (quality) {
    for (const field of ["verified", "translated", "description_generated"]) {
      const value = quality[field];
      if (value !== undefined && value !== null && typeof value !== "boolean") {
        errors.push(`quality.${field} doit être vrai/faux`);
      }
    }
    optionalTexts(quality, ["description_source"], errors, "quality.");
    optionalTextLists(quality, ["checks"], errors, "quality.");
  }
  return errors;
}

export function validateCompany(body: unknown): string[] {
  if (!isObject(body)) return ["corps JSON attendu (objet)"];
  const errors: string[] = [];
  requireText(body, "company_id", errors);
  optionalTexts(
    body,
    ["company_url", "name", "city", "country", "sector", "website", "description", "logo_url"],
    errors
  );
  optionalTextLists(body, ["emails", "phone_numbers"], errors);
  return errors;
}
