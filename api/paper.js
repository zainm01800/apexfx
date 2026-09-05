// Read-only paper data. No broker calls, execution, seeding or persistence.
// GET ?book=a|b|c|r|s|f|v6|v10&table=state|daily|positions|trades|pending|metadata
// Legacy pending_radar aliases pending. Defaults: book=a, table=daily, limit=120.
// V6/V10 state is one validated atomic JSONB document, never a legacy fallback.

export const config = { runtime: 'edge' };

const SUPA_URL  = 'https://cuvchjhaojhmxfgczndy.supabase.co';
const SUPA_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImN1dmNoamhhb2pobXhmZ2N6bmR5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ4ODYwNzYsImV4cCI6MjEwMDQ2MjA3Nn0.liH06gqou8QD0ifOLbNDohZjP5dsEk_RzH1WaXf1wtM';

// Book A = frozen proof (Book D); Book B = challenger (Book H gold 252 + spill50); Book C = champion multi-horizon.
// Same schema/RLS on all pairs; only the table names differ.
const TABLES = {
  a: { daily: 'apex_paper_daily', positions: 'apex_paper_positions' },
  b: { daily: 'apex_paper_b_daily', positions: 'apex_paper_b_positions' },
  c: { daily: 'apex_paper_c_daily', positions: 'apex_paper_c_positions', fallbackId: '__apex_book_c_paper_runtime__' },
  r: { fallbackId: '__apex_book_r_252_forward_paper_runtime__' },
  s: { daily: 'apex_paper_s_daily', positions: 'apex_paper_s_positions', fallbackId: '__apex_book_s_session_smc_runtime__' },
  f: { daily: 'apex_paper_f_daily', positions: 'apex_paper_f_positions', fallbackId: '__apex_book_f_prop_shield_runtime__' },
  v6: { fallbackId: '__apex_book_v6_forward_paper_runtime__', profile: 'strict_3_6_static' },
  v10: { fallbackId: '__apex_book_v10_forward_paper_runtime__', profile: 'standard_5_10_static' },
  v24: { fallbackId: '__apex_book_v24_forward_paper_runtime__', profile: 'higher_5_12_static' },
  v30: { fallbackId: '__apex_book_v30_forward_paper_runtime__', profile: 'higher_5_12_static' },
};
const PUBLIC_TABLES = new Set(['state', 'daily', 'positions', 'trades', 'pending', 'metadata', 'pending_radar']);
const COLLECTIONS = ['daily', 'positions', 'trades', 'pending'];
const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const isRows = value => Array.isArray(value) && value.every(isRecord);

class DataError extends Error {
  constructor(status = 503, code = 'paper_data_unavailable') {
    super(code);
    this.status = status;
    this.code = code;
  }
}

function supaHeaders() {
  return {
    'apikey': SUPA_ANON,
    'Authorization': `Bearer ${SUPA_ANON}`,
    'Content-Type': 'application/json',
  };
}

function corsHeaders(origin) {
  return {
    'Access-Control-Allow-Origin': origin || '*',
    'Access-Control-Allow-Methods': 'GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Content-Type': 'application/json',
    'Cache-Control': 'public, s-maxage=10, stale-while-revalidate=59',
  };
}

async function fetchRows(url) {
  let response;
  try {
    response = await fetch(url, { method: 'GET', headers: supaHeaders() });
  } catch {
    throw new DataError();
  }
  if (!response.ok) throw new DataError();
  let rows;
  try { rows = await response.json(); } catch { throw new DataError(); }
  if (!isRows(rows)) throw new DataError();
  return rows;
}

async function runtimeState(book) {
  const id = TABLES[book].fallbackId;
  if (!id) throw new DataError();
  const rows = await fetchRows(`${SUPA_URL}/rest/v1/apex_analyses?id=eq.${id}&select=feature_vector&limit=1`);
  if (!rows.length) throw new DataError(404, 'paper_state_not_found');
  if (rows.length !== 1 || !isRecord(rows[0].feature_vector)) throw new DataError();
  const state = rows[0].feature_vector;
  if (state.book_id !== undefined && state.book_id !== book) throw new DataError();
  return state;
}

