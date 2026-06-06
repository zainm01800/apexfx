// /api/discord — Discord Interactions endpoint for the /analyse slash command.
//
// The distribution wedge: drop the APEX bot into a trading Discord, and members can
// type `/analyse BTC` to get APEX's most recent PUBLISHED verdict (BUY/SELL/WAIT +
// confidence + entry/stop/target) right in the channel, with links to the full
// analysis and the public track record. It reads the latest cached verdict from
// Supabase — it does NOT run a fresh committee (fast < 3s, no LLM cost, abuse-safe).
//
// Runs on the Node runtime so Web-Crypto Ed25519 signature verification is available.
// One-time setup: see DISCORD_BOT_SETUP.md (create a Discord app, set this as the
// Interactions Endpoint URL, add DISCORD_PUBLIC_KEY to Vercel env, register the cmd).

export const maxDuration = 10;

const SUPA_URL  = 'https://dtiuwllodzqpbwohzrgj.supabase.co';
const SUPA_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImR0aXV3bGxvZHpxcGJ3b2h6cmdqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1MDAwODYsImV4cCI6MjA5NjA3NjA4Nn0.fxOdfqskMpwVYIP2aL1LbeSgOMFfv3223IjzM6ldi5k';
const SITE = 'https://apexfx.vercel.app';
const PUBLIC_KEY = process.env.DISCORD_PUBLIC_KEY || '';

const VERDICT_COLOR = { buy: 0x4ade80, sell: 0xf87171, wait: 0x9aa0b4 };

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: { 'Content-Type': 'application/json' } });
}
function hexToBytes(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
  return out;
}

// Discord signs every request with Ed25519; verifying it is mandatory.
async function verifySignature(rawBody, sig, ts) {
  if (!PUBLIC_KEY || !sig || !ts) return false;
  try {
    const key = await crypto.subtle.importKey('raw', hexToBytes(PUBLIC_KEY), { name: 'Ed25519' }, false, ['verify']);
    return await crypto.subtle.verify('Ed25519', key, hexToBytes(sig), new TextEncoder().encode(ts + rawBody));
  } catch { return false; }
}

// Latest published verdict for a symbol (prefix, case-insensitive: "BTC" -> BTC/USD).
async function latestVerdict(sym) {
  const enc = encodeURIComponent(sym.toUpperCase());
  const url = `${SUPA_URL}/rest/v1/apex_research_memory?symbol=ilike.${enc}*&order=created_at.desc&limit=1`;
  const r = await fetch(url, {
    headers: { apikey: SUPA_ANON, Authorization: `Bearer ${SUPA_ANON}` },
    signal: AbortSignal.timeout(2500),
  });
  if (!r.ok) return null;
  const rows = await r.json();
  return Array.isArray(rows) && rows.length ? rows[0] : null;
}

function buildEmbed(sym, row) {
  if (!row) {
    return {
      title: `No published call for ${sym.toUpperCase()} yet`,
      description: `APEX hasn't analysed **${sym.toUpperCase()}** recently. Run a fresh analysis at ${SITE}`,
      color: VERDICT_COLOR.wait,
      footer: { text: 'Information & education only — not financial advice.' },
    };
  }
  const v = (row.verdict || 'WAIT').toUpperCase();
  const dir = /BUY|LONG/.test(v) ? 'buy' : /SELL|SHORT/.test(v) ? 'sell' : 'wait';
  const icon = dir === 'buy' ? '▲' : dir === 'sell' ? '▼' : '⏸';
  const fields = [];
  if (row.confidence != null) fields.push({ name: 'Confidence', value: `${row.confidence}%`, inline: true });
  if (row.entry_zone)   fields.push({ name: 'Entry',  value: String(row.entry_zone).slice(0, 64),  inline: true });
  if (row.stop_loss)    fields.push({ name: 'Stop',   value: String(row.stop_loss).slice(0, 64),   inline: true });
  if (row.target_price) fields.push({ name: 'Target', value: String(row.target_price).slice(0, 64), inline: true });
  if (row.risk_reward)  fields.push({ name: 'R:R',    value: String(row.risk_reward).slice(0, 32),  inline: true });
  const outcome = row.outcome && row.outcome !== 'pending'
    ? (row.outcome === 'tp_hit' ? '✓ TP hit' : row.outcome === 'sl_hit' ? '✗ SL hit' : row.outcome)
    : 'open';
  return {
    title: `${icon} ${row.symbol} — ${v.replace(/_/g, ' ')}`,
    url: `${SITE}/dashboard.html`,
    description: ((row.summary || '').slice(0, 280)) || 'APEX committee verdict.',
    color: VERDICT_COLOR[dir],
    fields,
    footer: { text: `As of ${String(row.analysis_date || row.created_at || '').slice(0, 10)} · outcome: ${outcome} · info & education only, not advice` },
  };
}

async function handler(req) {
  if (req.method !== 'POST') return new Response('APEX Discord interactions endpoint', { status: 200 });

  const raw = await req.text();
  const valid = await verifySignature(
    raw,
    req.headers.get('x-signature-ed25519'),
    req.headers.get('x-signature-timestamp'),
  );
  if (!valid) return new Response('invalid request signature', { status: 401 });

  let body;
  try { body = JSON.parse(raw); } catch { return new Response('bad json', { status: 400 }); }

  // PING (Discord endpoint verification + keepalive)
  if (body.type === 1) return json({ type: 1 });

  // APPLICATION_COMMAND
  if (body.type === 2 && body.data && body.data.name === 'analyse') {
    const opt = (body.data.options || []).find((o) => o.name === 'ticker');
    const sym = (opt && opt.value ? String(opt.value) : '').trim();
    if (!sym) return json({ type: 4, data: { content: 'Usage: `/analyse <ticker>` — e.g. `/analyse BTC`', flags: 64 } });

    let row = null;
    try { row = await latestVerdict(sym); } catch { /* graceful */ }
    return json({
      type: 4,
      data: {
        embeds: [buildEmbed(sym, row)],
        components: [{
          type: 1,
          components: [
            { type: 2, style: 5, label: 'Full analysis', url: `${SITE}/dashboard.html` },
            { type: 2, style: 5, label: 'Track record', url: `${SITE}/track-record.html` },
          ],
        }],
      },
    });
  }

  return json({ type: 4, data: { content: 'Unknown command.', flags: 64 } });
}

export default { fetch: handler };
