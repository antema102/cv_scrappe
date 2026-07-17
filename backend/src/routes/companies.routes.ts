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

// GET /api/companies — liste paginée avec filtres + job_count
router.get("/", async (req: Request, res: Response): Promise<void> => {
  try {
    const page = Math.max(1, parseInt(req.query.page as string) || 1);
    const limit = Math.min(
      500,
      Math.max(1, parseInt(req.query.limit as string) || 24)
    );
    const skip = (page - 1) * limit;

    const filter: Record<string, unknown> = {};
    if (req.query.search) {
      filter.$or = [
        { name: { $regex: req.query.search, $options: "i" } },
        { sector: { $regex: req.query.search, $options: "i" } },
        { city: { $regex: req.query.search, $options: "i" } },
        { country: { $regex: req.query.search, $options: "i" } },
      ];
    }
    if (req.query.country) filter.country = req.query.country;
    if (req.query.sector) filter.sector = req.query.sector;

    const [companies, total] = await Promise.all([
      Company.find(filter, { __v: 0 })
        .skip(skip)
        .limit(limit)
        .sort({ createdAt: -1 }),
      Company.countDocuments(filter),
    ]);

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

export default router;
