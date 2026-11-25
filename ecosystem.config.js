// Configuration PM2 — planificateur quotidien du scraper.
// Démarrage : pm2 start ecosystem.config.js && pm2 save
module.exports = {
  apps: [
    {
      name: "daily-scraper",
      cwd: __dirname,
      script: "python",
      args: "run_daily.py --at 19:00",  // chaque jour à 19 h (heure de Madagascar, TZ ci-dessous), même après un redémarrage
      interpreter: "none",
      autorestart: true,
      watch: false,
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHONIOENCODING: "utf-8",
        TZ: "Indian/Antananarivo",
      },
      out_file: "logs/daily-scraper.out.log",
      error_file: "logs/daily-scraper.err.log",
      time: true,
    },
    {
      name: "fb-share-groups",
      cwd: __dirname,
      script: "python",
      args: "facebook_script/post_groups.py --all-countries",  // automatique et sans fenêtre par défaut
      interpreter: "none",
      autorestart: false,
      cron_restart: "0 8 * * *",
      watch: false,
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHONIOENCODING: "utf-8",
      },
      out_file: "logs/fb-share-groups.out.log",
      error_file: "logs/fb-share-groups.err.log",
      time: true,
    },
  ],
};
