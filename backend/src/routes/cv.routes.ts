import { Router, Request, Response } from "express";
import Cv from "../models/cv.model";

const router = Router();

// POST /api/cvs — upsert par id global (country_cvid)
router.post("/", async (req: Request, res: Response): Promise<void> => {
  try {
    const { id, country, cv_id, ...rest } = req.body as {
      id?: string;
      country?: string;
      cv_id?: number;
      [key: string]: unknown;
    };

    if (!id || !country || cv_id === undefined) {
      res.status(400).json({ error: "id, country et cv_id sont requis" });
      return;
    }

    const cv = await Cv.findOneAndUpdate(
      { id },
      { id, country, cv_id, ...rest },
      { upsert: true, new: true, runValidators: true }
    );

    res.status(201).json(cv);
  } catch (err) {
    res.status(500).json({ error: (err as Error).message });
  }
});

// GET /api/cvs/:id/exists — vérifie l'existence d'un CV par son id global
router.get("/:id/exists", async (req: Request, res: Response): Promise<void> => {
  try {
    const exists = await Cv.exists({ id: req.params.id });
    res.json({ exists: !!exists });
  } catch (err) {
    res.status(500).json({ error: (err as Error).message });
  }
});

// GET /api/cvs — liste paginée avec filtres
router.get("/", async (req: Request, res: Response): Promise<void> => {
  try {
    const page = Math.max(1, parseInt(req.query.page as string) || 1);
    const limit = Math.min(500, Math.max(1, parseInt(req.query.limit as string) || 20));
    const skip = (page - 1) * limit;

    const filter: Record<string, unknown> = {};
    if (req.query.country) filter.country = req.query.country;
    if (req.query.search) {
      filter.$or = [
        { nom: { $regex: req.query.search, $options: "i" } },
        { emails: { $regex: req.query.search, $options: "i" } },
      ];
    }

    const [cvs, total] = await Promise.all([
      Cv.find(filter, { __v: 0, texte: 0 })
        .skip(skip)
        .limit(limit)
        .sort({ downloaded_at: -1 }),
      Cv.countDocuments(filter),
    ]);

    res.json({ total, page, limit, cvs });
  } catch (err) {
    res.status(500).json({ error: (err as Error).message });
  }
});

// GET /api/cvs/:id — détail d'un CV
router.get("/:id", async (req: Request, res: Response): Promise<void> => {
  try {
    const cv = await Cv.findOne({ id: req.params.id }, { __v: 0 });
    if (!cv) {
      res.status(404).json({ error: "CV introuvable" });
      return;
    }
    res.json(cv);
  } catch (err) {
    res.status(500).json({ error: (err as Error).message });
  }
});

export default router;
