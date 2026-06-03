// APEX Backtest Lab — page controller.
// Owns the DOM, the candle fetching (main thread, reuses /api/candles + Vercel
// edge cache), the Web Worker (pure compute), the job queue, and storage POSTs.
// Strategies/metrics come from the shared APEX.* libs loaded before this script.
'use strict';

(function () {
  const $ = (id) => document.getElementById(id);
  const A = window.APEX;
  const DS = A.datasource;
  const APP_VERSION = 'bt1';

  // ── Universe (mirrors QUICK_PICKS / engine config) ──────────────────────────
  const UNIVERSE = {
    Forex:  ['EUR/USD','GBP/USD','USD/JPY','USD/CHF','AUD/USD','USD/CAD','NZD/USD','GBP/JPY','EUR/GBP','EUR/JPY'],
    Crypto: ['BTC/USD','ETH/USD','SOL/USD','BNB/USD','XRP/USD','ADA/USD','AVAX/USD','DOGE/USD','MATIC/USD','LINK/USD','ARB/USD','SUI/USD'],
    Stock:  ['NVDA','AAPL','MSFT','META','AMZN','GOOGL','TSLA','AMD','PLTR','TSM','NFLX','UBER'],
    ETF:    ['SPY','QQQ','IWM','GLD','TLT','XLK','XLE','XLF','ARKK','SMH','SOXX','XBI'],
  };
  const TYPE_OF = {};
  for (const t in UNIVERSE) for (const s of UNIVERSE[t]) TYPE_OF[s] = t;
  const ALL_SYMS = Object.values(UNIVERSE).flat();
  const TFS = DS.TIMEFRAMES.slice();

  // ── State ───────────────────────────────────────────────────────────────────
  const selSyms = new Set(['EUR/USD']);   // sensible default starter
  const selTfs = new Set(['1d']);
  let worker = null, running = false, paused = false, cancelled = false;
  let currentRows = [];                    // rows from the active run (+ reloads)
  const fmt = (v, d = 2) => (v == null || isNaN(v) ? '—' : (+v).toFixed(d));
  const pct = (v) => (v == null || isNaN(v) ? '—' : (+v).toFixed(2) + '%');

  // ── Build runner UI ─────────────────────────────────────────────────────────
  function renderCatChips() {
    const cats = ['All', ...Object.keys(UNIVERSE)];
    $('catChips').innerHTML = cats.map(c => `<button class="bt-chip" data-cat="${c}">${c}</button>`).join('');
    $('catChips').querySelectorAll('.bt-chip').forEach(b => b.onclick = () => {
      const c = b.dataset.cat;
      const syms = c === 'All' ? ALL_SYMS : UNIVERSE[c];
      const allOn = syms.every(s => selSyms.has(s));
      syms.forEach(s => allOn ? selSyms.delete(s) : selSyms.add(s));
      renderSymGrid(); updateJobCount();
    });
  }
  function renderSymGrid() {
    $('symGrid').innerHTML = ALL_SYMS.map(s =>
      `<button class="bt-sym ${selSyms.has(s) ? 'on' : ''}" data-sym="${s}"><span>${s}</span><span class="bt-sym-t">${TYPE_OF[s]}</span></button>`).join('');
    $('symGrid').querySelectorAll('.bt-sym').forEach(b => b.onclick = () => {
      const s = b.dataset.sym; selSyms.has(s) ? selSyms.delete(s) : selSyms.add(s);
      b.classList.toggle('on'); updateJobCount();
    });
  }
  function renderTfChips() {
    $('tfChips').innerHTML = TFS.map(tf => `<button class="bt-chip ${selTfs.has(tf) ? 'on' : ''}" data-tf="${tf}">${tf}</button>`).join('');
    $('tfChips').querySelectorAll('.bt-chip').forEach(b => b.onclick = () => {
      const tf = b.dataset.tf; selTfs.has(tf) ? selTfs.delete(tf) : selTfs.add(tf);
      b.classList.toggle('on'); updateJobCount();
    });
  }
  function updateJobCount() {
    const jobs = selSyms.size * selTfs.size;
    $('jobCount').textContent = `${selSyms.size} pair(s) × ${selTfs.size} timeframe(s) = ${jobs} job(s)`;
    $('runBtn').disabled = running || jobs === 0;
  }

  // ── Worker lifecycle ────────────────────────────────────────────────────────
  function ensureWorker() {
    if (worker) return worker;
    worker = new Worker('./backtest.worker.js');
    return worker;
  }
  // Run one job in the worker; resolves with rows.
  function runJobInWorker(payload, onProgress) {
    return new Promise((resolve, reject) => {
      const w = ensureWorker();
      const jobId = payload.sym + '|' + payload.timeframe + '|' + Math.random().toString(36).slice(2);
      const handler = (e) => {
        const m = e.data;
        if (m.jobId !== jobId) return;
        if (m.type === 'PROGRESS') onProgress && onProgress(m);
        else if (m.type === 'RESULT') { w.removeEventListener('message', handler); resolve(m.rows); }
        else if (m.type === 'ERROR') { w.removeEventListener('message', handler); reject(new Error(m.error)); }
      };
      w.addEventListener('message', handler);
      w.postMessage({ type: 'RUN', jobId, payload });
    });
  }

  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  async function waitWhilePaused() { while (paused && !cancelled) await sleep(150); }

  // ── The run loop ────────────────────────────────────────────────────────────
  async function startRun() {
    if (running) return;
    const syms = ALL_SYMS.filter(s => selSyms.has(s));   // stable order, grouped by type
    const tfs = TFS.filter(t => selTfs.has(t));
    const jobs = [];
    for (const s of syms) for (const tf of tfs) jobs.push({ sym: s, tf });
    if (!jobs.length) return;

    running = true; paused = false; cancelled = false;
    const runTs = Date.now(), runId = 'run_' + runTs;
    setRunState('running');
    $('progressWrap').style.display = '';
    let done = 0, postFailWarned = false;

    for (let j = 0; j < jobs.length; j++) {
      if (cancelled) break;
      await waitWhilePaused();
      if (cancelled) break;
      const { sym, tf } = jobs[j];
      const type = TYPE_OF[sym];
      setProgress(done, jobs.length, `${sym} · ${tf} · fetching candles…`);
      try {
        const bars = await DS.getCandles(sym, type, tf);
        const weekly = (tf === '1d') ? await DS.getWeekly(sym, type).catch(() => null) : null;
        if (!Array.isArray(bars) || bars.length < 30) { done++; setProgress(done, jobs.length, `${sym} · ${tf} · skipped (only ${bars ? bars.length : 0} bars)`); continue; }
        const rows = await runJobInWorker(
          { bars, weekly, sym, assetClass: type, timeframe: tf, runId, runTs, appVersion: APP_VERSION },
          (p) => setProgress(done, jobs.length, `${sym} · ${tf} · ${p.id} (${p.idx}/${p.total})`)
        );
        // render immediately (partial results) + persist
        currentRows = currentRows.concat(rows);
        renderResults();
        const okPost = await postRows(rows);
        if (!okPost && !postFailWarned) { postFailWarned = true; flashMeta('⚠ Could not save to cloud — has the apex_strategy_backtests table been created?'); }
      } catch (err) {
        flashMeta(`⚠ ${sym} ${tf}: ${err.message}`);
      }
      done++;
      setProgress(done, jobs.length, `${sym} · ${tf} · done`);
      await sleep(200); // rate-limit-friendly gap between pairs
    }

    running = false; paused = false;
    setRunState(cancelled ? 'idle' : 'done');
    setProgress(done, jobs.length, cancelled ? 'Stopped.' : 'Run complete.');
    updateJobCount();
    refreshRunFilter();
  }

  async function postRows(rows) {
    try {
      const r = await fetch('/api/backtest-runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(rows) });
      return r.ok;
    } catch { return false; }
  }

  function setRunState(s) {
    $('runState').textContent = s.toUpperCase();
    $('runDot').className = 'bt-dot ' + s;
    $('runBtn').disabled = running;
    $('pauseBtn').disabled = !running;
    $('cancelBtn').disabled = !running;
    $('pauseBtn').textContent = paused ? '▶ Resume' : '⏸ Pause';
  }
  function setProgress(done, total, text) {
    const p = total ? Math.round(done / total * 100) : 0;
    $('progressFill').style.width = p + '%';
    $('progressText').textContent = `${done}/${total} jobs · ${text}`;
  }
  let metaTimer = null;
  function flashMeta(msg) { $('resultMeta').textContent = msg; clearTimeout(metaTimer); metaTimer = setTimeout(() => { $('resultMeta').textContent = `${currentRows.length} rows`; }, 6000); }

  // ── Results table ───────────────────────────────────────────────────────────
  function filteredSorted() {
    const fPair = $('fPair').value, fTf = $('fTf').value, fFam = $('fFamily').value;
    const minSample = $('fMinSample').checked, sort = $('fSort').value;
    let rows = currentRows.filter(r =>
      (!fPair || r.instrument === fPair) && (!fTf || r.timeframe === fTf) &&
      (!fFam || r.strategy_family === fFam) && (!minSample || r.n_trades >= 30));
    const dir = sort === 'max_drawdown' ? 1 : -1; // lower DD better
    rows.sort((a, b) => dir * (((b[sort] ?? -1e9)) - ((a[sort] ?? -1e9))));
    return rows;
  }
  function renderResults() {
    const rows = filteredSorted();
    $('resultMeta').textContent = `${currentRows.length} rows`;
    populateFilterOptions();
    if (!rows.length) { $('btRows').innerHTML = `<tr><td colspan="11" class="bt-empty">No results match the filters.</td></tr>`; return; }
    $('btRows').innerHTML = rows.slice(0, 400).map((r, i) => {
      const flags = (r.low_sample ? '<span class="bt-flag warn" title="fewer than 30 trades — exploratory only">thin</span>' : '') +
        (r.shallow_sharpe ? '<span class="bt-flag warn" title="1m/5m Sharpe is noisy">noisy</span>' : '') +
        (r.regime_filtered ? '<span class="bt-flag rf" title="regime-filtered">RF</span>' : '');
      const sh = r.sharpe == null ? '—' : (+r.sharpe).toFixed(2);
      return `<tr class="${r.low_sample ? 'thin' : ''}">
        <td>${r.instrument}</td><td>${r.timeframe}</td>
        <td>${r.strategy}${flags}</td>
        <td>${r.n_trades}</td><td class="${r.sharpe > 0 ? 'pos' : r.sharpe < 0 ? 'neg' : ''}">${sh}</td>
        <td class="${r.total_return > 0 ? 'pos' : 'neg'}">${pct(r.total_return)}</td>
        <td class="neg">${pct(r.max_drawdown)}</td>
        <td>${fmt(r.win_rate, 1)}%</td><td>${fmt(r.expectancy, 3)}</td>
        <td>${r.profit_factor == null ? '∞' : fmt(r.profit_factor, 2)}</td>
        <td><button class="bt-btn tiny" data-trades="${r.instrument}|${r.timeframe}|${r.strategy}">trades</button></td>
      </tr>`;
    }).join('');
    $('btRows').querySelectorAll('[data-trades]').forEach(b => b.onclick = () => showTrades(b.dataset.trades));
    renderInsights();
  }

  // ── Improvement hypotheses (Layer 5) ────────────────────────────────────────
  function renderInsights() {
    if (!A.hypotheses) return;
    const { meta, cards } = A.hypotheses.buildHypotheses(currentRows);
    if (!cards.length) { $('insightsCard').style.display = 'none'; return; }
    $('insightsCard').style.display = '';
    const range = meta.dataFrom ? `${meta.dataFrom.slice(0, 10)} → ${meta.dataTo.slice(0, 10)}` : '—';
    const framing = `<div class="bt-disclaimer" style="margin:0 0 14px">
      <strong>Based on historical backtest data (${range}, ${meta.totalTrades.toLocaleString()} trades across ${meta.nEligible} eligible results).</strong>
      ${meta.framing}</div>`;
    const html = cards.map(c => {
      let body = '';
      if (c.id === 'signal_power') {
        body = `<table class="bt-table"><thead><tr><th>Confluence signal</th><th>Avg lift (win% aligned − not)</th><th>Runs</th><th>Samples</th></tr></thead><tbody>${
          c.signals.map(s => `<tr><td>${s.label}</td><td class="${s.avgLift > 2 ? 'pos' : s.avgLift < -2 ? 'neg' : ''}">${s.avgLift > 0 ? '+' : ''}${s.avgLift}</td><td>${s.runs}</td><td>${s.samples}</td></tr>`).join('')}</tbody></table>
          <p class="bt-insight-h">${c.strongest ? c.strongest + ' ' : ''}${c.hypothesis}</p>`;
      } else if (c.id === 'threshold') {
        body = `<table class="bt-table"><thead><tr><th>Threshold</th><th>Avg expectancy</th><th>Avg win %</th><th>Avg Sharpe</th><th>~Trades</th></tr></thead><tbody>${
          c.sweep.map(s => `<tr><td>${s.threshold}</td><td class="${s.avgExpectancy > 0 ? 'pos' : 'neg'}">${s.avgExpectancy}</td><td>${s.avgWinRate}%</td><td>${s.avgSharpe}</td><td>${s.avgTrades}</td></tr>`).join('')}</tbody></table>
          <p class="bt-insight-h">${c.hypothesis}</p>`;
      } else if (c.id === 'top_combos') {
        body = Object.entries(c.perPair).map(([pair, arr]) => `<div class="bt-insight-pair"><b>${pair}</b>: ${arr.map(x => `${x.strategy}/${x.timeframe} (Sharpe ${x.sharpe != null ? (+x.sharpe).toFixed(2) : '—'}, ${x.nTrades} trades${x.bestRegime ? `, best in ${x.bestRegime}` : ''})`).join(' · ')}</div>`).join('');
      } else if (c.id === 'tf_consistency') {
        body = Object.entries(c.perPair).map(([pair, arr]) => `<div class="bt-insight-pair"><b>${pair}</b>: ${arr.map(x => `${x.timeframe} (med Sharpe ${x.medianSharpe}, ${x.n})`).join(' · ')}</div>`).join('');
      }
      return `<div class="bt-insight"><h3>${c.title}</h3><div class="bt-dim" style="margin:2px 0 8px">${c.note}</div>${body}</div>`;
    }).join('');
    $('insightsBody').innerHTML = framing + html;
  }
  function populateFilterOptions() {
    const pairs = [...new Set(currentRows.map(r => r.instrument))].sort();
    const tfs = [...new Set(currentRows.map(r => r.timeframe))];
    const fams = [...new Set(currentRows.map(r => r.strategy_family))].sort();
    syncSelect($('fPair'), pairs, 'All pairs'); syncSelect($('fTf'), tfs, 'All timeframes'); syncSelect($('fFamily'), fams, 'All families');
    syncSelect($('mPair'), pairs, ''); syncSelect($('mTf'), tfs, '');
  }
  function syncSelect(sel, vals, allLabel) {
    const cur = sel.value;
    const opts = (allLabel !== undefined ? [`<option value="">${allLabel}</option>`] : []).concat(vals.map(v => `<option value="${v}">${v}</option>`));
    if (sel.children.length !== opts.length) sel.innerHTML = opts.join('');
    if ([...sel.options].some(o => o.value === cur)) sel.value = cur;
  }

  // ── Regime × strategy matrix ────────────────────────────────────────────────
  const REGIMES = ['up/high','up/normal','up/low','down/high','down/normal','down/low','ranging/high','ranging/normal','ranging/low'];
  function renderMatrix() {
    const pair = $('mPair').value, tf = $('mTf').value;
    const rows = currentRows.filter(r => r.instrument === pair && r.timeframe === tf);
    if (!rows.length) { $('matrixBody').innerHTML = `<tr><td class="bt-empty">Pick a pair + timeframe with results.</td></tr>`; return; }
    const present = REGIMES.filter(rg => rows.some(r => r.regime_breakdown && r.regime_breakdown[rg]));
    const head = `<tr><th>Strategy</th>${present.map(rg => `<th>${rg}</th>`).join('')}</tr>`;
    const body = rows.map(r => {
      const cells = present.map(rg => {
        const c = r.regime_breakdown && r.regime_breakdown[rg];
        if (!c) return `<td class="mc empty">·</td>`;
        const hue = c.winRate >= 55 ? 'good' : c.winRate <= 45 ? 'bad' : 'mid';
        return `<td class="mc ${hue}" title="${c.n} trades, avg ${c.avgPnl}%">${c.winRate}%<small>${c.n}</small></td>`;
      }).join('');
      return `<tr><td class="ms">${r.strategy}</td>${cells}</tr>`;
    }).join('');
    $('matrixBody').innerHTML = head + body;
  }

  // ── Trade drill-down: re-run the single strategy to list its trades ──────────
  async function showTrades(key) {
    const [sym, tf, stratId] = key.split('|');
    $('tradesTitle').textContent = `${sym} · ${tf} · ${stratId}`;
    $('tradesBody').innerHTML = '<div class="bt-loading">Re-running strategy…</div>';
    $('tradesModal').style.display = '';
    try {
      const type = TYPE_OF[sym];
      const bars = await DS.getCandles(sym, type, tf);
      const weekly = (tf === '1d') ? await DS.getWeekly(sym, type).catch(() => null) : null;
      const ctx = A.strategies.buildContext(bars, { sym, assetClass: type, timeframe: tf, weekly });
      const st = A.strategies.buildStrategies(ctx).find(s => s.id === stratId);
      if (!st) { $('tradesBody').innerHTML = '<div class="bt-empty">Strategy not found.</div>'; return; }
      const { trades } = A.strategies.simulate(bars, st.strat, ctx);
      if (!trades.length) { $('tradesBody').innerHTML = '<div class="bt-empty">No trades.</div>'; return; }
      const fmtT = (idx) => new Date(bars[idx].time * 1000).toISOString().slice(0, 10);
      $('tradesBody').innerHTML = `<div class="bt-dim" style="margin-bottom:8px">${trades.length} trades · net of modelled spread</div>
        <table class="bt-table"><thead><tr><th>#</th><th>Entry</th><th>Exit</th><th>Dir</th><th>Entry px</th><th>Exit px</th><th>PnL %</th><th>Bars</th><th>Reason</th><th>Regime</th></tr></thead>
        <tbody>${trades.map((t, i) => `<tr><td>${i + 1}</td><td>${fmtT(t.entryIdx)}</td><td>${fmtT(t.exitIdx)}</td>
          <td>${t.dir > 0 ? 'L' : 'S'}</td><td>${fmt(t.entryPrice, 5)}</td><td>${fmt(t.exitPrice, 5)}</td>
          <td class="${t.pnlPct > 0 ? 'pos' : 'neg'}">${fmt(t.pnlPct, 2)}</td><td>${t.barsHeld}</td><td>${t.exitReason}</td>
          <td>${t.regimeAtEntry ? t.regimeAtEntry.trend + '/' + t.regimeAtEntry.vol : '—'}</td></tr>`).join('')}</tbody></table>`;
    } catch (err) {
      $('tradesBody').innerHTML = `<div class="bt-empty">Error: ${err.message}</div>`;
    }
  }

  // ── Load stored results ─────────────────────────────────────────────────────
  async function refreshRunFilter() {
    try {
      const r = await fetch('/api/backtest-runs?runs=true');
      const runs = r.ok ? await r.json() : [];
      const sel = $('fRun');
      sel.innerHTML = `<option value="">Latest stored</option>` + runs.slice(0, 30).map(x => `<option value="${x.run_id}">${x.run_id} · ${(x.inserted_at || '').slice(0, 16).replace('T', ' ')}</option>`).join('');
    } catch {}
  }
  async function loadStored(runId) {
    $('resultMeta').textContent = 'Loading…';
    try {
      const q = runId ? `?run_id=${encodeURIComponent(runId)}&limit=2000` : '?limit=2000';
      const r = await fetch('/api/backtest-runs' + q);
      const rows = r.ok ? await r.json() : [];
      currentRows = Array.isArray(rows) ? rows : [];
      renderResults();
      if (!currentRows.length) flashMeta('No stored results yet — run a backtest above.');
    } catch (e) { flashMeta('Could not load stored results: ' + e.message); }
  }

  // ── Wire up ─────────────────────────────────────────────────────────────────
  function init() {
    renderCatChips(); renderSymGrid(); renderTfChips(); updateJobCount();
    $('runBtn').onclick = startRun;
    $('pauseBtn').onclick = () => { if (!running) return; paused = !paused; setRunState('running'); };
    $('cancelBtn').onclick = () => { cancelled = true; paused = false; };
    ['fRun','fPair','fTf','fFamily','fSort'].forEach(id => $(id).onchange = () => { if (id === 'fRun') loadStored($('fRun').value); else renderResults(); });
    $('fMinSample').onchange = renderResults;
    $('refreshBtn').onclick = () => loadStored($('fRun').value);
    $('mPair').onchange = renderMatrix; $('mTf').onchange = renderMatrix;
    $('tradesClose').onclick = () => { $('tradesModal').style.display = 'none'; };
    $('tradesModal').onclick = (e) => { if (e.target === $('tradesModal')) $('tradesModal').style.display = 'none'; };
    refreshRunFilter();
    loadStored('');   // show any previously stored results
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
