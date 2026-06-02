// APEX Quant Engine panel — consumes the Python service (read-only).
// API base: ?api=... query param > localStorage > host-based default
// (local engine during local dev; the hosted engine on the deployed site).

(() => {
  const params = new URLSearchParams(location.search);
  const _apiDefault = /^(localhost|127\.0\.0\.1)$/.test(location.hostname)
    ? 'http://127.0.0.1:8000'
    : 'https://apex-quant-engine.onrender.com';
  const API = (params.get('api') || localStorage.getItem('apexEngineApi') || _apiDefault).replace(/\/$/, '');
  if (params.get('api')) localStorage.setItem('apexEngineApi', params.get('api'));

  const FALLBACK_INSTRUMENTS = ['EUR/USD', 'GBP/USD', 'USD/JPY', 'USD/CHF', 'AUD/USD', 'USD/CAD', 'NZD/USD'];
  // panel strategy id -> validation-cache strategy name
  const VAL_NAME = { baseline: 'regime_gated_momentum', ml_gbm: 'ml_gbm', ml_linear: 'ml_gbm' };

  let _sym = null;
  let _method = 'rule_based';
  let _strategy = 'baseline';

  // ── helpers ──
  const $ = (id) => document.getElementById(id);
  const pct = (x, d = 1) => (x == null || isNaN(x) ? '—' : (x * 100).toFixed(d) + '%');
  const num = (x, d = 2) => (x == null || isNaN(x) ? '—' : Number(x).toFixed(d));
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  async function api(path) {
    const res = await fetch(`${API}${path}`, { headers: { Accept: 'application/json' } });
    if (!res.ok) {
      const err = new Error(`HTTP ${res.status}`);
      err.status = res.status;
      try { err.detail = (await res.json()).detail; } catch {}
      throw err;
    }
    return res.json();
  }

  // ── engine status / boot ──
  async function boot() {
    $('eoApi').textContent = API;
    try {
      const health = await api('/health');
      setEngine(true);
      const instruments = (health.instruments && health.instruments.length) ? health.instruments : FALLBACK_INSTRUMENTS;
      renderInstrumentBar(instruments);
      selectInstrument(instruments[0]);
      $('quantGrid').style.display = '';
    } catch (e) {
      setEngine(false);
      $('offlineBanner').style.display = 'flex';
      renderInstrumentBar(FALLBACK_INSTRUMENTS); // still show, disabled
    }
  }

  function setEngine(online) {
    $('engineDot').className = 'engine-dot ' + (online ? 'online' : 'offline');
    $('engineStatus').textContent = online ? 'ENGINE ONLINE' : 'ENGINE OFFLINE';
  }

  function renderInstrumentBar(list) {
    $('instrumentBar').innerHTML = list.map((s) =>
      `<button class="q-inst" data-sym="${esc(s)}">${esc(s)}</button>`).join('');
    document.querySelectorAll('.q-inst').forEach((b) =>
      b.addEventListener('click', () => selectInstrument(b.dataset.sym)));
  }

  function selectInstrument(sym) {
    _sym = sym;
    document.querySelectorAll('.q-inst').forEach((b) => b.classList.toggle('active', b.dataset.sym === sym));
    $('quantGrid').style.display = '';
    ['regimeBody', 'signalBody', 'riskBody', 'validationBody', 'researchBody'].forEach((id) => { $(id).innerHTML = '<div class="q-skel"></div>'; });
    loadRegime(); loadSignal(); loadRisk(); loadValidation(); loadResearch();
  }

  // ── regime ──
  async function loadRegime() {
    try {
      const r = await api(`/regime/${encodeURIComponent(_sym)}?method=${_method}`);
      $('regimeAsOf').textContent = r.as_of || '';
      const tb = { up: 'b-up', down: 'b-down', ranging: 'b-rng' }[r.trend] || 'b-rng';
      const vb = { high: 'b-hi', low: 'b-lo', normal: 'b-nm' }[r.vol] || 'b-nm';
      $('regimeBody').innerHTML = `
        <div class="regime-row">
          <span class="badge ${tb}">${esc((r.trend || '').toUpperCase())}</span>
          <span class="badge ${vb}">${esc((r.vol || '').toUpperCase())} VOL</span>
        </div>
        <div class="conf-row"><span>Confidence</span><span>${pct(r.confidence)}</span></div>
        <div class="conf-track"><div class="conf-fill" style="width:${(r.confidence * 100).toFixed(0)}%"></div></div>
        <div class="conf-row"><span>Aggression scalar</span><span>${num(r.aggression_scalar, 2)}</span></div>
        <div class="q-detail">${esc(r.detail || '')}</div>`;
    } catch (e) { $('regimeBody').innerHTML = errHtml(e); }
  }

  // ── signal ──
  async function loadSignal() {
    try {
      const s = await api(`/signal/${encodeURIComponent(_sym)}?strategy=${_strategy}`);
      const dirClass = { long: 'dir-long', short: 'dir-short', flat: 'dir-flat' }[s.direction] || 'dir-flat';
      let band = '';
      if (s.uncertainty) {
        const lo = s.uncertainty.lower * 100, hi = s.uncertainty.upper * 100, p = s.probability * 100;
        band = `
          <div class="prob-wrap">
            <div class="prob-label"><span>P(target before stop)</span><b>${p.toFixed(1)}%</b></div>
            <div class="band-track">
              <div class="band-mid"></div>
              <div class="band-range" style="left:${lo}%;width:${Math.max(1, hi - lo)}%"></div>
              <div class="band-point" style="left:${p}%"></div>
            </div>
            <div class="band-note">Calibrated estimate with conformal band ${lo.toFixed(0)}–${hi.toFixed(0)}% &middot; deliberately not a point "call"</div>
          </div>`;
      }
      const feats = s.contributing_features || {};
      const featRows = Object.entries(feats).map(([k, v]) =>
        `<div class="feat-row"><span class="fk">${esc(k)}</span><span class="fv">${v == null ? '—' : esc(v)}</span></div>`).join('');
      let sentiment = '';
      if (s.sentiment) {
        const sc = s.sentiment.score;
        const cls = sc > 0.05 ? 'pos' : sc < -0.05 ? 'neg' : 'neu';
        sentiment = `<div class="sig-reason">📰 sentiment <span class="${cls}">${sc >= 0 ? '+' : ''}${sc}</span> (${s.sentiment.n_articles} headlines) — ${esc(s.sentiment.effect)}</div>`;
      }
      $('signalBody').innerHTML = `
        <div class="sig-dir ${dirClass}">${esc((s.direction || 'flat').toUpperCase())}<span class="qc-tag" style="margin-left:10px">${esc(s.strategy || _strategy)}</span></div>
        ${band}
        ${s.reason ? `<div class="sig-reason">${esc(s.reason)}</div>` : ''}
        ${sentiment}
        <div class="feat-list">${featRows}</div>`;
    } catch (e) { $('signalBody').innerHTML = errHtml(e); }
  }

  // ── risk ──
  async function loadRisk() {
    try {
      const r = await api(`/risk/${encodeURIComponent(_sym)}?equity=100000&strategy=${_strategy}`);
      $('riskEquity').textContent = `equity $${Number(r.assumed_equity || 0).toLocaleString()}`;
      const chips = (r.constraints_applied || []).map((c) => `<span class="cons-chip">${esc(c)}</span>`).join('');
      if (r.permitted) {
        $('riskBody').innerHTML = `
          <div class="risk-stat"><span class="rk">Direction</span><span class="rv ${r.direction === 'long' ? 'pos' : 'neg'}">${esc((r.direction || '').toUpperCase())}</span></div>
          <div class="risk-stat"><span class="rk">Risk of equity</span><span class="rv">${pct(r.risk_fraction, 2)}</span></div>
          <div class="risk-stat"><span class="rk">Notional</span><span class="rv">$${Number(r.notional || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}</span></div>
          <div class="risk-stat"><span class="rk">Entry / Stop</span><span class="rv">${num(r.price, 5)} / ${num(r.stop_price, 5)}</span></div>
          <div class="risk-stat"><span class="rk">Target</span><span class="rv">${num(r.target_price, 5)}</span></div>
          <div class="constraints">${chips}</div>`;
      } else {
        $('riskBody').innerHTML = `
          <div class="risk-veto"><div class="rv-big">NO POSITION</div></div>
          <div class="constraints">${chips}</div>
          <div class="risk-rationale">${esc(r.rationale || 'Vetoed by the risk layer.')}</div>`;
      }
    } catch (e) { $('riskBody').innerHTML = errHtml(e); }
  }

  // ── validation ──
  async function loadValidation() {
    try {
      const v = await api(`/validation/${VAL_NAME[_strategy]}?instrument=${encodeURIComponent(_sym)}`);
      const verdict = v.verdict || {};
      const dsr = v.dsr || {}, pbo = v.pbo || {}, cpcv = v.cpcv || {};
      const pass = verdict.passed;
      const reasons = (verdict.reasons || []).map((r) => `<li>${esc(r)}</li>`).join('');
      $('validationBody').innerHTML = `
        <div class="val-verdict">
          <span class="verdict-badge ${pass ? 'vb-pass' : 'vb-fail'}">${pass ? 'PASSED' : 'REJECTED'}</span>
          <span class="q-detail">Strategy validated over ${v.n_trials || '?'} configs &middot; config v${v.config_version || '?'}</span>
        </div>
        <div class="val-metrics">
          <div class="val-metric"><div class="vm-label">Deflated Sharpe</div><div class="vm-value ${dsr.dsr > 0.95 ? 'pos' : 'neg'}">${num(dsr.dsr, 2)}</div><div class="vm-sub">need &gt; 0.95 &middot; obs SR ${num(dsr.observed_sharpe_ann, 2)}</div></div>
          <div class="val-metric"><div class="vm-label">PBO</div><div class="vm-value ${pbo.pbo != null && pbo.pbo < 0.5 ? 'pos' : 'neg'}">${pbo.pbo == null ? '—' : num(pbo.pbo, 2)}</div><div class="vm-sub">overfit prob &middot; need &lt; 0.5</div></div>
          <div class="val-metric"><div class="vm-label">CPCV OOS</div><div class="vm-value ${cpcv.frac_positive > 0.5 ? 'pos' : 'neg'}">${pct(cpcv.frac_positive, 0)}</div><div class="vm-sub">${cpcv.n_paths || 0} paths +ve &middot; med SR ${num(cpcv.oos_sharpe_median, 2)}</div></div>
        </div>
        <ul class="val-reasons">${reasons}</ul>`;
    } catch (e) {
      if (e.status === 404) {
        $('validationBody').innerHTML = `<div class="val-missing">No cached validation for <b>${esc(_sym)}</b> yet.<br/>Generate it offline:<br/><code>cd engine</code><br/><code>.venv\\Scripts\\python.exe scripts/run_validation.py ${esc(_sym)}</code></div>`;
      } else { $('validationBody').innerHTML = errHtml(e); }
    }
  }

  // ── AI research ──
  async function loadResearch() {
    try {
      const r = await api(`/research/${encodeURIComponent(_sym)}`);
      const results = r.results || [];
      const meta = `Generated for ${esc(r.generated_for || r.as_of || '')} · ${r.n_hypotheses || results.length} hypotheses · proposer: ${r.llm_used ? 'LLM debate' : 'programmatic'}`;
      const hypos = results.map((h) => {
        const v = h.validation;
        const vb = v == null ? '<span class="vbadge v-skip">VALIDATION SKIPPED</span>'
          : v.error ? '<span class="vbadge v-skip">VALIDATION ERROR</span>'
          : v.passed ? `<span class="vbadge v-pass">VALIDATED ✓</span>`
          : '<span class="vbadge v-fail">REJECTED</span>';
        const verdict = (h.debate && h.debate.verdict) || 'test';
        const dv = { test: 'v-test', refine: 'v-refine', discard: 'v-discard' }[verdict] || 'v-test';
        const valLine = v && !v.error
          ? `<div class="hypo-val">DSR ${v.dsr ?? '—'} · PBO ${v.pbo ?? '—'} · OOS ${v.frac_positive != null ? Math.round(v.frac_positive * 100) + '%' : '—'} of ${v.n_paths ?? '—'} paths +ve · obs SR ${v.observed_sharpe ?? '—'}</div>`
          : (v && v.error ? `<div class="hypo-val">${esc(v.error)}</div>` : '');
        const dbt = h.debate || {};
        const debate = dbt.llm_used ? `
          <div class="debate-row"><span class="dr-role dr-bull">Bull</span><span class="dr-text">${esc(dbt.bull || '')}</span></div>
          <div class="debate-row"><span class="dr-role dr-bear">Bear</span><span class="dr-text">${esc(dbt.bear || '')}</span></div>
          <div class="debate-row"><span class="dr-role dr-sup">Risk</span><span class="dr-text">${esc(dbt.supervisor || '')}</span></div>` : '';
        return `
          <div class="hypo">
            <div class="hypo-head">
              <span class="hypo-label">${esc(h.label || '')}</span>
              <span class="vbadge ${dv}">${esc(verdict.toUpperCase())}</span>
              ${vb}
              <span class="hypo-by">via ${esc(h.proposed_by || '')}</span>
            </div>
            <div class="hypo-thesis">${esc(h.thesis || '')}</div>
            ${debate}
            ${valLine}
          </div>`;
      }).join('');
      $('researchBody').innerHTML = `
        <div class="research-disclaimer">${esc(r.disclaimer || '')}</div>
        <div class="research-meta">${meta}</div>
        ${hypos || '<div class="val-missing">No hypotheses.</div>'}`;
    } catch (e) {
      if (e.status === 404) {
        $('researchBody').innerHTML = `<div class="val-missing">No AI research cached for <b>${esc(_sym)}</b> yet.<br/>Generate it (LLM optional — falls back to a programmatic proposer):<br/><code>cd engine</code><br/><code>.venv\\Scripts\\python.exe scripts/run_research.py ${esc(_sym)}</code></div>`;
      } else { $('researchBody').innerHTML = errHtml(e); }
    }
  }

  function errHtml(e) {
    return `<div class="val-missing">Couldn't load: ${esc(e.detail || e.message || 'error')}</div>`;
  }

  // ── regime method toggle (scoped to its own group) ──
  document.querySelectorAll('#regimeMethod .qm-btn').forEach((b) =>
    b.addEventListener('click', () => {
      document.querySelectorAll('#regimeMethod .qm-btn').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      _method = b.dataset.method;
      if (_sym) loadRegime();
    }));

  // ── strategy selector (signal/risk/validation depend on it) ──
  document.querySelectorAll('#strategySel .qm-btn').forEach((b) =>
    b.addEventListener('click', () => {
      document.querySelectorAll('#strategySel .qm-btn').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      _strategy = b.dataset.strategy;
      if (_sym) { loadSignal(); loadRisk(); loadValidation(); }
    }));

  boot();
})();
