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
  const LIBV = 'b6';   // bump when public/lib/*.js change — busts the worker's importScripts cache

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
    worker = new Worker('./backtest.worker.js?b=' + LIBV);
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

  // ── Multiple-testing reality check (C1: False Strategy Theorem) ──────────────
  // Inverse standard-normal CDF (Acklam) + expected MAXIMUM Sharpe across N
  // skill-less trials (Bailey & Lopez de Prado). A "best" Sharpe below this noise
  // floor is exactly what trying many strategies produces by chance.
  function invNormCDF(p) {
    if (p <= 0) return -Infinity; if (p >= 1) return Infinity;
    const a = [-3.969683028665376e+01,2.209460984245205e+02,-2.759285104469687e+02,1.383577518672690e+02,-3.066479806614716e+01,2.506628277459239e+00];
    const b = [-5.447609879822406e+01,1.615858368580409e+02,-1.556989798598866e+02,6.680131188771972e+01,-1.328068155288572e+01];
    const c = [-7.784894002430293e-03,-3.223964580411365e-01,-2.400758277161838e+00,-2.549732539343734e+00,4.374664141464968e+00,2.938163982698783e+00];
    const d = [7.784695709041462e-03,3.224671290700398e-01,2.445134137142996e+00,3.754408661907416e+00];
    const pl = 0.02425, ph = 1 - pl; let q, r;
    if (p < pl) { q = Math.sqrt(-2*Math.log(p)); return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1); }
    if (p <= ph) { q = p-0.5; r = q*q; return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q/(((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1); }
    q = Math.sqrt(-2*Math.log(1-p)); return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1);
  }
  function expectedMaxSharpe(N, sigma) {
    if (!(N > 1) || !(sigma > 0)) return 0;
    const g = 0.5772156649;
    return sigma * ((1 - g) * invNormCDF(1 - 1/N) + g * invNormCDF(1 - 1/(N*Math.E)));
  }
  function multipleTestingPanel(allRows) {
    if (!allRows || !allRows.length) return '';
    const latest = allRows[0].run_id;
    const elig = allRows.filter(r => r.run_id === latest && r.n_trades >= 30 && r.sharpe != null);
    if (elig.length < 3) return '';
    const sharpes = elig.map(r => r.sharpe);
    const N = elig.length, mean = sharpes.reduce((s, v) => s + v, 0) / N;
    const sigma = Math.sqrt(sharpes.reduce((s, v) => s + (v - mean) ** 2, 0) / (N - 1));
    const best = Math.max(...sharpes);
    const expMax = expectedMaxSharpe(N, sigma);
    const deflated = +(best - expMax).toFixed(2);
    const survives = deflated > 0, cls = survives ? 'pos' : 'neg';
    const robust = elig.filter(r => r.n_trades >= 100).length;
    return `<div class="bt-mt ${cls}">
      <div class="bt-mt-title">⚖ Multiple-testing reality check — False Strategy Theorem</div>
      <div class="bt-mt-grid">
        <div><span class="bt-mt-v">${N}</span><span class="bt-mt-l">strategies tried</span></div>
        <div><span class="bt-mt-v">${best.toFixed(2)}</span><span class="bt-mt-l">best Sharpe (in-sample)</span></div>
        <div><span class="bt-mt-v">${expMax.toFixed(2)}</span><span class="bt-mt-l">noise floor (best by chance)</span></div>
        <div><span class="bt-mt-v ${cls}">${deflated > 0 ? '+' : ''}${deflated}</span><span class="bt-mt-l">deflated edge</span></div>
      </div>
      <div class="bt-mt-note ${cls}">${survives
        ? `The best strategy beats the noise floor by ${deflated} — a weak positive signal worth out-of-sample testing. Still in-sample only; not a validated edge.`
        : `The best Sharpe (${best.toFixed(2)}) does NOT exceed what trying ${N} strategies yields by chance (~${expMax.toFixed(2)}). On this evidence there is NO demonstrated edge — treat these results as noise, not a strategy to trade.`}</div>
      ${robust < N ? `<div class="bt-mt-sample">Only ${robust}/${N} results have ≥100 trades (the reliable minimum) — the rest are small-sample/exploratory.</div>` : ''}
    </div>`;
  }

  // ── Improvement hypotheses (Layer 5) ────────────────────────────────────────
  function renderInsights() {
    const mt = multipleTestingPanel(currentRows);
    let framing = '', html = '';
    if (A.hypotheses) {
      const { meta, cards } = A.hypotheses.buildHypotheses(currentRows);
      if (cards.length) { renderHypothesisCards(meta, cards); framing = _bt_framing; html = _bt_html; }
    }
    if (!mt && !html) { $('insightsCard').style.display = 'none'; return; }
    $('insightsCard').style.display = '';
    $('insightsBody').innerHTML = (mt || '') + framing + html;
  }
  let _bt_framing = '', _bt_html = '';
  function renderHypothesisCards(meta, cards) {
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
    _bt_framing = framing; _bt_html = html;
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

  // ── Robustness panels: Monte Carlo drawdown bands + random-entry control ─────
  function renderRobustnessPanels(mc, rnd) {
    let html = '';
    if (mc) {
      html += `<div class="bt-rb">
        <div class="bt-rb-title">🎲 Drawdown stress test — ${mc.runs} bootstrap resamples of the ${mc.n} trades</div>
        <div class="bt-rb-grid">
          <div><span class="bt-rb-v">${mc.ddObserved}%</span><span class="bt-rb-l">observed trade-seq DD</span></div>
          <div><span class="bt-rb-v">${mc.ddMedian}%</span><span class="bt-rb-l">median resampled DD</span></div>
          <div><span class="bt-rb-v neg">${mc.ddP95}%</span><span class="bt-rb-l">95th-pctile DD (bad path)</span></div>
          <div><span class="bt-rb-v neg">${mc.ddWorst}%</span><span class="bt-rb-l">worst of ${mc.runs}</span></div>
          <div><span class="bt-rb-v ${mc.posRate >= 50 ? 'pos' : 'neg'}">${mc.posRate}%</span><span class="bt-rb-l">resamples profitable</span></div>
        </div>
        <div class="bt-rb-note">Resampling completed-trade P&amp;Ls (not the headline bar-level DD). Return range: ${mc.retP5}% (5th) · ${mc.retMedian}% (median) · ${mc.retP95}% (95th). Your single backtest is ONE ordering — size for the bad ones.${mc.posRate < 60 ? ' A profitable rate well under 100% means much of the headline return is luck of ordering/composition.' : ''}</div>
      </div>`;
    }
    if (rnd) {
      const cls = rnd.percentile >= 95 ? 'pos' : rnd.percentile >= 80 ? 'mid' : 'neg';
      html += `<div class="bt-rb ${cls}">
        <div class="bt-rb-title">🪙 vs random entries — ${rnd.runs} coin-flip clones (same frequency, hold ${rnd.holdBars} bars)</div>
        <div class="bt-rb-grid">
          <div><span class="bt-rb-v">${rnd.realReturn}%</span><span class="bt-rb-l">this strategy's return</span></div>
          <div><span class="bt-rb-v">${rnd.randMedian}%</span><span class="bt-rb-l">median random clone</span></div>
          <div><span class="bt-rb-v">${rnd.randBest}%</span><span class="bt-rb-l">best random clone</span></div>
          <div><span class="bt-rb-v ${cls}">${rnd.percentile}%</span><span class="bt-rb-l">random clones beaten</span></div>
        </div>
        <div class="bt-rb-note ${cls}">${rnd.percentile >= 95
          ? `Beats ${rnd.percentile}% of same-frequency random-entry clones — the ENTRY SIGNAL (not just the stop/exit) is adding value. Still in-sample, not a validated edge.`
          : rnd.percentile >= 80
            ? `Beats ${rnd.percentile}% of random clones — suggestive but not conclusive; the entry signal may be only marginally better than chance.`
            : `Beats only ${rnd.percentile}% of random-entry clones with the same trade count and holding period. On this data the entry signal is NO better than coin-flips — the return comes from drift/exit mechanics, not the edge.`}</div>
      </div>`;
    }
    return html;
  }

  // ── Trade drill-down: re-run the single strategy to list its trades ──────────
  async function showTrades(key) {
    const [sym, tf, stratId] = key.split('|');
    $('tradesTitle').textContent = `${sym} · ${tf} · ${stratId}`;
    $('tradesBody').innerHTML = '<div class="bt-loading">Re-running strategy + robustness tests…</div>';
    $('tradesModal').style.display = '';
    try {
      const type = TYPE_OF[sym];
      const bars = await DS.getCandles(sym, type, tf);
      const weekly = (tf === '1d') ? await DS.getWeekly(sym, type).catch(() => null) : null;
      const ctx = A.strategies.buildContext(bars, { sym, assetClass: type, timeframe: tf, weekly });
      const st = A.strategies.buildStrategies(ctx).find(s => s.id === stratId);
      if (!st) { $('tradesBody').innerHTML = '<div class="bt-empty">Strategy not found.</div>'; return; }
      const sim = A.strategies.simulate(bars, st.strat, ctx);
      const trades = sim.trades;
      if (!trades.length) { $('tradesBody').innerHTML = '<div class="bt-empty">No trades.</div>'; return; }

      // Robustness: Monte Carlo drawdown bands (cheap) + random-entry control
      // (re-simulates; cap runs on big bar counts so the modal stays responsive).
      const m = A.metrics.computeMetrics(sim, { assetClass: type, timeframe: tf });
      const mc = A.metrics.monteCarloDrawdown(trades, { runs: 2000 });
      const rnd = A.strategies.randomEntryTest(bars, ctx, trades, m.totalReturn, { runs: bars.length > 2000 ? 100 : 200 });
      const robust = renderRobustnessPanels(mc, rnd);

      const fmtT = (idx) => new Date(bars[idx].time * 1000).toISOString().slice(0, 10);
      $('tradesBody').innerHTML = robust + `<div class="bt-dim" style="margin-bottom:8px">${trades.length} trades · net of modelled spread</div>
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
