// /api/ai — Vercel serverless function
// Proxies Groq API calls so the key never appears in browser source code.
// Frontend calls: POST /api/ai  { prompt, model?, max_tokens?, temperature? }

export const config = { runtime: 'edge' };

const GROQ_KEY = process.env.GROQ_API_KEY; // Set in Vercel dashboard → Environment Variables

function isAllowedOrigin(origin, host) {
  if (!origin) return true;
  try {
    const url = new URL(origin);
    if (url.host === host) return true;
    if (url.hostname === 'localhost' || url.hostname === '127.0.0.1') return true;
  } catch {}
  return false;
}

export default async function handler(req) {
  const url = new URL(req.url);
  const origin = req.headers.get('origin');
  const allowedOrigin = isAllowedOrigin(origin, url.host) ? (origin || url.origin) : url.origin;
  const corsHeaders = {
    'Access-Control-Allow-Origin': allowedOrigin,
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Expose-Headers': 'x-ratelimit-reset-requests, retry-after',
    'Content-Type': 'application/json',
  };

  if (origin && !isAllowedOrigin(origin, url.host)) {
    return new Response(JSON.stringify({ error: 'Origin not allowed' }), { status: 403, headers: corsHeaders });
  }

  // Handle preflight
  if (req.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: corsHeaders });
  }

  if (req.method !== 'POST') {
    return new Response(JSON.stringify({ error: 'Method not allowed' }), { status: 405, headers: corsHeaders });
  }

  if (!GROQ_KEY) {
    return new Response(JSON.stringify({ error: 'AI service not configured' }), { status: 503, headers: corsHeaders });
  }

  let body;
  try {
    body = await req.json();
  } catch {
    return new Response(JSON.stringify({ error: 'Invalid JSON body' }), { status: 400, headers: corsHeaders });
  }

  const { prompt, system, model = 'llama-3.1-8b-instant', max_tokens = 1000, temperature = 0, timeoutMs = 30000 } = body;
  const safeMaxTokens = Math.max(1, Math.min(8000, Number(max_tokens) || 1000));
  const safeTemperature = Math.max(0, Math.min(1, Number(temperature) || 0));
  const safeTimeoutMs = Math.max(5000, Math.min(60000, Number(timeoutMs) || 30000));

  if (!prompt || typeof prompt !== 'string' || prompt.length > 120000) {
    return new Response(JSON.stringify({ error: 'Invalid prompt' }), { status: 400, headers: corsHeaders });
  }

  const messages = [];
  if (system && typeof system === 'string' && system.length <= 20000) {
    messages.push({ role: 'system', content: system });
  }
  messages.push({ role: 'user', content: prompt });

  try {
    const res = await fetch('https://api.groq.com/openai/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${GROQ_KEY}`,
      },
      body: JSON.stringify({
        model,
        max_tokens: safeMaxTokens,
        temperature: safeTemperature,
        messages,
      }),
      signal: AbortSignal.timeout(safeTimeoutMs),
    });

    const data = await res.json();

    if (!res.ok) {
      const msg = data?.error?.message || data?.error?.code || `HTTP ${res.status}`;
      const errHeaders = { ...corsHeaders };
      const rl = res.headers.get('x-ratelimit-reset-requests');
      if (rl) errHeaders['x-ratelimit-reset-requests'] = rl;
      const ra = res.headers.get('retry-after');
      if (ra) errHeaders['retry-after'] = ra;
      const retryAfterMs = rl
        ? (/^\d+(\.\d+)?$/.test(rl) ? Math.ceil(Number(rl) * 1000) : null)
        : (ra ? (/^\d+(\.\d+)?$/.test(ra) ? Math.ceil(Number(ra) * 1000) : null) : null);
      return new Response(JSON.stringify({ error: msg, retryAfterMs }), { status: res.status, headers: errHeaders });
    }

    const text = data.choices?.[0]?.message?.content || '';
    return new Response(JSON.stringify({ text }), { status: 200, headers: corsHeaders });

  } catch (e) {
    return new Response(JSON.stringify({ error: e.message }), { status: 500, headers: corsHeaders });
  }
}
