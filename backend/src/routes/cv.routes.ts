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

// ============================================================
// ENVOI VERS L'IA WIPWORK (/parse/resume)
// ============================================================

/**
 * Filtre de sélection des CV à pousser vers l'IA.
 *
 * Base imposée par la campagne commerciale :
 *   { commercial_email_wave: <wave>, commercial_email_unsubscribed: { $exists: false } }
 * + on exclut les CV déjà envoyés (ia_sent).
 *
 * Query params : wave (défaut 2, "all" pour ignorer), country,
 * include_sent=1 (réenvoi), max_attempts=N (ignore les CV ayant déjà
 * échoué N fois).
 */
function buildIaSelectionFilter(query: Request["query"]): Record<string, unknown> {
  const filter: Record<string, unknown> = {
    commercial_email_unsubscribed: { $exists: false },
  };

  const wave = (query.wave as string) ?? "2";
  if (wave !== "all") {
    const parsedWave = parseInt(wave, 10);
    if (Number.isNaN(parsedWave)) {
      throw new Error("wave doit être un entier ou 'all'");
    }
    filter.commercial_email_wave = parsedWave;
  }

  if (query.country) filter.country = query.country;
  if (query.include_sent !== "1") filter.ia_sent = { $ne: true };

  const maxAttempts = parseInt(query.max_attempts as string, 10);
  if (!Number.isNaN(maxAttempts)) {
    filter.$and = [
      {
        $or: [
          { ia_attempts: { $exists: false } },
          { ia_attempts: { $lt: maxAttempts } },
        ],
      },
    ];
  }

  return filter;
}

// GET /api/cvs/ia/pending — CV éligibles et pas encore envoyés à l'IA
router.get("/ia/pending", async (req: Request, res: Response): Promise<void> => {
  try {
    const page = Math.max(1, parseInt(req.query.page as string) || 1);
    const limit = Math.min(
      5000,
      Math.max(1, parseInt(req.query.limit as string) || 200)
    );
    const skip = (page - 1) * limit;

    const filter = buildIaSelectionFilter(req.query);

    const [cvs, total] = await Promise.all([
      Cv.find(filter, { __v: 0, texte: 0 })
        .skip(skip)
        .limit(limit)
        .sort({ _id: 1 }), // tri stable : les CV marqués envoyés quittent le filtre
      Cv.countDocuments(filter),
    ]);

    res.json({ total, page, limit, cvs });
  } catch (err) {
    res.status(400).json({ error: (err as Error).message });
  }
});

// GET /api/cvs/ia/stats — avancement de l'envoi vers l'IA
router.get("/ia/stats", async (req: Request, res: Response): Promise<void> => {
  try {
    const baseFilter = buildIaSelectionFilter({ ...req.query, include_sent: "1" });

    const [eligible, pending, sent, failed] = await Promise.all([
      Cv.countDocuments(baseFilter),
      Cv.countDocuments({ ...baseFilter, ia_sent: { $ne: true } }),
      Cv.countDocuments({ ...baseFilter, ia_sent: true }),
      Cv.countDocuments({
        ...baseFilter,
        ia_sent: { $ne: true },
        ia_last_error: { $exists: true },
      }),
    ]);

    res.json({ eligible, pending, sent, failed });
  } catch (err) {
    res.status(400).json({ error: (err as Error).message });
  }
});

// PATCH /api/cvs/:id/ia — marque le résultat d'un envoi vers l'IA
router.patch("/:id/ia", async (req: Request, res: Response): Promise<void> => {
  try {
    const {
      status,
      point_id,
      user_id,
      email,
      operation_type,
      storage_path,
      country_ids,
      enterprise_ids,
      duplicate_replaced,
      error,
      reason,
    } = req.body as Record<string, unknown>;

    if (status !== "sent" && status !== "failed" && status !== "skipped") {
      res
        .status(400)
        .json({ error: "status doit valoir 'sent', 'failed' ou 'skipped'" });
      return;
    }

    const now = new Date();
    const set: Record<string, unknown> = { ia_last_attempt_at: now };
    const unset: Record<string, string> = {};
    const inc: Record<string, number> = {};

    if (status === "sent") {
      set.ia_sent = true;
      set.ia_sent_at = now;
      if (point_id !== undefined) set.ia_point_id = point_id;
      if (user_id !== undefined) set.ia_user_id = user_id;
      if (email !== undefined) set.ia_email = email;
      if (operation_type !== undefined) set.ia_operation_type = operation_type;
      if (storage_path !== undefined) set.ia_storage_path = storage_path;
      if (Array.isArray(country_ids)) set.ia_country_ids = country_ids;
      if (Array.isArray(enterprise_ids)) set.ia_enterprise_ids = enterprise_ids;
      if (duplicate_replaced !== undefined)
        set.ia_duplicate_replaced = !!duplicate_replaced;
      inc.ia_attempts = 1;
      unset.ia_last_error = "";
      unset.ia_skip_reason = "";
    } else if (status === "failed") {
      set.ia_last_error = String(error ?? "erreur inconnue");
      inc.ia_attempts = 1;
      unset.ia_skip_reason = "";
    } else {
      // skipped : pas un échec d'appel API (fichier introuvable, pays inconnu…)
      // → pas d'incrément de ia_attempts, le CV reste éligible
      set.ia_skip_reason = String(reason ?? "non précisé");
    }

    const update: Record<string, unknown> = { $set: set };
    if (Object.keys(inc).length) update.$inc = inc;
    if (Object.keys(unset).length) update.$unset = unset;

    const cv = await Cv.findOneAndUpdate({ id: req.params.id }, update, {
      new: true,
      projection: {
        id: 1,
        ia_sent: 1,
        ia_sent_at: 1,
        ia_attempts: 1,
        ia_point_id: 1,
        ia_last_error: 1,
        ia_skip_reason: 1,
      },
    });

    if (!cv) {
      res.status(404).json({ error: "CV introuvable" });
      return;
    }

    res.json(cv);
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
