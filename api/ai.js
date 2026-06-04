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
// Tries each in order; on 429/503 waits briefly then tries the next model before falling back to Groq
const GEMINI_MODELS = [
  'gemini-3-flash-preview',
  'gemini-3.1-flash-lite',
  'gemini-flash-lite-latest',
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

  if (!GEMINI_KEY && !GROQ_KEY) {
    return new Response(JSON.stringify({ error: 'AI service not configured. Add GEMINI_API_KEY or GROQ_API_KEY in Vercel environment variables.' }), { status: 503, headers: corsHeaders });
  }

  let body;
  try { body = await req.json(); }
  catch { return new Response(JSON.stringify({ error: 'Invalid JSON body' }), { status: 400, headers: corsHeaders }); }

  const { prompt, system, max_tokens = 2000, temperature = 0.35, timeoutMs = 55000 } = body;
  const safeMaxTokens  = Math.max(1, Math.min(8000, Number(max_tokens)  || 2000));
  const safeTemp       = Math.max(0, Math.min(1,    Number(temperature) || 0.35));
  const safeTimeoutMs  = Math.max(5000, Math.min(60000, Number(timeoutMs) || 55000));

  if (!prompt || typeof prompt !== 'string' || prompt.length > 200000) {
    return new Response(JSON.stringify({ error: 'Invalid prompt' }), { status: 400, headers: corsHeaders });
  }

  const messages = [];
  if (system && typeof system === 'string' && system.length <= 20000) {
    messages.push({ role: 'system', content: system });
  }
  messages.push({ role: 'user', content: prompt });

  // ── Try Gemini (4-model fallback chain with retry) ───────────────────────
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  let geminiError = null;

  if (GEMINI_KEY) {
    for (const model of GEMINI_MODELS) {
      // Each model gets 2 attempts (catches transient burst-limit 429s)
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          const text = await callProvider({
            apiUrl:      GEMINI_URL,
            apiKey:      GEMINI_KEY,
            model,
            messages,
            maxTokens:   safeMaxTokens,
            temperature: safeTemp,
            timeoutMs:   safeTimeoutMs,
          });
          return new Response(JSON.stringify({ text, provider: 'gemini', model }), { status: 200, headers: corsHeaders });
        } catch (e) {
          const isRateLimit = e.status === 429 || e.status === 503;
          if (!isRateLimit) {
            // Hard error (auth, bad request etc.) — skip straight to Groq
            geminiError = `${model}: ${e.message}`;
            if (!GROQ_KEY) {
              return new Response(
                JSON.stringify({ error: `Gemini error: ${geminiError}` }),
                { status: e.status || 500, headers: corsHeaders }
              );
            }
            break; // break inner loop → skip remaining attempts for this model
          }
          geminiError = `${model}: HTTP ${e.status}`;
          if (attempt === 0) {
            // Wait 1.5 s then retry the same model once before trying next
            await sleep(1500);
          }
          // attempt === 1 → fall through to next model in outer loop
        }
      }
    }

    // All Gemini models exhausted
    if (!GROQ_KEY) {
      return new Response(
        JSON.stringify({ error: `Gemini unavailable: ${geminiError}` }),
        { status: 503, headers: corsHeaders }
      );
    }
  }

  // ── Groq fallback ─────────────────────────────────────────────────────────
  try {
    const text = await callProvider({
      apiUrl:     GROQ_URL,
      apiKey:     GROQ_KEY,
      model:      GROQ_MODEL,
      messages,
      maxTokens:  safeMaxTokens,
      temperature: safeTemp,
      timeoutMs:  safeTimeoutMs,
    });
    return new Response(JSON.stringify({ text, provider: 'groq' }), { status: 200, headers: corsHeaders });
  } catch (e) {
    const retryAfterMs = e.retryAfterMs || null;
    let msg = e.message;
    if (e.status === 429) {
      const mins = retryAfterMs ? Math.ceil(retryAfterMs / 60000) : null;
      msg = mins
        ? `Groq rate limit. Resets in ~${mins} min.${geminiError ? ` Gemini also failed: ${geminiError}` : ''}`
        : `Groq rate limit reached.${geminiError ? ` Gemini also failed: ${geminiError}` : ''}`;
    } else if (geminiError) {
      msg = `Groq error: ${msg} | Gemini error: ${geminiError}`;
    }
    return new Response(
      JSON.stringify({ error: msg, retryAfterMs }),
      { status: e.status || 500, headers: corsHeaders }
    );
  }
}

// Web-standard `fetch` export — runs `handler` on the Node.js runtime.
export default { fetch: handler };
