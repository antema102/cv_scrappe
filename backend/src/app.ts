import "dotenv/config";
import express from "express";
import cors from "cors";
import connectDB from "./config/db";
import jobsRouter from "./routes/jobs.routes";
import companiesRouter from "./routes/companies.routes";
import cvsRouter from "./routes/cv.routes";

const app = express();
const PORT = process.env.PORT ?? 3000;

connectDB();

app.use(cors());
app.use(express.json({ limit: "10mb" }));

// ================================
// LOGS DES REQUÊTES HTTP
// ================================
app.use((req, res, next) => {
  const start = Date.now();

  console.log(
    `➡️  ${req.method} ${req.originalUrl}`
  );

  res.on("finish", () => {
    const duration = Date.now() - start;

    console.log(
      `⬅️  ${req.method} ${req.originalUrl} → ${res.statusCode} (${duration}ms)`
    );
  });

  next();
});

// ================================
// ROUTES
// ================================
app.use("/api/jobs", jobsRouter);
app.use("/api/companies", companiesRouter);
app.use("/api/cvs", cvsRouter);

// ================================
// HEALTH CHECK
// ================================
app.get("/health", (_req, res) => {
  res.json({ status: "ok" });
});

// ================================
// START SERVER
// ================================
app.listen(PORT, () => {
  console.log(
    `🚀 Serveur démarré sur http://localhost:${PORT}`
  );
});