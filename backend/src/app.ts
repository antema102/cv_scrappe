import "dotenv/config";
import express from "express";
import cors from "cors";
import connectDB from "./config/db";
import jobsRouter from "./routes/jobs.routes";
import companiesRouter from "./routes/companies.routes";

const app = express();
const PORT = process.env.PORT ?? 3000;

connectDB();

app.use(cors());
app.use(express.json({ limit: "10mb" }));

app.use("/api/jobs", jobsRouter);
app.use("/api/companies", companiesRouter);

app.get("/health", (_req, res) => res.json({ status: "ok" }));

app.listen(PORT, () => {
  console.log(`Serveur démarré sur http://localhost:${PORT}`);
});