async function repairedRequest(book, table, limit, archive) {
  const id=archive?`__apex_book_${book}_archive_20260905__`:`__apex_book_${book}_repaired_v2__`;
  const rows=await fetchRows(`${SUPA_URL}/rest/v1/apex_analyses?id=eq.${id}&select=feature_vector&limit=1`);
  if(rows.length!==1)throw new DataError(404,'paper_state_not_found');
  const raw=rows[0].feature_vector;
  const p=archive?raw?.snapshot:raw;
  const currency=['a','b','c'].includes(book)?'GBP':'USD';
  if(!isRecord(p)||p.book_id!==book||p.metadata?.book_id!==book||p.metadata.account_currency!==currency||
    p.metadata.initial_equity!==100000||p.metadata.paper_only!==true||p.metadata.broker_enabled!==false||
    !COLLECTIONS.every(k=>isRows(p[k])))throw new DataError();
  if(!archive&&(p.schema_version!==2||p.metadata.accounting_version!=='quote_cash_v2'||p.state?.book_id!==book))throw new DataError();
  return project(archive?{...p,metadata:{...p.metadata,archived:true}}:p,table,limit);
}

function validateExperimental(state, book) {
  if (state.schema_version !== 1 || state.book_id !== book ||
      !isRecord(state.metadata) || state.metadata.book_id !== book ||
      state.metadata.profile !== TABLES[book].profile ||
      state.metadata.account_currency !== 'GBP' || state.metadata.initial_equity !== 100000 ||
      state.metadata.paper_only !== true || state.metadata.broker_enabled !== false ||
      !COLLECTIONS.every(key => isRows(state[key]))) throw new DataError();
  return state;
}

function chronological(rows, limit, table = 'daily') {
  const time = row => String(table === 'trades'
    ? (row.exit_time || row.exit_date || row.date || '')
    : (row.date || row.decision_date || ''));
  return [...rows].sort((a, b) => time(a).localeCompare(time(b))).slice(-limit);
}

function project(state, table, limit) {
  if (table === 'state') return state; // Keep the complete atomic document intact.
  if (table === 'metadata') return state.metadata;
  const rows = state[table];
  if (!isRows(rows)) throw new DataError();
  return table === 'positions' || table === 'pending'
    ? rows.slice(0, limit) : chronological(rows, limit, table);
}

function legacyExtra(daily) {
  const latest = daily.length ? daily[daily.length - 1] : null;
  return latest && isRecord(latest.state_extra) ? latest.state_extra : {};
}

function legacyTrades(daily, state = null) {
  const extra = legacyExtra(daily);
  const rows = state?.trades ?? extra.trades;
  if (rows === undefined && daily.length === 0) return [];
  if (!isRows(rows)) throw new DataError();
  return rows;
}

function legacyPending(extra, state = null) {
  const value = state?.pending_radar ?? state?.pending ?? extra.pending_radar ?? extra.pending ?? extra.pending_orders;
  if (value === undefined || value === null) return [];
  // Legacy engines used both arrays and instrument-keyed dictionaries.
  const rows = isRecord(value) ? Object.entries(value).map(([instrument, row]) =>
    isRecord(row) ? { ...row, instrument: row.instrument || instrument } : row) : value;
  if (!isRows(rows)) throw new DataError();
  return rows;
}

function legacyMetadata(book, daily, state = null, atomic = false) {
  const latest = daily.length ? daily[daily.length - 1] : null;
  const extra = legacyExtra(daily);
  const currency = ['a', 'b', 'c'].includes(book) ? 'GBP' : 'USD';
  const halted = Boolean(state?.halted ?? extra.halted);
  return {
    ...(isRecord(state?.metadata) ? state.metadata : {}),
    book_id: book, label: `Book ${book.toUpperCase()}`, currency, account_currency: currency,
    paper_only: true, broker_enabled: false, atomic_snapshot: atomic,
    halted, status: halted ? 'halted' : state?.status || extra.status || (latest ? 'paper_snapshot' : 'no_data'),
    last_data_as_of: state?.last_processed_date || extra.last_processed_date || latest?.date || null,
    initial_equity: state?.initial_equity ?? extra.initial_equity ?? null,
  };
}

function normalizeLegacyState(raw, book, limit) {
  const rawDaily = raw.daily ?? raw.equity_curve;
  const rawPositions = isRecord(raw.positions) ? Object.values(raw.positions) : raw.positions;
  if (!isRows(rawDaily) || !isRows(rawPositions)) throw new DataError();
  const daily = chronological(rawDaily, limit);
  return {
    book_id: book, generated_at_utc: raw.generated_at_utc || raw.updated_at || null,
    daily, positions: rawPositions.slice(0, limit),
    trades: chronological(legacyTrades(daily, raw), limit, 'trades'),
    pending: legacyPending(legacyExtra(daily), raw).slice(0, limit),
    metadata: legacyMetadata(book, daily, raw, true),
  };
}

