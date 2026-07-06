// /api/ai — Multi-provider AI proxy (Gemini primary, Groq fallback)
// Frontend calls: POST /api/ai  { prompt, system?, model?, max_tokens?, temperature?, timeoutMs? }
//
// Provider selection:
//   - If GEMINI_API_KEY is set → use Google Gemini (4M TPM free tier, no rate issues)
//   - Otherwise → fall back to Groq (6K TPM free tier, hits limits fast)
// Add GEMINI_API_KEY in Vercel dashboard → Settings → Environment Variables
// Get a free key at: https://aistudio.google.com

// Node.js runtime (default — no `runtime: 'edge'`) with a raised maxDuration.
// The Edge runtime caps wall-clock around ~25s, which was 504-ing the heaviest
// call (the 6000-token committee synthesis). Node + maxDuration gives it room to
// finish. The handler still uses the Web Request/Response API (fully supported on
// the Node runtime via the `fetch` export below), so no other logic changes.
export const maxDuration = 60;

const GEMINI_KEY = process.env.GEMINI_API_KEY;
const GROQ_KEY   = process.env.GROQ_API_KEY;

// Gemini model fallback chain (OpenAI-compatible endpoint)
// Tries each in order; on 429/503 waits briefly then tries the next model before falling back to Groq.
// flash-lite (a NON-thinking model) is first on purpose: the "thinking" preview model spends part of
// its token budget on hidden reasoning, which truncated the large committee JSON → parse failures.
// flash-lite returns the full strict JSON reliably; the preview model stays as a later fallback.
const GEMINI_MODELS = [
  'gemini-3.1-flash-lite',
  'gemini-flash-lite-latest',
  'gemini-3-flash-preview',
  'gemini-flash-latest',
];
const GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions';

// Groq fallback model
const GROQ_MODEL = 'llama-3.3-70b-versatile';
const GROQ_URL   = 'https://api.groq.com/openai/v1/chat/completions';

function isAllowedOrigin(origin, host) {
  if (!origin) return true;
  try {
    const url = new URL(origin);
    if (url.host === host) return true;
    if (url.hostname === 'localhost' || url.hostname === '127.0.0.1') return true;
  } catch {}
  return false;
}

async function callProvider({ apiUrl, apiKey, model, messages, maxTokens, temperature, timeoutMs }) {
  const res = await fetch(apiUrl, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model,
      max_tokens: maxTokens,
      temperature,
      messages,
    }),
    signal: AbortSignal.timeout(timeoutMs),
  });

  const data = await res.json();

  if (!res.ok) {
    const msg = data?.error?.message || data?.error?.code || `HTTP ${res.status}`;
    // Attach rate-limit headers if present (Groq specific)
    const rl = res.headers.get('x-ratelimit-reset-requests');
    const ra = res.headers.get('retry-after');
    const retryAfterMs = rl
      ? (/^\d+(\.\d+)?$/.test(rl) ? Math.ceil(Number(rl) * 1000) : null)
      : (ra ? (/^\d+(\.\d+)?$/.test(ra) ? Math.ceil(Number(ra) * 1000) : null) : null);
    throw Object.assign(new Error(msg), { status: res.status, retryAfterMs });
  }

  return data.choices?.[0]?.message?.content || '';
}

