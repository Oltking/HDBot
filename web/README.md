# HD Bot Cockpit — frontend

Bold, playful 3D dashboard for the SLP bot. Pure static site (HTML/CSS/JS +
Three.js from CDN) — no build step.

## Deploy on Vercel

1. Vercel → **Add New → Project** → import the `Oltking/HDBot` repo.
2. **Root Directory:** set to `web`  ← important (the repo root is the Python bot).
3. **Framework Preset:** Other. No build command, output dir = `.` (default).
4. Deploy.

## Connect it to your bot

The dashboard reads live data from the bot's API (the Render web service).
Two ways to point it there:

- Open the deployed site, click the **⚙**, paste your Render URL
  (e.g. `https://slp-trading-bot.onrender.com`), Connect. It's saved in the browser.
- Or share a pre-connected link: `https://your-site.vercel.app/?api=https://slp-trading-bot.onrender.com`

Until connected, it shows a labelled **sample preview** so the layout is alive.

## Local preview

```
cd web && python3 -m http.server 8123   # then open http://localhost:8123
```
