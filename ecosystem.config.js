// Configuration PM2 — planificateur quotidien du scraper.
// Démarrage : pm2 start ecosystem.config.js && pm2 save
module.exports = {
  apps: [
    {
      name: "daily-scraper",
      cwd: __dirname,
      // Processus permanent : attend l'heure prévue, enchaîne index.py puis
      // scrapers.emails_website, et décale l'horaire si un cycle dépasse 24 h
      // (voir run_daily.py). Pas de cron_restart : c'est run_daily.py qui planifie.
      script: "python",
      args: "run_daily.py",
      interpreter: "none",
      autorestart: true,
      watch: false,
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHONIOENCODING: "utf-8",
      },
      out_file: "logs/daily-scraper.out.log",
      error_file: "logs/daily-scraper.err.log",
      time: true,
    },
  ],
};
