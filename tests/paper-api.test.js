import assert from 'node:assert/strict';
import test from 'node:test';
import handler from '../api/paper.js';

const jsonResponse = (value, status = 200) => new Response(JSON.stringify(value), { status });
const request = (query = '', method = 'GET') => new Request(`https://apexfx.test/api/paper${query}`, { method });
const fixture = (book = 'v6') => ({
  schema_version: 1, book_id: book, generated_at_utc: '2026-09-05T12:00:00Z',
  state: { durable_revision: 7 },
  daily: [{ date: '2026-09-04', equity: 100000 }, { date: '2026-09-05', equity: 100001 }],
  positions: [{ instrument: 'SPY', units: 1 }],
  trades: [{ instrument: 'XLF', exit_time: '2026-09-04', pnl: 1 }],
  pending: [{ instrument: 'XLV', decision_date: '2026-09-05' }],
  metadata: { book_id: book, profile: book === 'v6' ? 'strict_3_6_static' : 'standard_5_10_static',
    account_currency: 'GBP', initial_equity: 100000, paper_only: true, broker_enabled: false },
});

async function withFetch(implementation, run) {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options });
    assert.equal(options.method, 'GET');
    return implementation(String(url), options);
  };
  try { return await run(calls); } finally { globalThis.fetch = original; }
}

test('default A daily reads the most recent window, then returns chronological rows', async () => {
  await withFetch(url => {
    assert.match(url, /apex_paper_daily\?order=date.desc&limit=2$/);
    return jsonResponse([{ date: '2026-09-05' }, { date: '2026-09-04' }]);
  }, async calls => {
    const response = await handler(request('?limit=2'));
    assert.equal(response.status, 200);
    assert.deepEqual((await response.json()).map(r => r.date), ['2026-09-04', '2026-09-05']);
    assert.equal(calls.length, 1);
  });
});

test('unknown and malformed query values are 400 without any upstream request', async () => {
  await withFetch(() => { throw new Error('network must not run'); }, async calls => {
    for (const query of ['?book=unknown', '?book=toString', '?book=', '?table=unknown', '?table=',
      ...['', '0', '-1', '1.5', 'NaN', '500x', '501', 'Infinity'].map(n => `?limit=${n}`)]) {
      const response = await handler(request(query));
      assert.equal(response.status, 400, query);
      assert.equal(response.headers.get('cache-control'), 'no-store');
    }
    assert.equal(calls.length, 0);
  });
});

test('OPTIONS and unsupported write methods never invoke upstream', async () => {
  await withFetch(() => { throw new Error('network must not run'); }, async calls => {
    assert.equal((await handler(request('', 'OPTIONS'))).status, 204);
    for (const method of ['POST', 'PATCH', 'DELETE']) assert.equal((await handler(request('', method))).status, 405);
    assert.equal(calls.length, 0);
  });
});

test('V6 state is an untouched atomic document from exactly one matching namespace', async () => {
  const state = fixture();
  await withFetch(url => {
    assert.match(url, /id=eq.__apex_book_v6_forward_paper_runtime__/);
    return jsonResponse([{ feature_vector: state }]);
  }, async calls => {
    const response = await handler(request('?book=v6&table=state&limit=1'));
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), state); // limit must not truncate atomic state.
    assert.equal(calls.length, 1);
  });
});

test('V10 namespace and metadata remain independent of V6 and legacy A', async () => {
  const state = fixture('v10');
  await withFetch(url => {
    assert.match(url, /id=eq.__apex_book_v10_forward_paper_runtime__/);
    assert.doesNotMatch(url, /apex_paper_daily/);
    return jsonResponse([{ feature_vector: state }]);
  }, async calls => {
    const response = await handler(request('?book=v10&table=metadata'));
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), state.metadata);
    assert.equal(calls.length, 1);
  });
});

