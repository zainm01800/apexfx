# Deploying the APEX Quant Engine (so the live site's Analyse cross-check lights up)

The Vercel site serves the **frontend**. The Python **engine** runs as its own small
web service on an HTTPS host. Once it's live, `apexfx.vercel.app` Analyse fills in the
regime / risk-layer / validation card automatically (HTTPS → HTTPS, no mixed-content).

Everything in this repo is already deploy-ready: `engine/Dockerfile`, `render.yaml`,
`engine/Procfile`, and a bundled validation/research cache. You only need to create a
host account and click deploy.

---

## Option A — Render (recommended: free, Docker, HTTPS, one blueprint)

1. Go to **https://render.com** → sign up (use your GitHub login).
2. **New +** → **Blueprint** → connect the `zainm01800/apexfx` repo.
   Render reads `render.yaml` and provisions a Docker web service from `engine/`.
3. Click **Apply**. First build takes ~3–5 min (installs numpy/scipy/sklearn/
   lightgbm/arch/hmmlearn). When it's done you get a URL like
   `https://apex-quant-engine.onrender.com`.
4. Verify: open `https://<your-engine>.onrender.com/health` → `{"status":"ok",...}`.

> Free plan sleeps after ~15 min idle; the first request then takes ~30–60 s to wake.
> A paid instance ($7/mo) stays warm. Fine either way for personal use.

## Option B — Railway

1. **https://railway.app** → sign up with GitHub → **New Project → Deploy from repo**.
2. Set the service **Root Directory** to `engine` (it auto-detects the Dockerfile).
3. Add a variable `APEX_CORS_ORIGINS = https://apexfx.vercel.app`.
4. Deploy → copy the generated `*.up.railway.app` URL → check `/health`.

## Option C — Fly.io

```bash
cd engine
fly launch --no-deploy        # creates a fly app; keep the Dockerfile
fly secrets set APEX_CORS_ORIGINS=https://apexfx.vercel.app
fly deploy
```

---

## Point the live site at your engine

Once you have the engine URL, just load the site once with it:

```
https://apexfx.vercel.app/dashboard.html?engine=https://<your-engine-url>
```

It's saved to `localStorage`, so every later visit (and the Quant tab) uses it
automatically. **Or tell me the URL and I'll bake it in as the default** for the
`apexfx.vercel.app` domain, so no query param is ever needed.

CORS is already handled — the engine allows any `*.vercel.app` origin.

---

## Optional — turn on the live LLM debate + news sentiment

By default the AI research uses the programmatic proposer and sentiment is off.
To wire them to your existing Groq/Gemini pipeline, set these env vars on the host
(uncomment them in `render.yaml`, or add in the host dashboard):

```
APEX_AI__ENABLED=true
APEX_AI__APP_URL=https://apexfx.vercel.app
APEX_SENTIMENT__ENABLED=true
APEX_SENTIMENT__APP_URL=https://apexfx.vercel.app
```

---

## Refreshing the validation / research cache

`/validation` and `/risk` regime computations are live, but the CPCV/DSR/PBO reports
and AI research are precomputed (too slow per request). A snapshot ships in the image.
To refresh, regenerate locally and push (the host auto-redeploys):

```bash
cd engine
.venv\Scripts\python.exe scripts/run_validation.py EUR/USD GBP/USD
.venv\Scripts\python.exe scripts/run_research.py EUR/USD
git add engine/data_store && git commit -m "refresh engine cache" && git push
```

(Scheduling this automatically — regime-aware retraining + live-vs-backtest decay
monitoring — is the remaining piece of Phase 4.)
