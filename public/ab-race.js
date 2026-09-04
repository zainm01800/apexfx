/* The Race — 4-Way Championship Race: Book A (certified 252) vs Book B (252+spill50) vs Book C (Champion Multi-Horizon [63,126,252]) vs Book F (Prop Shield Elite). */
(function () {
  'use strict';

  const SEED = 100000;
  const DAYS_TARGET = 60;
  const SUPA_URL = 'https://cuvchjhaojhmxfgczndy.supabase.co';
  const SUPA_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImN1dmNoamhhb2pobXhmZ2N6bmR5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ4ODYwNzYsImV4cCI6MjEwMDQ2MjA3Nn0.liH06gqou8QD0ifOLbNDohZjP5dsEk_RzH1WaXf1wtM';

  const $ = (id) => document.getElementById(id);
  const fmtMoney = (v) => '£' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmtSigned = (v) => (v >= 0 ? '+' : '−') + '£' + Math.abs(Number(v)).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmtMoneyUSD = (v) => '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmtSignedUSD = (v) => (v >= 0 ? '+' : '−') + '$' + Math.abs(Number(v)).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmtPct = (v) => (v * 100).toFixed(2) + '%';

  async function fetchDaily(book) {
    if (book === 'f') {
      try {
        const r = await fetch('/api/paper?book=f&table=daily&limit=500');
        if (r.ok) { const j = await r.json(); if (Array.isArray(j) && j.length) return j; }
      } catch (e) {}
      try {
        const r = await fetch('/book-f-paper-snapshot.json');
        if (r.ok) {
          const j = await r.json();
          if (j && Array.isArray(j.daily) && j.daily.length) return j.daily;
        }
      } catch (e) {}
      return [];
    }
    if (book === 'r') {
      try {
        const r = await fetch('/api/paper?book=r&table=daily&limit=500');
        if (r.ok) { const j = await r.json(); if (Array.isArray(j) && j.length) return j; }
      } catch (e) {}
      try {
        const r2 = await fetch(`${SUPA_URL}/rest/v1/apex_analyses?id=eq.__apex_book_r_252_forward_paper_runtime__&select=feature_vector&limit=1`,
          { headers: { apikey: SUPA_ANON, Authorization: `Bearer ${SUPA_ANON}` } });
        if (r2.ok) {
          const j2 = await r2.json();
          const state = j2[0] && j2[0].feature_vector;
          if (state && Array.isArray(state.daily) && state.daily.length) return state.daily;
        }
      } catch (e) {}
      return [];
    }
    const q = `?book=${book}&table=daily&limit=500`;
    try {
      const r = await fetch('/api/paper' + q);
      if (r.ok) { const j = await r.json(); if (Array.isArray(j) && j.length) return j; }
    } catch (e) { /* fall through to Supabase */ }
    const table = book === 'c' ? 'apex_paper_c_daily' : (book === 'b' ? 'apex_paper_b_daily' : 'apex_paper_daily');
    try {
      const r2 = await fetch(`${SUPA_URL}/rest/v1/${table}?order=date.asc&limit=500`,
        { headers: { apikey: SUPA_ANON, Authorization: `Bearer ${SUPA_ANON}` } });
      return r2.ok ? await r2.json() : [];
    } catch (e) {
      return [];
    }
  }

  let _gbpUsd = 1.285;
  const _liveMarks = {};

  function paperClassFor(inst) {
    if (inst.includes('/')) {
      const base = inst.split('/')[0].toUpperCase();
      return ['BTC', 'ETH', 'SOL', 'XRP', 'ADA', 'DOGE', 'LINK', 'AVAX'].includes(base) ? 'crypto' : 'forex';
    }
    return 'stocks';
  }

  function calcTradePnl(inst, entry, currentPx, units, isLong, gbpusd = (_gbpUsd || 1.285), toGbp = true) {
    if (!entry || !currentPx || !units || entry <= 0 || currentPx <= 0) return 0;
    const rawDiff = (isLong ? (currentPx - entry) : (entry - currentPx)) * units;
    const cls = paperClassFor(inst);

    let pnlUsd = rawDiff;
    if (cls === 'forex' && inst.includes('/')) {
      if (inst.startsWith('USD/')) {
        pnlUsd = rawDiff / currentPx;
      } else if (inst.endsWith('/USD')) {
        pnlUsd = rawDiff;
      }
    }
    return toGbp ? (pnlUsd / (gbpusd || 1.285)) : pnlUsd;
  }

  async function fetchLiveMark(inst, cls) {
    const to = Math.floor(Date.now() / 1000);
    const from = to - 7 * 86400;
    const type = cls === 'forex' ? 'Forex' : (cls === 'crypto' ? 'Crypto' : 'Stock');
    const res = await fetch(`/api/candles?sym=${encodeURIComponent(inst)}&type=${encodeURIComponent(type)}&tf=1d&from=${from}&to=${to}`);
    if (!res.ok) return null;
    const bars = await res.json();
    if (!Array.isArray(bars) || !bars.length) return null;
    const px = parseFloat(bars[bars.length - 1].close);
    return Number.isFinite(px) ? px : null;
  }

  async function fetchPositions(book) {
    if (book === 'f') {
      try {
        const r = await fetch('/api/paper?book=f&table=positions');
        if (r.ok) { const j = await r.json(); if (Array.isArray(j)) return j; }
      } catch (e) {}
      try {
        const r = await fetch('/book-f-paper-snapshot.json');
        if (r.ok) {
          const j = await r.json();
          if (j && j.positions) {
            return Array.isArray(j.positions) ? j.positions : Object.values(j.positions);
          }
        }
      } catch (e) {}
      return [];
    }
    if (book === 'r') {
      try {
        const r = await fetch('/api/paper?book=r&table=positions');
        if (r.ok) { const j = await r.json(); if (Array.isArray(j)) return j; }
      } catch (e) {}
      try {
        const r2 = await fetch(`${SUPA_URL}/rest/v1/apex_analyses?id=eq.__apex_book_r_252_forward_paper_runtime__&select=feature_vector&limit=1`,
          { headers: { apikey: SUPA_ANON, Authorization: `Bearer ${SUPA_ANON}` } });
        if (r2.ok) {
          const j2 = await r2.json();
          const state = j2[0] && j2[0].feature_vector;
          if (state && state.positions) {
            return Array.isArray(state.positions) ? state.positions : Object.values(state.positions);
          }
        }
      } catch (e) {}
      return [];
    }
    const q = `?book=${book}&table=positions`;
    try {
      const r = await fetch('/api/paper' + q);
      if (r.ok) { const j = await r.json(); if (Array.isArray(j)) return j; }
    } catch (e) { /* fall through */ }
    const table = book === 'c' ? 'apex_paper_c_positions' : (book === 'b' ? 'apex_paper_b_positions' : 'apex_paper_positions');
    try {
      const r2 = await fetch(`${SUPA_URL}/rest/v1/${table}?select=*`,
        { headers: { apikey: SUPA_ANON, Authorization: `Bearer ${SUPA_ANON}` } });
      return r2.ok ? await r2.json() : [];
    } catch (e) {
      return [];
    }
  }

  // Re-base an equity series to SEED
  function rebase(rows, liveEquity = null) {
    if (!rows || !rows.length) return { base: null, pts: [] };
    const base = rows[0].equity || SEED;
    const pts = rows.map(r => ({ time: r.date, value: (r.equity / base) * SEED }));
    if (liveEquity !== null && pts.length) {
      const todayStr = new Date().toISOString().slice(0, 10);
      if (pts[pts.length - 1].time === todayStr) {
        pts[pts.length - 1].value = (liveEquity / base) * SEED;
      } else {
        pts.push({ time: todayStr, value: (liveEquity / base) * SEED });
      }
    }
    return { base, pts };
  }

  function bookStats(rows, positions, liveOpenPnl = 0, defaultSeed = SEED) {
    if (!rows || !rows.length) {
      return {
        equity: defaultSeed + liveOpenPnl,
        cum: liveOpenPnl,
        curDD: 0,
        maxDD: 0,
        days: 1,
        open: positions.length,
        updated: new Date().toISOString(),
      };
    }
    const last = rows[rows.length - 1];
    const initialSeed = rows[0]?.equity || defaultSeed;
    const liveEquity = Number(last.equity) + liveOpenPnl;
    const liveCum = (liveEquity - initialSeed);

    const maxDD = Math.max(...rows.map(r => Number(r.drawdown || r.drawdown_from_peak) || 0));
    return {
      equity: liveEquity,
      cum: liveCum,
      curDD: Number(last.drawdown || last.drawdown_from_peak) || 0,
      maxDD,
      days: Math.max(1, rows.length),
      open: positions.length,
      updated: last.inserted_at || new Date().toISOString(),
    };
  }

  function renderHero(a, b, c, r, f) {
    if ($('raceEquityA')) $('raceEquityA').textContent = a ? fmtMoney(a.equity) : '—';
    if ($('raceEquityB')) $('raceEquityB').textContent = b ? fmtMoney(b.equity) : '—';
    if ($('raceEquityC')) $('raceEquityC').textContent = c ? fmtMoney(c.equity) : '—';
    if ($('raceEquityR')) $('raceEquityR').textContent = r ? fmtMoneyUSD(r.equity) : '—';
    if ($('raceEquityF')) $('raceEquityF').textContent = f ? fmtMoneyUSD(f.equity) : '—';

    if ($('raceSubA')) $('raceSubA').textContent = a ? `Net Return: ${fmtSigned(a.cum)}` : '—';
    if ($('raceSubB')) $('raceSubB').textContent = b ? `Net Return: ${fmtSigned(b.cum)}` : '—';
    if ($('raceSubC')) $('raceSubC').textContent = c ? `Net Return: ${fmtSigned(c.cum)}` : '—';
    if ($('raceSubR')) $('raceSubR').textContent = r ? `Net Return: ${fmtSignedUSD(r.cum)}` : '—';
    if ($('raceSubF')) $('raceSubF').textContent = f ? `Net Return: ${fmtSignedUSD(f.cum)}` : '—';

    const el = $('raceLeader');
    if (!el) return;

    const books = [
      { name: 'Book A (Certified)', pct: a ? (a.equity / SEED - 1) : -999, eq: a ? a.equity : 0, color: '#2FD6A3' },
      { name: 'Book B (spill50)', pct: b ? (b.equity / SEED - 1) : -999, eq: b ? b.equity : 0, color: '#D8B36A' },
      { name: 'Book C (Champion Ensemble)', pct: c ? (c.equity / SEED - 1) : -999, eq: c ? c.equity : 0, color: '#38BDF8' },
      { name: 'Book R (USD ETF)', pct: r ? (r.equity / SEED - 1) : -999, eq: r ? r.equity : 0, color: '#FB923C' },
      { name: 'Book F (Prop Shield)', pct: f ? (f.equity / SEED - 1) : -999, eq: f ? f.equity : 0, color: '#A855F7' }
    ];

    books.sort((x, y) => y.pct - x.pct);
    const leader = books[0];
    const runnerUp = books[1];
    const leadPct = (leader.pct - runnerUp.pct) * 100;

    if (leadPct < 0.05) {
      el.textContent = `Dead heat between top models — leading return: ${fmtPct(leader.pct)}.`;
      el.style.color = '#F8FAFC';
    } else {
      el.textContent = `👑 ${leader.name} leads the championship (+${fmtPct(leader.pct)} return)!`;
      el.style.color = leader.color;
    }
  }

  let _chartInstance = null;
  function renderChart(rowsA, rowsB, rowsC, rowsR, rowsF, liveA = null, liveB = null, liveC = null, liveR = null, liveF = null) {
    const el = $('raceChart');
    if (!el || typeof LightweightCharts === 'undefined') return;

    if (_chartInstance) {
      try { _chartInstance.remove(); } catch (e) {}
      _chartInstance = null;
    }

    const a = rebase(rowsA, liveA).pts;
    const b = rebase(rowsB, liveB).pts;
    const c = rebase(rowsC, liveC).pts;
    const r = rebase(rowsR, liveR).pts;
    const f = rebase(rowsF, liveF).pts;

    if (!a.length && !b.length && !c.length && !r.length && !f.length) {
      el.textContent = 'Waiting for engine data…';
      return;
    }

    const chart = LightweightCharts.createChart(el, {
      width: el.clientWidth,
      height: el.clientHeight || 340,
      layout: {
        background: { type: 'solid', color: 'transparent' },
        textColor: '#64748B',
        fontFamily: "'Space Mono', monospace",
        fontSize: 10
      },
      grid: {
        vertLines: { color: 'rgba(51,65,85,0.35)' },
        horzLines: { color: 'rgba(51,65,85,0.35)' }
      },
      rightPriceScale: { borderColor: 'rgba(51,65,85,0.6)' },
      timeScale: { borderColor: 'rgba(51,65,85,0.6)' },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      localization: { priceFormatter: v => Number(v).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 }) },
    });

    const sA = chart.addLineSeries({ color: '#2FD6A3', lineWidth: 2, title: 'Book A' });
    const sB = chart.addLineSeries({ color: '#D8B36A', lineWidth: 2, title: 'Book B' });
    const sC = chart.addLineSeries({ color: '#38BDF8', lineWidth: 2, title: 'Book C' });
    const sR = chart.addLineSeries({ color: '#FB923C', lineWidth: 2, title: 'Book R' });
    const sF = chart.addLineSeries({ color: '#A855F7', lineWidth: 2, title: 'Book F' });

    if (a.length) sA.setData(a);
    if (b.length) sB.setData(b);
    if (c.length) sC.setData(c);
    if (r.length) sR.setData(r);
    if (f.length) sF.setData(f);

    sA.createPriceLine({
      price: SEED,
      color: 'rgba(148,163,184,0.4)',
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: 'seed'
    });

    chart.timeScale().fitContent();
    window.addEventListener('resize', () => chart.applyOptions({ width: el.clientWidth }));
    _chartInstance = chart;
  }

  function renderTable(a, b, c, r, f) {
    const rows = [
      ['Live Equity', a?.equity, b?.equity, c?.equity, r?.equity, f?.equity, 'money'],
      ['Cumulative P&L', a?.cum, b?.cum, c?.cum, r?.cum, f?.cum, 'signed'],
      ['Current Drawdown', a?.curDD, b?.curDD, c?.curDD, r?.curDD, f?.curDD, 'pct'],
      ['Max Drawdown', a?.maxDD, b?.maxDD, c?.maxDD, r?.maxDD, f?.maxDD, 'pct'],
      ['Open Positions', a?.open, b?.open, c?.open, r?.open, f?.open, 'int'],
      ['Days in Proof', a?.days, b?.days, c?.days, r?.days, f?.days, 'int'],
    ];

    const fmtCol = (v, kind, isUSD = false) => {
      if (v === null || v === undefined) return '—';
      if (kind === 'money') return isUSD ? fmtMoneyUSD(v) : fmtMoney(v);
      if (kind === 'signed') return isUSD ? fmtSignedUSD(v) : fmtSigned(v);
      if (kind === 'pct') return fmtPct(v);
      return String(v);
    };

    const determineLeader = (va, vb, vc, vr, vf, kind) => {
      if (va === null && vb === null && vc === null && vr === null && vf === null) return '—';
      if (kind === 'money' || kind === 'signed') {
        const ra = va !== null && va !== undefined ? va / SEED : -999;
        const rb = vb !== null && vb !== undefined ? vb / SEED : -999;
        const rc = vc !== null && vc !== undefined ? vc / SEED : -999;
        const rr = vr !== null && vr !== undefined ? vr / SEED : -999;
        const rf = vf !== null && vf !== undefined ? vf / SEED : -999;
        const max = Math.max(ra, rb, rc, rr, rf);
        if (max === ra) return '<span style="color:#2FD6A3; font-weight:700;">Book A</span>';
        if (max === rb) return '<span style="color:#D8B36A; font-weight:700;">Book B</span>';
        if (max === rc) return '<span style="color:#38BDF8; font-weight:700;">Book C</span>';
        if (max === rr) return '<span style="color:#FB923C; font-weight:700;">Book R</span>';
        return '<span style="color:#A855F7; font-weight:700;">Book F</span>';
      }
      if (kind === 'pct') {
        const list = [va, vb, vc, vr, vf].filter(x => x !== null && x !== undefined);
        if (!list.length) return '—';
        const min = Math.min(...list);
        if (min === va) return '<span style="color:#2FD6A3; font-weight:700;">Book A</span>';
        if (min === vb) return '<span style="color:#D8B36A; font-weight:700;">Book B</span>';
        if (min === vc) return '<span style="color:#38BDF8; font-weight:700;">Book C</span>';
        if (min === vr) return '<span style="color:#FB923C; font-weight:700;">Book R</span>';
        return '<span style="color:#A855F7; font-weight:700;">Book F</span>';
      }
      return '—';
    };

    $('raceTableBody').innerHTML = rows.map(([label, va, vb, vc, vr, vf, kind]) => {
      const leader = determineLeader(va, vb, vc, vr, vf, kind);
      return `<tr>
        <td>${label}</td>
        <td>${fmtCol(va, kind, false)}</td>
        <td>${fmtCol(vb, kind, false)}</td>
        <td>${fmtCol(vc, kind, false)}</td>
        <td>${fmtCol(vr, kind, true)}</td>
        <td>${fmtCol(vf, kind, true)}</td>
        <td>${leader}</td>
      </tr>`;
    }).join('');
  }

  function renderDays(a, b, c, r, f) {
    if (a && $('raceDayA')) {
      $('raceDayA').textContent = `${a.days} / ${DAYS_TARGET}`;
      if ($('raceBarA')) $('raceBarA').style.width = Math.min(100, (a.days / DAYS_TARGET) * 100) + '%';
    }
    if (b && $('raceDayB')) {
      $('raceDayB').textContent = `${b.days} / ${DAYS_TARGET}`;
      if ($('raceBarB')) $('raceBarB').style.width = Math.min(100, (b.days / DAYS_TARGET) * 100) + '%';
    }
    if (c && $('raceDayC')) {
      $('raceDayC').textContent = `${c.days} / ${DAYS_TARGET}`;
      if ($('raceBarC')) $('raceBarC').style.width = Math.min(100, (c.days / DAYS_TARGET) * 100) + '%';
    }
    if (r && $('raceDayR')) {
      $('raceDayR').textContent = `${r.days} / ${DAYS_TARGET}`;
      if ($('raceBarR')) $('raceBarR').style.width = Math.min(100, (r.days / DAYS_TARGET) * 100) + '%';
    }
    if (f && $('raceDayF')) {
      $('raceDayF').textContent = `${f.days} / ${DAYS_TARGET}`;
      if ($('raceBarF')) $('raceBarF').style.width = Math.min(100, (f.days / DAYS_TARGET) * 100) + '%';
    }
  }

  async function load() {
    try {
      const [rowsA, rowsB, rowsC, rowsR, rowsF, posA, posB, posC, posR, posF] = await Promise.all([
        fetchDaily('a'), fetchDaily('b'), fetchDaily('c'), fetchDaily('r'), fetchDaily('f'),
        fetchPositions('a'), fetchPositions('b'), fetchPositions('c'), fetchPositions('r'), fetchPositions('f')
      ]);

      // Collect all instruments from all 5 books to fetch live marks
      const instruments = new Set();
      for (const p of (posA || [])) if (p && p.instrument) instruments.add(p.instrument);
      for (const p of (posB || [])) if (p && p.instrument) instruments.add(p.instrument);
      for (const p of (posC || [])) if (p && p.instrument) instruments.add(p.instrument);
      for (const p of (posR || [])) if (p && p.instrument) instruments.add(p.instrument);
      for (const p of (posF || [])) if (p && p.instrument) instruments.add(p.instrument);

      const stale = [{ inst: 'GBP/USD', cls: 'forex' }];
      for (const inst of instruments) {
        stale.push({ inst, cls: paperClassFor(inst) });
      }

      await Promise.allSettled(stale.map(async ({ inst, cls }) => {
        try {
          const px = await fetchLiveMark(inst, cls);
          if (px !== null) {
            _liveMarks[inst] = px;
            if (inst === 'GBP/USD') _gbpUsd = px;
          }
        } catch (e) {}
      }));

      // Compute live open PnL for Book A (GBP)
      let livePnlA = 0;
      for (const p of (posA || [])) {
        const inst = String(p.instrument || '');
        const livePx = _liveMarks[inst] || parseFloat(p.last_px);
        const entry = parseFloat(p.entry_price);
        const units = parseFloat(p.units);
        const isLong = String(p.direction || '').toLowerCase() !== 'short';
        livePnlA += calcTradePnl(inst, entry, livePx, units, isLong, _gbpUsd, true);
      }

      // Compute live open PnL for Book B (GBP)
      let livePnlB = 0;
      for (const p of (posB || [])) {
        const inst = String(p.instrument || '');
        const livePx = _liveMarks[inst] || parseFloat(p.last_px);
        const entry = parseFloat(p.entry_price);
        const units = parseFloat(p.units);
        const isLong = String(p.direction || '').toLowerCase() !== 'short';
        livePnlB += calcTradePnl(inst, entry, livePx, units, isLong, _gbpUsd, true);
      }

      // Compute live open PnL for Book C (GBP)
      let livePnlC = 0;
      for (const p of (posC || [])) {
        const inst = String(p.instrument || '');
        const livePx = _liveMarks[inst] || parseFloat(p.last_px);
        const entry = parseFloat(p.entry_price);
        const units = parseFloat(p.units);
        const isLong = String(p.direction || '').toLowerCase() !== 'short';
        livePnlC += calcTradePnl(inst, entry, livePx, units, isLong, _gbpUsd, true);
      }

      // Compute live open PnL for Book R (USD)
      let livePnlR = 0;
      for (const p of (posR || [])) {
        const inst = String(p.instrument || '');
        const livePx = _liveMarks[inst] || parseFloat(p.last_px);
        const entry = parseFloat(p.entry_price);
        const units = parseFloat(p.units);
        const isLong = String(p.direction || '').toLowerCase() !== 'short';
        livePnlR += calcTradePnl(inst, entry, livePx, units, isLong, _gbpUsd, false);
      }

      // Compute live open PnL for Book F (USD)
      let livePnlF = 0;
      for (const p of (posF || [])) {
        const inst = String(p.instrument || '');
        const livePx = _liveMarks[inst] || parseFloat(p.last_px);
        const entry = parseFloat(p.entry_price);
        const units = parseFloat(p.units);
        const isLong = String(p.direction || '').toLowerCase() !== 'short';
        livePnlF += calcTradePnl(inst, entry, livePx, units, isLong, _gbpUsd, false);
      }

      const a = bookStats(rowsA, posA || [], livePnlA);
      const b = bookStats(rowsB, posB || [], livePnlB);
      const c = bookStats(rowsC, posC || [], livePnlC);
      const r = bookStats(rowsR, posR || [], livePnlR);
      const f = bookStats(rowsF, posF || [], livePnlF);

      renderHero(a, b, c, r, f);
      renderChart(rowsA, rowsB, rowsC, rowsR, rowsF, a.equity, b.equity, c.equity, r.equity, f.equity);
      renderTable(a, b, c, r, f);
      renderDays(a, b, c, r, f);

      const upd = (r && r.updated) || (f && f.updated) || (c && c.updated) || (b && b.updated) || (a && a.updated);
      if (upd && $('raceLastSync')) {
        $('raceLastSync').textContent = 'Last sync: ' +
          new Date().toLocaleString('en-GB', { timeZone: 'Europe/London', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' }) + ' UK · Live marks active.';
      }
    } catch (e) {
      console.warn('race load failed', e);
    }
  }

  load();
  setInterval(() => { if (!document.hidden) load(); }, 60 * 1000);
})();
