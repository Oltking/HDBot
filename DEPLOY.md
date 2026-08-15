# Deploying the SLP bot to Render

The bot runs as a **Background Worker** (always-on, no web port) with a
**persistent disk** for the trade journal + open-position state.

## 1. Put the code in a GitHub repo

Render deploys from Git. From the project folder:

```bash
git init
git add .
git commit -m "SLP trading bot"
# create an EMPTY repo on github.com first, then:
git remote add origin https://github.com/<you>/trading-bot.git
git branch -M main
git push -u origin main
```

`.env` is git-ignored, so **your token is NOT pushed** — good. Secrets go into
Render directly (step 3).

## 2. Create the service from the blueprint

- Render dashboard → **New** → **Blueprint**
- Connect your GitHub repo → Render reads `render.yaml` and proposes the
  `slp-trading-bot` worker with a 1 GB disk.
- Click **Apply**.

## 3. Set the two secrets

When prompted (they're marked `sync: false`), enter:

| Key | Value |
|-----|-------|
| `DERIV_API_TOKEN` | your `pat_…` token |
| `DERIV_APP_ID`    | your registered app id |

(You can also set/rotate these later under the service's **Environment** tab.)

## 4. Deploy & watch

- The worker builds (`pip install -r requirements.txt`) and starts
  `python -m slp.live --live-demo`.
- Open the **Logs** tab — you should see:
  ```
  Trading account: DOT… (demo) balance=$…
  Seeded cryBTCUSD / R_75 …
  Polling every 30s …
  ```
- Trades stream to the logs AND to `/data/logs/trades.csv` on the disk.

## Notes

- **Paid plan required**: always-on workers + disks aren't on the free tier.
  The free tier sleeps, and a sleeping bot misses trades.
- **Pull the trade log**: use Render's Shell tab and `cat /data/logs/trades.csv`,
  or add a tiny exporter later. This CSV is what we compare to the backtest.
- **`autoDeploy: false`**: pushes to GitHub won't auto-restart the bot mid-trade.
  Deploy manually from the dashboard when you want to ship changes.
- **Stay on DEMO** until the forward-test matches the backtest.
