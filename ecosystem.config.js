// Configuration PM2 — planificateur quotidien du scraper.
// Démarrage : pm2 start ecosystem.config.js && pm2 save
module.exports = {
  apps: [
    {
      name: "daily-scraper",
      cwd: __dirname,
      script: "python",
      // chaque jour à 18 h heure de Madagascar (--tz) = 19 h heure du serveur (UTC+4), même après un redémarrage
      // (demande de l'utilisateur du 26/11/2026 ; avant : 19 h à Madagascar = 20 h serveur). Pas de variable TZ : sous
      // Windows, Python ne comprend pas « Indian/Antananarivo » et se met en UTC (19 h devenait 22 h à Madagascar).
      // --max-parallel 1 depuis le 27/11/2026 : le 26/11, deux Chrome de groupes Facebook à 5-7 Go chacun (8 Go de
      // RAM) ont épuisé la mémoire virtuelle (MemoryError du Cameroun) ; remettre 2 avec plus de RAM.
      args: "run_daily.py --at 18:00 --tz Indian/Antananarivo --max-parallel 1",
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