async function handler(req) {
  const url    = new URL(req.url);
  const origin = req.headers.get('origin');
  const allowedOrigin = isAllowedOrigin(origin, url.host) ? (origin || url.origin) : url.origin;

  const corsHeaders = {
    'Access-Control-Allow-Origin':  allowedOrigin,
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Content-Type': 'application/json',
  };

  if (origin && !isAllowedOrigin(origin, url.host)) {
    return new Response(JSON.stringify({ error: 'Origin not allowed' }), { status: 403, headers: corsHeaders });
  }
  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: corsHeaders });
  if (req.method !== 'POST')   return new Response(JSON.stringify({ error: 'Method not allowed' }), { status: 405, headers: corsHeaders });

  let body;
  try { body = await req.json(); }
  catch { return new Response(JSON.stringify({ error: 'Invalid JSON body' }), { status: 400, headers: corsHeaders }); }

  const useLocal = body && (body.useLocalLlm || process.env.APEX_LOCAL_LLM_ENABLED === 'true');
  if (!useLocal && !GEMINI_KEY && !GROQ_KEY) {
    return new Response(JSON.stringify({ error: 'AI service not configured. Add GEMINI_API_KEY or GROQ_API_KEY in Vercel environment variables.' }), { status: 503, headers: corsHeaders });
  }

  const { prompt, system, max_tokens = 2000, temperature = 0.35, timeoutMs = 55000 } = body;
  const safeMaxTokens  = Math.max(1, Math.min(8000, Number(max_tokens)  || 2000));
  const safeTemp       = Math.max(0, Math.min(1,    Number(temperature) || 0.35));
  const safeTimeoutMs  = Math.max(5000, Math.min(60000, Number(timeoutMs) || 55000));

  // Optional explicit provider/model (used by the committee ensemble for model
  // diversity). Honoured first, then we fall back to the normal chain so a scan
  // never breaks if the requested provider is down.
  const reqProvider = typeof body.provider === 'string' ? body.provider.toLowerCase() : null;
  const reqModel    = typeof body.model === 'string' && body.model.length <= 100 ? body.model : null;

  if (!prompt || typeof prompt !== 'string' || prompt.length > 200000) {
    return new Response(JSON.stringify({ error: 'Invalid prompt' }), { status: 400, headers: corsHeaders });
  }

  const messages = [];
  if (system && typeof system === 'string' && system.length <= 20000) {
    messages.push({ role: 'system', content: system });
  }
  messages.push({ role: 'user', content: prompt });

  // ── Local LLM Routing (Ollama / LM Studio / DeepSeek) ─────────────────────
  if (useLocal) {
    const localUrl = body.localLlmUrl || process.env.APEX_LOCAL_LLM_URL || 'http://localhost:11434/v1/chat/completions';
    const localModel = body.localLlmModel || process.env.APEX_LOCAL_LLM_MODEL || 'llama3';
    const localKey = body.localLlmKey || process.env.APEX_LOCAL_LLM_KEY || 'none';
    try {
      const text = await callProvider({
        apiUrl: localUrl,
        apiKey: localKey,
        model: localModel,
        messages,
        maxTokens: safeMaxTokens,
        temperature: safeTemp,
        timeoutMs: safeTimeoutMs
      });
      return new Response(JSON.stringify({ text, provider: 'local', model: localModel }), { status: 200, headers: corsHeaders });
    } catch (e) {
      return new Response(JSON.stringify({ error: `Local LLM failed: ${e.message}` }), { status: 502, headers: corsHeaders });
    }
  }

  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // ── Gemini (model fallback chain with retry). If a specific Gemini model was
  // requested, try it first, then the rest of the chain. ────────────────────────
  async function runGemini() {
    if (!GEMINI_KEY) throw Object.assign(new Error('Gemini not configured'), { status: 503 });
    const wanted = (reqProvider === 'gemini' && reqModel) ? reqModel : null;
    const models = wanted ? [wanted, ...GEMINI_MODELS.filter(m => m !== wanted)] : GEMINI_MODELS;
    let err = null;
    for (const model of models) {
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          const text = await callProvider({ apiUrl: GEMINI_URL, apiKey: GEMINI_KEY, model, messages, maxTokens: safeMaxTokens, temperature: safeTemp, timeoutMs: safeTimeoutMs });
          return { text, provider: 'gemini', model };
        } catch (e) {
          const isRateLimit = e.status === 429 || e.status === 503;
          err = isRateLimit ? `${model}: HTTP ${e.status}` : `${model}: ${e.message}`;
          if (!isRateLimit) break;            // hard error → next model
          if (attempt === 0) await sleep(1500);
        }
      }
    }
    throw Object.assign(new Error(`Gemini unavailable: ${err}`), { status: 503 });
  }

  // ── Groq (honours a requested model, else the default Llama). ─────────────────
  async function runGroq() {
    if (!GROQ_KEY) throw Object.assign(new Error('Groq not configured'), { status: 503 });
    const model = (reqProvider === 'groq' && reqModel) ? reqModel : GROQ_MODEL;
    const text = await callProvider({ apiUrl: GROQ_URL, apiKey: GROQ_KEY, model, messages, maxTokens: safeMaxTokens, temperature: safeTemp, timeoutMs: safeTimeoutMs });
    return { text, provider: 'groq', model };
  }

  // Provider order: honour the explicit request first, then fall back to the other
  // so the scan never breaks if the requested provider is unavailable.
  const order = reqProvider === 'groq' ? [runGroq, runGemini] : [runGemini, runGroq];

  let lastErr = null;
  for (const run of order) {
    try {
      const out = await run();
      return new Response(JSON.stringify(out), { status: 200, headers: corsHeaders });
    } catch (e) { lastErr = e; }
  }

  const retryAfterMs = lastErr?.retryAfterMs || null;
  let msg = lastErr?.message || 'AI providers unavailable';
  if (lastErr?.status === 429) {
    const mins = retryAfterMs ? Math.ceil(retryAfterMs / 60000) : null;
    msg = mins ? `Rate limited. Resets in ~${mins} min.` : 'Rate limit reached on all providers.';
  }
  return new Response(JSON.stringify({ error: msg, retryAfterMs }), { status: lastErr?.status || 500, headers: corsHeaders });
}

// Web-standard `fetch` export — runs `handler` on the Node.js runtime.
export default { fetch: handler };
