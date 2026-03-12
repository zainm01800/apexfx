# APEX FX — Deployment Guide

## Deploy to Vercel (free, ~2 minutes)

### Option A — Drag & drop (no account setup needed)
1. Go to **vercel.com** and sign up (free)
2. Click **"Add New Project"**
3. Drag the entire `apexfx-site` folder onto the Vercel dashboard
4. Click **Deploy** — done. You get a live URL like `apexfx.vercel.app`

### Option B — GitHub (best for updates)
1. Push this folder to a GitHub repo
2. Go to **vercel.com** → New Project → Import from GitHub
3. Select the repo, click Deploy
4. Every `git push` auto-redeploys

---

## How it works

```
Browser → /api/candles?sym=AAPL&tf=1d  →  Vercel Edge Function
                                        →  Yahoo Finance (server-side, no CORS)
                                        ←  Clean OHLCV JSON
```

The `api/candles.js` serverless function runs on Vercel's edge network,
fetches Yahoo Finance from server-side (no CORS restrictions), and returns
clean bar data to the browser. Crypto data fetches Binance directly from
the browser (Binance allows cross-origin requests).

## Vercel free tier limits
- 100 GB bandwidth / month
- 1,000,000 edge function invocations / month
- Unlimited deployments

This is more than enough for a trading platform with thousands of daily users.

## Custom domain
In Vercel dashboard → Project Settings → Domains → Add your domain.
