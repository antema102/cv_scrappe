import { Router, Request, Response } from "express";
import Company from "../models/company.model";

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

// GET /api/companies — liste paginée
router.get("/", async (req: Request, res: Response): Promise<void> => {
  try {
    const page = Math.max(1, parseInt(req.query.page as string) || 1);
    const limit = Math.min(
      100,
      Math.max(1, parseInt(req.query.limit as string) || 20)
    );
    const skip = (page - 1) * limit;

    const [companies, total] = await Promise.all([
      Company.find({}, { __v: 0 })
        .skip(skip)
        .limit(limit)
        .sort({ createdAt: -1 }),
      Company.countDocuments(),
    ]);

    res.json({ total, page, limit, companies });
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
