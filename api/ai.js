// /api/ai — Vercel serverless function
// Proxies Groq API calls so the key never appears in browser source code.
// Frontend calls: POST /api/ai  { prompt, model?, max_tokens?, temperature? }

export const config = { runtime: 'edge' };

const GROQ_KEY = process.env.GROQ_API_KEY; // Set in Vercel dashboard → Environment Variables

export default async function handler(req) {
  const corsHeaders = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Content-Type': 'application/json',
  };

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

  const { prompt, model = 'llama-3.1-8b-instant', max_tokens = 1000, temperature = 0 } = body;

  if (!prompt || typeof prompt !== 'string' || prompt.length > 8000) {
    return new Response(JSON.stringify({ error: 'Invalid prompt' }), { status: 400, headers: corsHeaders });
  }

  try {
    const res = await fetch('https://api.groq.com/openai/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${GROQ_KEY}`,
      },
      body: JSON.stringify({
        model,
        max_tokens,
        temperature,
        messages: [{ role: 'user', content: prompt }],
      }),
      signal: AbortSignal.timeout(30000),
    });

    const data = await res.json();

    if (!res.ok) {
      const msg = data?.error?.message || data?.error?.code || `HTTP ${res.status}`;
      return new Response(JSON.stringify({ error: msg }), { status: res.status, headers: corsHeaders });
    }

    const text = data.choices?.[0]?.message?.content || '';
    return new Response(JSON.stringify({ text }), { status: 200, headers: corsHeaders });

  } catch (e) {
    return new Response(JSON.stringify({ error: e.message }), { status: 500, headers: corsHeaders });
  }
}
