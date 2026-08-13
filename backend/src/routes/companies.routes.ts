import { Router, Request, Response } from "express";
import Company from "../models/company.model";
import Job from "../models/job.model";

const router = Router();

// POST /api/companies — upsert par company_id
router.post("/", async (req: Request, res: Response): Promise<void> => {
  try {
    const { company_id, ...rest } = req.body as {
      company_id?: string;
      [key: string]: unknown;
    };

    if (!company_id) {
      res.status(400).json({ error: "company_id est requis" });
      return;
    }

    const company = await Company.findOneAndUpdate(
      { company_id },
      { company_id, ...rest },
      { upsert: true, new: true, runValidators: true }
    );

    res.status(201).json(company);
  } catch (err) {
    res.status(500).json({ error: (err as Error).message });
  }
});

// Valeurs de "website" considérées comme absentes/invalides — insensible à la
// casse et aux espaces superflus (le champ contient parfois "Not available",
// "Non disponible.", etc. selon le site source). Miroir de _INVALID_WEBSITE_VALUES
// dans scrapers/emails_website.py.
const INVALID_WEBSITE_REGEX =
  /^\s*(non disponible\.?|n\/a|na|null|none|not available\.?)?\s*$/i;

// GET /api/companies — liste paginée avec filtres + job_count
// ?scrape=1 : retourne les entreprises avec website et sans emails (pour le scraper)
// ?missingEmail=1 : même condition (website valide et non vide, sans email), mais
// composable avec search/country/sector et paginée normalement (pour le dashboard)
router.get("/", async (req: Request, res: Response): Promise<void> => {
  try {
    const isScrape = req.query.scrape === "1";
    const missingEmail = req.query.missingEmail === "1";
    const maxLimit = isScrape ? 10000 : 500;
    const defaultLimit = isScrape ? 10000 : 24;
    const page = Math.max(1, parseInt(req.query.page as string) || 1);
    const limit = Math.min(
      maxLimit,
      Math.max(1, parseInt(req.query.limit as string) || defaultLimit)
    );
    const skip = (page - 1) * limit;

    const filter: Record<string, unknown> = {};
    const andConditions: Record<string, unknown>[] = [];

    if (!isScrape && req.query.search) {
      andConditions.push({
        $or: [
          { name: { $regex: req.query.search, $options: "i" } },
          { sector: { $regex: req.query.search, $options: "i" } },
          { city: { $regex: req.query.search, $options: "i" } },
          { country: { $regex: req.query.search, $options: "i" } },
        ],
      });
    }
    if (req.query.country) filter.country = req.query.country;
    if (req.query.sector) filter.sector = req.query.sector;

    if (isScrape || missingEmail) {
      filter.website = {
        $exists: true,
        $not: INVALID_WEBSITE_REGEX,
      };
      andConditions.push({
        $or: [
          { emails: { $exists: false } },
          { emails: { $size: 0 } },
        ],
      });
    }

    if (andConditions.length > 0) {
      filter.$and = andConditions;
    }

    const projection = isScrape
      ? { __v: 0, description: 0, logo_url: 0, emails: 0, phone_numbers: 0 }
      : { __v: 0 };

    const [companies, total] = await Promise.all([
      Company.find(filter, projection)
        .skip(skip)
        .limit(limit)
        .sort({ createdAt: -1 }),
      Company.countDocuments(filter),
    ]);

    // En mode scraper, pas besoin de job_count
    if (isScrape) {
      res.json({ total, page, limit, companies });
      return;
    }

    // Compter les offres d'emploi par company_id en une seule requête
    const companyIds = companies.map((c) => c.company_id);
    const jobCounts = await Job.aggregate([
      { $match: { company_id: { $in: companyIds } } },
      { $group: { _id: "$company_id", count: { $sum: 1 } } },
    ]);
    const countMap: Record<string, number> = Object.fromEntries(
      jobCounts.map((r) => [r._id as string, r.count as number])
    );

    const result = companies.map((c) => ({
      ...c.toObject(),
      job_count: countMap[c.company_id] ?? 0,
    }));

    res.json({ total, page, limit, companies: result });
  } catch (err) {
    res.status(500).json({ error: (err as Error).message });
  }
});

// GET /api/companies/:company_id — détail d'une entreprise
router.get(
  "/:company_id",
  async (req: Request, res: Response): Promise<void> => {
    try {
      const company = await Company.findOne(
        { company_id: req.params.company_id },
        { __v: 0 }
      );
      if (!company) {
        res.status(404).json({ error: "Entreprise introuvable" });
        return;
      }
      res.json(company);
    } catch (err) {
      res.status(500).json({ error: (err as Error).message });
    }
  }
);

// PATCH /api/companies/:company_id/contact — merge emails + téléphones (scraper)
router.patch(
  "/:company_id/contact",
  async (req: Request, res: Response): Promise<void> => {
    try {
      const { company_id } = req.params;
      const { emails, phone_numbers } = req.body as {
        emails?: unknown;
        phone_numbers?: unknown;
      };

      // Validation : au moins un des deux champs requis
      if (emails === undefined && phone_numbers === undefined) {
        res.status(400).json({ error: "emails ou phone_numbers est requis" });
        return;
      }

      // Validation des types
      if (
        emails !== undefined &&
        (!Array.isArray(emails) || emails.some((e) => typeof e !== "string"))
      ) {
        res.status(400).json({ error: "emails doit être un tableau de chaînes" });
        return;
      }
      if (
        phone_numbers !== undefined &&
        (!Array.isArray(phone_numbers) ||
          phone_numbers.some((p) => typeof p !== "string"))
      ) {
        res
          .status(400)
          .json({ error: "phone_numbers doit être un tableau de chaînes" });
        return;
      }

      const cleanEmails = ((emails as string[] | undefined) ?? []).map((e) =>
        e.trim().toLowerCase()
      ).filter(Boolean);

      const cleanPhones = ((phone_numbers as string[] | undefined) ?? []).map(
        (p) => p.trim()
      ).filter(Boolean);

      // $addToSet avec $each : MongoDB dédoublonne nativement
      const update: Record<string, unknown> = {};
      if (cleanEmails.length > 0)
        update["emails"] = { $each: cleanEmails };
      if (cleanPhones.length > 0)
        update["phone_numbers"] = { $each: cleanPhones };

      if (Object.keys(update).length === 0) {
        res.status(400).json({ error: "Aucune donnée valide à enregistrer" });
        return;
      }

      const company = await Company.findOneAndUpdate(
        { company_id },
        { $addToSet: update },
        { new: true, runValidators: true }
      );

      if (!company) {
        res.status(404).json({ error: "Entreprise introuvable" });
        return;
      }

      res.json({
        success: true,
        company_id,
        emails_added: cleanEmails.length,
        phone_numbers_added: cleanPhones.length,
      });
    } catch (err) {
      res.status(500).json({ error: (err as Error).message });
    }
  }
);

export default router;
