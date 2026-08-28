/* The Race — 3-Way Championship Race: Book A (certified 252) vs Book B (252+spill50) vs Book C (Champion Multi-Horizon [63,126,252]). */
(function () {
  'use strict';

  const SEED = 100000;
  const DAYS_TARGET = 60;
  const SUPA_URL = 'https://cuvchjhaojhmxfgczndy.supabase.co';
  const SUPA_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImN1dmNoamhhb2pobXhmZ2N6bmR5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ4ODYwNzYsImV4cCI6MjEwMDQ2MjA3Nn0.liH06gqou8QD0ifOLbNDohZjP5dsEk_RzH1WaXf1wtM';

  const $ = (id) => document.getElementById(id);
  const fmtMoney = (v) => '£' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmtSigned = (v) => (v >= 0 ? '+' : '−') + '£' + Math.abs(Number(v)).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmtPct = (v) => (v * 100).toFixed(2) + '%';

  async function fetchDaily(book) {
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

  function calcTradePnl(inst, entry, currentPx, units, isLong, gbpusd = (_gbpUsd || 1.285)) {
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
    return pnlUsd / (gbpusd || 1.285);
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
    const realizedBanked = Number(last.cum_pnl) || 0;
    const liveEquity = Number(last.equity) + liveOpenPnl;
    const liveCum = (liveEquity - initialSeed);

    // `drawdown_from_peak` is stored as a positive loss fraction (for example,
    // 0.0648 means a 6.48% drawdown).  The largest observed value is therefore
    // the maximum drawdown; taking Math.min incorrectly reports 0 whenever the
    // series includes an at-peak observation.
    const maxDD = Math.max(...rows.map(r => Number(r.drawdown_from_peak) || 0));
    return {
      equity: liveEquity,
      cum: liveCum,
      curDD: Number(last.drawdown_from_peak) || 0,
      maxDD,
      days: Math.max(1, rows.length),
      open: positions.length,
      updated: last.inserted_at || new Date().toISOString(),
    };
  }

  function renderHero(a, b, c) {
    if ($('raceEquityA')) $('raceEquityA').textContent = a ? fmtMoney(a.equity) : '—';
    if ($('raceEquityB')) $('raceEquityB').textContent = b ? fmtMoney(b.equity) : '—';
    if ($('raceEquityC')) $('raceEquityC').textContent = c ? fmtMoney(c.equity) : '—';

    if ($('raceSubA')) $('raceSubA').textContent = a ? `Net Return: ${fmtSigned(a.cum)}` : '—';
    if ($('raceSubB')) $('raceSubB').textContent = b ? `Net Return: ${fmtSigned(b.cum)}` : '—';
    if ($('raceSubC')) $('raceSubC').textContent = c ? `Net Return: ${fmtSigned(c.cum)}` : '—';

    const el = $('raceLeader');
    if (!el) return;

    const books = [
      { name: 'Book A (Certified)', eq: a ? a.equity : 0, color: '#2FD6A3' },
      { name: 'Book B (spill50)', eq: b ? b.equity : 0, color: '#D8B36A' },
      { name: 'Book C (Champion Ensemble)', eq: c ? c.equity : 0, color: '#38BDF8' }
    ];

    books.sort((x, y) => y.eq - x.eq);
    const leader = books[0];
    const runnerUp = books[1];
    const leadAmount = leader.eq - runnerUp.eq;

    if (leadAmount < 1) {
      el.textContent = `Dead heat between top engines — ${fmtMoney(leader.eq)} live.`;
      el.style.color = '#F8FAFC';
    } else {
      el.textContent = `👑 ${leader.name} leads the championship by ${fmtMoney(leadAmount)}!`;
      el.style.color = leader.color;
    }
  }

  let _chartInstance = null;
  function renderChart(rowsA, rowsB, rowsC, liveA = null, liveB = null, liveC = null) {
    const el = $('raceChart');
    if (!el || typeof LightweightCharts === 'undefined') return;

    if (_chartInstance) {
      try { _chartInstance.remove(); } catch (e) {}
      _chartInstance = null;
    }

    const a = rebase(rowsA, liveA).pts;
    const b = rebase(rowsB, liveB).pts;
    const c = rebase(rowsC, liveC).pts;

    if (!a.length && !b.length && !c.length) {
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
      localization: { priceFormatter: v => fmtMoney(v) },
    });

    const sA = chart.addLineSeries({ color: '#2FD6A3', lineWidth: 2, title: 'Book A' });
    const sB = chart.addLineSeries({ color: '#D8B36A', lineWidth: 2, title: 'Book B' });
    const sC = chart.addLineSeries({ color: '#38BDF8', lineWidth: 2, title: 'Book C' });

    if (a.length) sA.setData(a);
    if (b.length) sB.setData(b);
    if (c.length) sC.setData(c);

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

  function renderTable(a, b, c) {
    const rows = [
      ['Live Equity', a?.equity, b?.equity, c?.equity, 'money'],
      ['Cumulative P&L', a?.cum, b?.cum, c?.cum, 'signed'],
      ['Current Drawdown', a?.curDD, b?.curDD, c?.curDD, 'pct'],
      ['Max Drawdown', a?.maxDD, b?.maxDD, c?.maxDD, 'pct'],
      ['Open Positions', a?.open, b?.open, c?.open, 'int'],
      ['Days in Proof', a?.days, b?.days, c?.days, 'int'],
    ];

    const fmt = (v, kind) => {
      if (v === null || v === undefined) return '—';
      if (kind === 'money') return fmtMoney(v);
      if (kind === 'signed') return fmtSigned(v);
      if (kind === 'pct') return fmtPct(v);
      return String(v);
    };

    const determineLeader = (va, vb, vc, kind) => {
      if (va === null || vb === null || vc === null) return '—';
      if (kind === 'money' || kind === 'signed') {
        const max = Math.max(va, vb, vc);
        if (max === va) return '<span style="color:#2FD6A3; font-weight:700;">Book A</span>';
        if (max === vb) return '<span style="color:#D8B36A; font-weight:700;">Book B</span>';
        return '<span style="color:#38BDF8; font-weight:700;">Book C</span>';
      }
      if (kind === 'pct') {
        const min = Math.min(va, vb, vc);
        if (min === va) return '<span style="color:#2FD6A3; font-weight:700;">Book A</span>';
        if (min === vb) return '<span style="color:#D8B36A; font-weight:700;">Book B</span>';
        return '<span style="color:#38BDF8; font-weight:700;">Book C</span>';
      }
      return '—';
    };

    $('raceTableBody').innerHTML = rows.map(([label, va, vb, vc, kind]) => {
      const leader = determineLeader(va, vb, vc, kind);
      return `<tr><td>${label}</td><td>${fmt(va, kind)}</td><td>${fmt(vb, kind)}</td><td>${fmt(vc, kind)}</td><td>${leader}</td></tr>`;
    }).join('');
  }

  function renderDays(a, b, c) {
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
  }

  async function load() {
    try {
      const [rowsA, rowsB, rowsC, posA, posB, posC] = await Promise.all([
        fetchDaily('a'), fetchDaily('b'), fetchDaily('c'),
        fetchPositions('a'), fetchPositions('b'), fetchPositions('c')
      ]);

      // Collect all instruments from all 3 books to fetch live marks
      const instruments = new Set();
      for (const p of (posA || [])) if (p && p.instrument) instruments.add(p.instrument);
      for (const p of (posB || [])) if (p && p.instrument) instruments.add(p.instrument);
      for (const p of (posC || [])) if (p && p.instrument) instruments.add(p.instrument);

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

      // Compute live open PnL for Book A
      let livePnlA = 0;
      for (const p of (posA || [])) {
        const inst = String(p.instrument || '');
        const livePx = _liveMarks[inst] || parseFloat(p.last_px);
        const entry = parseFloat(p.entry_price);
        const units = parseFloat(p.units);
        const isLong = String(p.direction || '').toLowerCase() !== 'short';
        livePnlA += calcTradePnl(inst, entry, livePx, units, isLong, _gbpUsd);
      }

      // Compute live open PnL for Book B
      let livePnlB = 0;
      for (const p of (posB || [])) {
        const inst = String(p.instrument || '');
        const livePx = _liveMarks[inst] || parseFloat(p.last_px);
        const entry = parseFloat(p.entry_price);
        const units = parseFloat(p.units);
        const isLong = String(p.direction || '').toLowerCase() !== 'short';
        livePnlB += calcTradePnl(inst, entry, livePx, units, isLong, _gbpUsd);
      }

      // Compute live open PnL for Book C
      let livePnlC = 0;
      for (const p of (posC || [])) {
        const inst = String(p.instrument || '');
        const livePx = _liveMarks[inst] || parseFloat(p.last_px);
        const entry = parseFloat(p.entry_price);
        const units = parseFloat(p.units);
        const isLong = String(p.direction || '').toLowerCase() !== 'short';
        livePnlC += calcTradePnl(inst, entry, livePx, units, isLong, _gbpUsd);
      }

      const a = bookStats(rowsA, posA || [], livePnlA);
      const b = bookStats(rowsB, posB || [], livePnlB);
      const c = bookStats(rowsC, posC || [], livePnlC);

      renderHero(a, b, c);
      renderChart(rowsA, rowsB, rowsC, a.equity, b.equity, c.equity);
      renderTable(a, b, c);
      renderDays(a, b, c);

      const upd = (c && c.updated) || (b && b.updated) || (a && a.updated);
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