test('experimental projections and pending_radar alias return only their collection', async () => {
  const state = fixture();
  await withFetch(() => jsonResponse([{ feature_vector: state }]), async () => {
    for (const table of ['daily', 'positions', 'trades', 'pending', 'pending_radar']) {
      const response = await handler(request(`?book=v6&table=${table}&limit=1`));
      assert.equal(response.status, 200);
      const expected = table === 'daily' ? state.daily.slice(-1) : state[table === 'pending_radar' ? 'pending' : table];
      assert.deepEqual(await response.json(), expected, table);
    }
  });
});

test('missing experimental state is 404, not fabricated seeded data or fallback A', async () => {
  await withFetch(() => jsonResponse([]), async calls => {
    const response = await handler(request('?book=v6&table=state'));
    assert.equal(response.status, 404);
    assert.deepEqual(await response.json(), { error: 'paper_state_not_found' });
    assert.equal(calls.length, 1);
  });
});

test('experimental schema, identity, arrays and paper-account invariants fail closed', async () => {
  const cases = [
    state => { state.book_id = 'v10'; },
    state => { state.metadata.book_id = 'v10'; },
    state => { state.schema_version = 2; },
    state => { state.metadata.profile = 'standard_5_10_static'; },
    state => { state.metadata.account_currency = 'USD'; },
    state => { state.metadata.initial_equity = 10000; },
    state => { state.metadata.broker_enabled = true; },
    state => { state.metadata.paper_only = false; },
    state => { state.metadata = null; },
    ...['daily', 'positions', 'trades', 'pending'].flatMap(key => [
      state => { delete state[key]; }, state => { state[key] = {}; }, state => { state[key] = [42]; },
    ]),
  ];
  for (const mutate of cases) {
    const state = fixture(); mutate(state);
    await withFetch(() => jsonResponse([{ feature_vector: state }]), async calls => {
      const response = await handler(request('?book=v6&table=state'));
      assert.equal(response.status, 503);
      assert.deepEqual(await response.json(), { error: 'paper_data_unavailable' });
      assert.equal(calls.length, 1);
    });
  }
});

test('null/malformed upstream payload and private error bodies never leak', async () => {
  for (const implementation of [
    () => jsonResponse([{ feature_vector: null }]),
    () => jsonResponse({ private_message: 'DO_NOT_LEAK' }),
    () => new Response('DO_NOT_LEAK', { status: 403 }),
    () => new Response('not JSON: DO_NOT_LEAK', { status: 200 }),
    () => { throw new Error('DO_NOT_LEAK'); },
  ]) await withFetch(implementation, async calls => {
    const response = await handler(request('?book=v10&table=state'));
    assert.equal(response.status, 503);
    assert.doesNotMatch(await response.text(), /DO_NOT_LEAK|Supabase|eyJ/);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.equal(calls.length, 1);
  });
});

test('legacy successful trades request reads state_extra.trades, never daily rows', async () => {
  const trades = [{ instrument: 'SPY', exit_time: '2026-09-03', pnl: 12 },
    { instrument: 'XLV', exit_time: '2026-09-04', pnl: -2 }];
  await withFetch(url => {
    assert.match(url, /apex_paper_b_daily\?order=date.desc&limit=1$/);
    return jsonResponse([{ date: '2026-09-05', equity: 100010, state_extra: { trades } }]);
  }, async () => {
    const response = await handler(request('?book=b&table=trades&limit=1'));
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), trades.slice(-1));
  });
});

test('legacy state joins daily/positions/actual trades and identifies non-atomic metadata', async () => {
  const trade = { instrument: 'XLE', exit_time: '2026-09-04', pnl: 5 };
  await withFetch(url => url.includes('apex_paper_positions')
    ? jsonResponse([{ instrument: 'SPY', units: 1 }])
    : jsonResponse([{ date: '2026-09-05', equity: 100005,
      state_extra: { trades: [trade], initial_equity: 100000, pending: { XLV: { direction: 'long' } } } }]),
  async calls => {
    const response = await handler(request('?book=a&table=state'));
    const state = await response.json();
    assert.equal(response.status, 200);
    assert.equal(state.metadata.currency, 'GBP');
    assert.equal(state.metadata.atomic_snapshot, false);
    assert.equal(state.metadata.initial_equity, 100000);
    assert.deepEqual(state.trades, [trade]);
    assert.equal(state.pending[0].instrument, 'XLV');
    assert.equal(state.positions[0].instrument, 'SPY');
    assert.equal(calls.length, 2);
  });
});

