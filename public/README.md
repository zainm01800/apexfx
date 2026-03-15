# APEX FX — Deployment Guide

## Step 1 — Deploy to Vercel

1. Go to **vercel.com** → sign up free (use GitHub login)
2. Push this folder to a GitHub repo (or drag-drop onto Vercel dashboard)
3. Click **Deploy**

## Step 2 — Add API keys as Environment Variables

Keys are never in the code — they live securely in Vercel's environment.

Vercel dashboard → your project → **Settings** → **Environment Variables**:

| Name               | Value       | Where to get it                        |
|--------------------|-------------|----------------------------------------|
| `GROQ_API_KEY`     | `gsk_...`   | console.groq.com → API Keys (free)     |
| `FINNHUB_API_KEY`  | `d6pi06...` | finnhub.io → Dashboard (free)          |

After adding → **Deployments** → **Redeploy** (required once).

## Step 3 — (Optional) Password protect while building

Vercel → Settings → Deployment Protection → enable Password Protection

---

## Architecture

```
Browser
  ├── /api/candles?sym=AAPL  →  Vercel edge fn  →  Yahoo Finance (server-side)
  ├── /api/ai  { prompt }    →  Vercel edge fn  →  Groq API (key never in browser)
  ├── /api/ws-token          →  Vercel edge fn  →  returns Finnhub token safely
  └── Crypto                 →  Binance directly (allows browser requests)
```

## What's secured
- ✅ Groq API key — server-side only
- ✅ Finnhub API key — server-side only  
- ✅ Passwords — SHA-256 hashed before storage
