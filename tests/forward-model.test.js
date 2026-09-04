import assert from 'node:assert/strict';
import test from 'node:test';
import { number, money, summarize, tradeCard, escapeHtml } from '../public/forward-model.js';

function ledger(book = 'v6') {
  return {
    schema_version: 1, book_id: book, generated_at_utc: '2026-09-08T21:00:00Z', state: {},
    metadata: {
      book_id: book, profile: book === 'v6' ? 'strict_3_6_static' : 'standard_5_10_static',
      account_currency: 'GBP', initial_equity: 100000, paper_only: true, broker_enabled: false,
      activation_recorded_at_utc: '2026-09-05T12:00:00Z', first_eligible_decision_session: '2026-09-08',
      last_processed_session: '2026-09-08', last_data_as_of: '2026-09-08T20:00:00Z', spec_sha256: 'frozen-test-spec',
    },
    daily: [
      { date: '2026-09-05', equity: 100000, cash: 100000, open_pnl: 0, is_seed: true, drawdown_from_peak: 0 },
      { date: '2026-09-08', equity: 100250, cash: 100100, open_pnl: 150, day_pnl: 250,
        external_daily_floor: book === 'v6' ? 97000 : 95000,
        external_maximum_floor: book === 'v6' ? 94000 : 90000,
        internal_daily_floor: book === 'v6' ? 97750 : 96250,
        internal_maximum_floor: book === 'v6' ? 95500 : 92500,
        drawdown_from_peak: 0, metrics: { cost_total_gbp: 10 } },
    ],
    positions: [], trades: [], pending: [],
  };
}

const position = () => ({
  instrument: 'SPY', direction: 'long', units: 12.5, account_currency: 'GBP',
  entry_price: 100, stop: 95, initial_stop: 95, last_px: 105, open_pnl: 42.5,
  decision_date: '2026-09-04', entry_time: '2026-09-08T13:30:00Z',
  decision_recorded_at_utc: '2026-09-04T20:05:00Z', scheduled_exit_session: '2026-09-15',
  decision_atr: 3.25, lagged_vix: 32.4, lagged_vix_source_date: '2026-09-03',
  initial_total_risk: 740, current_risk_gbp: 650,
  signal_rationale: 'Prior-session sector reversal under the lagged stress filter.',
});

test('number preserves genuine zero and negative values, including numeric strings', () => {
  for (const [input, expected] of [[0, 0], [-12.5, -12.5], ['0', 0], [' 12.50 ', 12.5], ['-3.1', -3.1], ['1e2', 100]]) {
    assert.equal(number(input), expected);
  }
});

test('number does not fabricate zero/one from missing, boolean, array or object values', () => {
  for (const value of [undefined, null, '', '  ', '\n', false, true, [], [1], {}, NaN, Infinity, -Infinity, 'NaN', 'Infinity', '£100']) {
    assert.equal(number(value), null, String(value));
  }
});

test('money defaults to GBP and distinguishes missing from actual zero', () => {
  assert.equal(money(100000), '£100,000.00');
  assert.equal(money(42.5, true), '+£42.50');
  assert.equal(money(-42.5, true), '-£42.50');
  assert.equal(money(0, true), '£0.00');
  assert.equal(money(null), '—');
  assert.equal(money(false), '—');
  assert.equal(money(' '), '—');
});

test('summarize uses the published GBP contract, not market quotes or invented FX conversion', () => {
  const payload = ledger();
  payload.live_equity = 999999;
  payload.live_marks = { SPY: 10000 };
  payload.gbpusd = 0.1;
  payload.positions = [position()];
  const model = summarize(payload, 'v6');
  assert.equal(model.equity, 100250);
  assert.equal(model.cash, 100100);
  assert.equal(model.pnl, 250);
  assert.equal(model.openPnl, 150);
  assert.equal(model.dayPnl, 250);
  assert.equal(model.dailyFloor, 97000);
  assert.equal(model.maxFloor, 94000);
  assert.equal(model.activation, payload.metadata.activation_recorded_at_utc);
  assert.equal(model.through, payload.metadata.last_processed_session);
  assert.equal(model.sessions, 1);
});

