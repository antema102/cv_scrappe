import { Router, Request, Response } from "express";
import Job from "../models/job.model";

const router = Router();

// POST /api/jobs — upsert par job_id
router.post("/", async (req: Request, res: Response): Promise<void> => {
  try {
    const { job_id, ...rest } = req.body as {
      job_id?: string;
      [key: string]: unknown;
    };

    if (!job_id) {
      res.status(400).json({ error: "job_id est requis" });
      return;
    }

    const job = await Job.findOneAndUpdate(
      { job_id },
      { job_id, ...rest },
      { upsert: true, new: true, runValidators: true }
    );

    res.status(201).json(job);
  } catch (err) {
    res.status(500).json({ error: (err as Error).message });
  }
});

// GET /api/jobs — liste paginée
router.get("/", async (req: Request, res: Response): Promise<void> => {
  try {
    const page = Math.max(1, parseInt(req.query.page as string) || 1);
    const limit = Math.min(
      100,
      Math.max(1, parseInt(req.query.limit as string) || 20)
    );
    const skip = (page - 1) * limit;

    const [jobs, total] = await Promise.all([
      Job.find({}, { __v: 0 }).skip(skip).limit(limit).sort({ createdAt: -1 }),
      Job.countDocuments(),
    ]);

    res.json({ total, page, limit, jobs });
  } catch (err) {
    res.status(500).json({ error: (err as Error).message });
  }
});

// GET /api/jobs/:job_id — détail d'une offre
router.get("/:job_id", async (req: Request, res: Response): Promise<void> => {
  try {
    const job = await Job.findOne({ job_id: req.params.job_id }, { __v: 0 });
    if (!job) {
      res.status(404).json({ error: "Offre introuvable" });
      return;
    }
    res.json(job);
  } catch (err) {
    res.status(500).json({ error: (err as Error).message });
  }
});

export default router;
