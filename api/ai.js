// /api/ai — Multi-provider AI proxy (Gemini primary, Groq fallback)
// Frontend calls: POST /api/ai  { prompt, system?, model?, max_tokens?, temperature?, timeoutMs? }
//
// Provider selection:
//   - If GEMINI_API_KEY is set → use Google Gemini (4M TPM free tier, no rate issues)
//   - Otherwise → fall back to Groq (6K TPM free tier, hits limits fast)
// Add GEMINI_API_KEY in Vercel dashboard → Settings → Environment Variables
// Get a free key at: https://aistudio.google.com

export const config = { runtime: 'edge' };

const GEMINI_KEY = process.env.GEMINI_API_KEY;
const GROQ_KEY   = process.env.GROQ_API_KEY;

// Gemini model to use (OpenAI-compatible endpoint)
// gemini-2.0-flash: fast, smart, 1M context, 4M TPM free
const GEMINI_MODEL = 'gemini-2.0-flash';
const GEMINI_URL   = 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions';

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

export default async function handler(req) {
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

  // ── Try Gemini first (high limits, free) ─────────────────────────────────
  if (GEMINI_KEY) {
    try {
      const text = await callProvider({
        apiUrl:     GEMINI_URL,
        apiKey:     GEMINI_KEY,
        model:      GEMINI_MODEL,
        messages,
        maxTokens:  safeMaxTokens,
        temperature: safeTemp,
        timeoutMs:  safeTimeoutMs,
      });
      return new Response(JSON.stringify({ text, provider: 'gemini' }), { status: 200, headers: corsHeaders });
    } catch (e) {
      // Gemini quota or error — fall through to Groq if available
      if (!GROQ_KEY) {
        const retryAfterMs = e.retryAfterMs || null;
        return new Response(
          JSON.stringify({ error: e.message, retryAfterMs }),
          { status: e.status || 500, headers: corsHeaders }
        );
      }
      // else: fall through to Groq
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
        ? `AI rate limit reached. Resets in ~${mins} minute${mins !== 1 ? 's' : ''}. Add a free GEMINI_API_KEY in Vercel to avoid this.`
        : 'AI rate limit reached. Add a free GEMINI_API_KEY in Vercel environment variables to avoid limits.';
    }
    return new Response(
      JSON.stringify({ error: msg, retryAfterMs }),
      { status: e.status || 500, headers: corsHeaders }
    );
  }
}