test('public latest daily fields take precedence over stale durable-state duplicates', () => {
  const payload = ledger();
  payload.state = { equity: 100000, equity_gbp: 100000, cash: 99900, cash_gbp: 99900,
    open_pnl_gbp: 100, floors: { external_daily: 80000, external_maximum: 70000 } };
  const model = summarize(payload, 'v6');
  assert.equal(model.equity, 100250);
  assert.equal(model.cash, 100100);
  assert.equal(model.openPnl, 150);
  assert.equal(model.dailyFloor, 97000);
  assert.equal(model.maxFloor, 94000);
});

test('both profiles retain distinct official static floors and correct selected identity', () => {
  const strict = summarize(ledger('v6'), 'v6');
  const standard = summarize(ledger('v10'), 'v10');
  assert.equal(strict.maxFloor, 94000);
  assert.equal(standard.maxFloor, 90000);
  assert.equal(standard.dailyFloor, 95000);
  assert.throws(() => summarize(ledger('v6'), 'v10'));
});

test('summarize rejects mismatched currency, metadata identity, profile or live-account flags', () => {
  const mutations = [
    p => { p.book_id = 'v10'; }, p => { p.metadata.book_id = 'v10'; },
    p => { p.metadata.account_currency = 'USD'; },
    p => { p.metadata.profile = 'standard_5_10_static'; },
    p => { p.metadata.initial_equity = 10000; },
    p => { p.metadata.paper_only = false; }, p => { p.metadata.broker_enabled = true; },
    p => { delete p.metadata; },
  ];
  for (const mutate of mutations) {
    const payload = ledger(); mutate(payload);
    assert.throws(() => summarize(payload, 'v6'));
  }
  for (const book of ['unknown', 'toString', 'constructor', '__proto__']) {
    const unknown = ledger();
    unknown.book_id = book; unknown.metadata.book_id = book;
    unknown.metadata.profile = 'standard_5_10_static';
    assert.throws(() => summarize(unknown, book), book);
  }
});

test('summarize rejects missing collections and non-record rows before rendering', () => {
  for (const key of ['daily', 'positions', 'trades', 'pending']) {
    for (const value of [undefined, {}, [null], [42]]) {
      const payload = ledger(); payload[key] = value;
      assert.throws(() => summarize(payload, 'v6'), `${key}: ${JSON.stringify(value)}`);
    }
  }
});

test('missing GBP balances fail rather than fabricate the £100k seed', () => {
  for (const key of ['equity', 'cash']) {
    const payload = ledger(); payload.daily = [{ date: '2026-09-08', equity: 100000, cash: 100000 }];
    delete payload.daily[0][key];
    assert.throws(() => summarize(payload, 'v6'), /balance|ledger/i);
  }
  const payload = ledger(); payload.daily = [];
  assert.throws(() => summarize(payload, 'v6'), /balance|ledger/i);
});

test('genuine zero and negative failed-account balances remain visible', () => {
  for (const value of [0, -500]) {
    const payload = ledger(); Object.assign(payload.daily.at(-1), { equity: value, cash: value, open_pnl: 0 });
    const model = summarize(payload, 'v6');
    assert.equal(model.equity, value);
    assert.equal(model.cash, value);
    assert.equal(model.pnl, value-100000);
  }
});

test('closed P&L uses net ledgers and missing P&L does not count as zero or a loss', () => {
  const payload = ledger();
  payload.trades = [{ net_pnl_gbp: 20, gross_pnl_gbp: 2000 }, { pnl: -5 }, { pnl: 0 }];
  let model = summarize(payload, 'v6');
  assert.equal(model.closedPnl, 15);
  assert.equal(model.winRate, 1/3);
  payload.trades.push({ instrument: 'XLE' });
  model = summarize(payload, 'v6');
  assert.equal(model.closedPnl, null);
  assert.equal(model.winRate, null);
  payload.trades = [];
  model = summarize(payload, 'v6');
  assert.equal(model.closedPnl, 0);
  assert.equal(model.winRate, null);
});

