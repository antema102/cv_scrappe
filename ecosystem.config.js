// Configuration PM2 — planificateur quotidien du scraper.
// Démarrage : pm2 start ecosystem.config.js && pm2 save
module.exports = {
  apps: [
    {
      name: "daily-scraper",
      cwd: __dirname,
      // Processus permanent : attend l'heure prévue, lance facebook_script/pages.py --push --headless
      // --country <code> pour chaque pays qui a des liens (facebook_script/pays/<code>/), l'un après
      // l'autre (voir steps()), et décale l'horaire si un cycle dépasse 24 h (voir run_daily.py).
      // Pas de cron_restart : c'est run_daily.py qui planifie.
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
    {
      name: "fb-share-groups",
      cwd: __dirname,
      // Publie les 40 offres WipWork les plus récentes dans les groupes de chaque pays
      // (facebook_script/pays/<code>/share_groups.txt), pays après pays.
      // Lancé une fois au démarrage puis chaque jour à 8 h (cron_restart) ; autorestart false : le script
      // s'arrête après le dernier groupe du dernier pays et attend le prochain cron. Profil de chaque pays :
      // facebook_script/share_countries.json. Un profil partagé avec daily-scraper (my_custom_profile_facebook)
      // ne peut pas servir aux deux en même temps : Chrome refuse un profil déjà ouvert.
      // Démarrage seul : pm2 start ecosystem.config.js --only fb-share-groups && pm2 save
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