test('successful empty legacy positions are authoritative and do not resurrect mirrors', async () => {
  await withFetch(url => {
    assert.match(url, /apex_paper_c_positions/);
    return jsonResponse([]);
  }, async calls => {
    const response = await handler(request('?book=c&table=positions'));
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), []);
    assert.equal(calls.length, 1);
  });
});

test('legacy mirror fallback preserves actual trades, positions and pending radar', async () => {
  const raw = { daily: [{ date: '2026-09-04', equity: 100100 }],
    positions: { EURUSD: { instrument: 'EUR/USD', units: 1 } },
    trades: [{ instrument: 'USD/JPY', exit_time: '2026-09-03', pnl: 100 }],
    pending_radar: [{ instrument: 'GBP/USD', trigger: 1.3 }] };
  await withFetch(url => url.includes('apex_analyses')
    ? jsonResponse([{ feature_vector: raw }]) : new Response('DO_NOT_LEAK', { status: 404 }),
  async () => {
    for (const table of ['state', 'trades', 'pending_radar']) {
      const response = await handler(request(`?book=s&table=${table}`));
      const payload = await response.json();
      assert.equal(response.status, 200);
      if (table === 'state') {
        assert.equal(payload.metadata.account_currency, 'USD');
        assert.deepEqual(payload.trades, raw.trades);
        assert.deepEqual(payload.pending, raw.pending_radar);
      } else assert.deepEqual(payload, table === 'trades' ? raw.trades : raw.pending_radar);
    }
  });
});

test('legacy R trades projection no longer aliases daily', async () => {
  const raw = { equity_curve: [{ date: '2026-09-05', equity: 100030 }], positions: [],
    trades: [{ instrument: 'SPY', exit_time: '2026-09-04', pnl: 30 }], pending: {} };
  await withFetch(() => jsonResponse([{ feature_vector: raw }]), async () => {
    const response = await handler(request('?book=r&table=trades'));
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), raw.trades);
  });
});

test('legacy missing trade log is unavailable, not silently replaced with daily rows', async () => {
  await withFetch(() => jsonResponse([{ date: '2026-09-05', equity: 100000 }]), async () => {
    const response = await handler(request('?book=a&table=trades'));
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: 'paper_data_unavailable' });
  });
});

test('missing primary log cannot replace a successfully flat legacy state with old holdings', async () => {
  await withFetch(url => {
    assert.doesNotMatch(url, /apex_analyses/);
    return url.includes('_positions') ? jsonResponse([])
      : jsonResponse([{ date: '2026-09-05', equity: 100000 }]);
  }, async calls => {
    const response = await handler(request('?book=c&table=state'));
    assert.equal(response.status, 503);
    assert.equal(calls.length, 2);
  });
});

test('a partial primary-state read fails rather than replacing valid flat positions with a mirror', async () => {
  await withFetch(url => {
    assert.doesNotMatch(url, /apex_analyses/);
    return url.includes('_positions') ? jsonResponse([]) : new Response('offline', { status: 503 });
  }, async calls => {
    const response = await handler(request('?book=c&table=state'));
    assert.equal(response.status, 503);
    assert.equal(calls.length, 2);
  });
});

test('legacy daily/positions/metadata default endpoints remain read-only', async () => {
  await withFetch(url => url.includes('_positions') ? jsonResponse([]) : jsonResponse([
    { date: '2026-09-05', equity: 95000, state_extra: { trades: [], halted: true } },
  ]), async () => {
    for (const table of ['daily', 'positions', 'metadata']) {
      const response = await handler(request(`?book=b&table=${table}&limit=500`));
      assert.equal(response.status, 200);
      const value = await response.json();
      if (table === 'metadata') {
        assert.equal(value.account_currency, 'GBP');
        assert.equal(value.status, 'halted');
      } else assert.ok(Array.isArray(value));
    }
  });
});