test('missing drawdown data is unavailable rather than an invented zero drawdown', () => {
  const payload = ledger();
  for (const row of payload.daily) delete row.drawdown_from_peak;
  assert.equal(summarize(payload, 'v6').maxDD, null);
});

test('sorting display snapshots never mutates the published payload', () => {
  const payload = ledger(); payload.daily.reverse();
  const before = structuredClone(payload);
  const model = summarize(payload, 'v6');
  assert.deepEqual(payload, before);
  assert.equal(model.latest.date, '2026-09-08');
});

test('position cards show USD prices but official GBP P&L and saved decision evidence', () => {
  const row = position();
  row.live_price = 9999;
  row.live_pnl = 88888;
  const html = tradeCard(row);
  assert.match(html, /Entry · USD/);
  assert.match(html, /\$100\.00/);
  assert.match(html, /Official mark · USD/);
  assert.match(html, /\$105\.00/);
  assert.match(html, /\+£42\.50/);
  assert.match(html, /£650\.00/);
  assert.match(html, /£740\.00/);
  assert.match(html, /32\.4/);
  assert.match(html, /\$3\.25/);
  assert.match(html, /Prior-session sector reversal/);
  assert.match(html, /15 Sept? 2026/);
  assert.doesNotMatch(html, /9,?999|88,?888/);
});

test('strategy cards never invent fixed take-profit, partials or guaranteed-risk labels', () => {
  const row = position(); row.target = 120; row.partial_taken = true;
  const html = tradeCard(row);
  assert.match(html, /Take-profit \/ partials/);
  assert.match(html, /Not used by this strategy/);
  assert.match(html, /not a guaranteed fill price/);
  assert.doesNotMatch(html, /\$120\.00|RISK-FREE|\+1\.0R|\+2\.0R|secured/i);
});

test('pending cards distinguish queued decisions from filled trades', () => {
  const html = tradeCard({ instrument: 'XLF', direction: 'short', decision_atr: 2, lagged_vix: 31,
    decision_date: '2026-09-08', eligible_fill_session: '2026-09-09' }, 'pending');
  assert.match(html, /Queued/);
  assert.match(html, /Pending · next eligible open/);
  assert.match(html, /Next-open simulation/);
  assert.match(html, /1\.5 × prior ATR20 at fill/);
  assert.match(html, /At fill/);
  assert.match(html, />SHORT</);
  assert.doesNotMatch(html, /NaN|Infinity|\+£0\.00/);
});

test('unknown directions remain unknown instead of an invented long position', () => {
  for (const direction of [undefined, null, 0, true, false, [], {}, 'sideways']) {
    const html = tradeCard({ ...position(), direction });
    assert.match(html, />UNKNOWN</, String(direction));
    assert.doesNotMatch(html, />LONG<|>SHORT</);
  }
  assert.match(tradeCard({ ...position(), direction: -1 }), />SHORT</);
  assert.match(tradeCard({ ...position(), direction: 1 }), />LONG</);
});

test('closed cards show actual net GBP result and supplied exit reason', () => {
  const html = tradeCard({ ...position(), exit_price: 96.2, pnl: -62.34,
    exit_reason: 'maintenance_guard', gross_pnl_gbp: 500 }, 'trades');
  assert.match(html, /Exit · USD/);
  assert.match(html, /\$96\.20/);
  assert.match(html, /-£62\.34/);
  assert.match(html, /maintenance guard/);
  assert.doesNotMatch(html, /TAKE PROFIT|£500\.00/);
});

test('trade-card untrusted symbol, rationale and exit reason are escaped', () => {
  const html = tradeCard({ ...position(), instrument: '<img src=x onerror="alert(1)">',
    signal_rationale: '</p><script>alert("reason")</script>',
    exit_reason: '<svg onload="alert(2)">' }, 'trades');
  assert.doesNotMatch(html, /<img|<script|<svg|onerror="|onload="/);
  assert.match(html, /&lt;img/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /&lt;svg/);
  assert.equal(escapeHtml('&<>"\''), '&amp;&lt;&gt;&quot;&#39;');
});