async function dedicatedRows(book, table, limit) {
  const name = TABLES[book][table];
  if (!name) throw new DataError();
  const order = table === 'daily' ? 'date.desc' : 'instrument.asc';
  const rows = await fetchRows(`${SUPA_URL}/rest/v1/${name}?order=${order}&limit=${limit}`);
  return table === 'daily' ? chronological(rows, limit) : rows;
}

async function legacyRequest(book, table, limit) {
  if (!TABLES[book].daily) return project(normalizeLegacyState(await runtimeState(book), book, limit), table, limit);
  let daily;
  let positions;
  try {
    if (table === 'daily' || table === 'positions') return await dedicatedRows(book, table, limit);
    if (table === 'state') {
      const pair = await Promise.allSettled([
        dedicatedRows(book, 'daily', limit), dedicatedRows(book, 'positions', limit),
      ]);
      if (pair.some(result => result.status === 'rejected')) {
        const error = new DataError();
        // If one primary side is readable, replacing the pair with an older
        // mirror could contradict a valid flat/open state. Fail closed instead.
        error.noFallback = pair.some(result => result.status === 'fulfilled');
        throw error;
      }
      [daily, positions] = pair.map(result => result.value);
    } else {
      // Logs live inside the latest daily snapshot, not in the daily array.
      daily = await dedicatedRows(book, 'daily', 1);
    }
  } catch (error) {
    if (error.noFallback || !TABLES[book].fallbackId) throw error;
    // A failed/malformed source can fall back; a successful empty positions
    // array cannot. This avoids resurrecting closed lots from stale mirrors.
    return project(normalizeLegacyState(await runtimeState(book), book, limit), table, limit);
  }
  // Successfully read primary tables remain authoritative. An absent/malformed
  // embedded log is an availability error, not permission to swap in an older
  // mirror that could resurrect positions or mix incompatible book revisions.
  if (table === 'state') return {
    book_id: book, generated_at_utc: null, daily, positions,
    trades: chronological(legacyTrades(daily), limit, 'trades'),
    pending: legacyPending(legacyExtra(daily)).slice(0, limit),
    metadata: legacyMetadata(book, daily),
  };
  if (table === 'trades') return chronological(legacyTrades(daily), limit, 'trades');
  if (table === 'pending') return legacyPending(legacyExtra(daily)).slice(0, limit);
  return legacyMetadata(book, daily);
}

export default async function handler(req) {
  const url    = new URL(req.url);
  const origin = req.headers.get('origin');
  const cors   = corsHeaders(origin);

  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
  if (req.method !== 'GET') return new Response(JSON.stringify({ error: 'Method not allowed' }), { status: 405, headers: cors });

  const book = url.searchParams.get('book') ?? 'a';
  const requestedTable = url.searchParams.get('table') ?? 'daily';
  const rawLimit = url.searchParams.get('limit') ?? '120';
  const edition = url.searchParams.get('edition') ?? 'repaired';
  if (!Object.hasOwn(TABLES, book) || !PUBLIC_TABLES.has(requestedTable) ||
      !['repaired','archive','legacy'].includes(edition) ||
      !/^[1-9]\d*$/.test(rawLimit) || Number(rawLimit) > 500) {
    return new Response(JSON.stringify({ error: 'Invalid book, table or limit' }),
      { status: 400, headers: { ...cors, 'Cache-Control': 'no-store' } });
  }
  const table = requestedTable === 'pending_radar' ? 'pending' : requestedTable;
  const limit = Number(rawLimit);
  try {
    const payload = ['v6', 'v10', 'v24', 'v30'].includes(book)
      ? project(validateExperimental(await runtimeState(book), book), table, limit)
      : edition==='legacy'?await legacyRequest(book,table,limit):await repairedRequest(book,table,limit,edition==='archive');
    return new Response(JSON.stringify(payload), { status: 200, headers: cors });
  } catch (error) {
    const status = error instanceof DataError ? error.status : 503;
    const code = error instanceof DataError ? error.code : 'paper_data_unavailable';
    return new Response(JSON.stringify({ error: code }),
      { status, headers: { ...cors, 'Cache-Control': 'no-store' } });
  }
}
