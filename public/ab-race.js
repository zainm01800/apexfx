/* The Race — live A/B forward test: Book A (certified 252) vs Book B (252+spill50).
   Headline comparisons are re-based to the shared window start (2026-08-10). */
(function () {
  'use strict';

  const SHARED_START = '2026-08-10';
  const SEED = 100000;
  const DAYS_TARGET = 60;
  const SUPA_URL = 'https://cuvchjhaojhmxfgczndy.supabase.co';
  const SUPA_ANON = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImN1dmNoamhhb2pobXhmZ2N6bmR5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ4ODYwNzYsImV4cCI6MjEwMDQ2MjA3Nn0.liH06gqou8QD0ifOLbNDohZjP5dsEk_RzH1WaXf1wtM';

  const $ = (id) => document.getElementById(id);
  const fmtMoney = (v) => '£' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmtSigned = (v) => (v >= 0 ? '+' : '−') + '£' + Math.abs(Number(v)).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const fmtPct = (v) => (v * 100).toFixed(2) + '%';

  async function fetchDaily(book) {
    const q = book === 'b' ? '?book=b&table=daily&limit=500' : '?table=daily&limit=500';
    try {
      const r = await fetch('/api/paper' + q);
      if (r.ok) { const j = await r.json(); if (Array.isArray(j) && j.length) return j; }
    } catch (e) { /* fall through to Supabase */ }
    const table = book === 'b' ? 'apex_paper_b_daily' : 'apex_paper_daily';
    const r2 = await fetch(`${SUPA_URL}/rest/v1/${table}?order=date.asc&limit=500`,
      { headers: { apikey: SUPA_ANON, Authorization: `Bearer ${SUPA_ANON}` } });
    return r2.ok ? await r2.json() : [];
  }

  async function fetchPositions(book) {
    const q = book === 'b' ? '?book=b&table=positions' : '?table=positions';
    try {
      const r = await fetch('/api/paper' + q);
      if (r.ok) { const j = await r.json(); if (Array.isArray(j)) return j; }
    } catch (e) { /* fall through */ }
    const table = book === 'b' ? 'apex_paper_b_positions' : 'apex_paper_positions';
    const r2 = await fetch(`${SUPA_URL}/rest/v1/${table}?select=instrument`,
      { headers: { apikey: SUPA_ANON, Authorization: `Bearer ${SUPA_ANON}` } });
    return r2.ok ? await r2.json() : [];
  }

  // Re-base an equity series to SEED on the shared start date (last row <= start).
  function rebase(rows) {
    const prior = rows.filter(r => r.date <= SHARED_START);
    if (!prior.length) return { base: null, pts: [] };
    const base = prior[prior.length - 1].equity;
    const pts = rows.filter(r => r.date >= SHARED_START)
      .map(r => ({ time: r.date, value: r.equity / base * SEED }));
    return { base, pts };
  }

  function bookStats(rows, positionsCount) {
    if (!rows.length) return null;
    const last = rows[rows.length - 1];
    const rb = rebase(rows);
    const rbLast = rb.pts.length ? rb.pts[rb.pts.length - 1].value : null;
    const maxDD = Math.min(...rows.map(r => Number(r.drawdown_from_peak) || 0));
    return {
      equity: last.equity,
      cum: last.cum_pnl,
      sinceShared: rbLast !== null ? rbLast - SEED : null,
      curDD: Number(last.drawdown_from_peak) || 0,
      maxDD,
      days: rows.length,
      open: positionsCount,
      updated: last.inserted_at,
    };
  }

  function renderHero(a, b) {
    $('raceEquityA').textContent = a ? fmtMoney(a.equity) : '—';
    $('raceEquityB').textContent = b ? fmtMoney(b.equity) : '—';
    $('raceSubA').textContent = a && a.sinceShared !== null ? `since 10 Aug: ${fmtSigned(a.sinceShared)}` : 'since 10 Aug: —';
    $('raceSubB').textContent = b && b.sinceShared !== null ? `since 10 Aug: ${fmtSigned(b.sinceShared)}` : 'since 10 Aug: —';
    const el = $('raceLeader');
    if (a && b && a.sinceShared !== null && b.sinceShared !== null) {
      const diff = b.sinceShared - a.sinceShared;
      const abs = fmtMoney(Math.abs(diff));
      if (Math.abs(diff) < 1) {
        el.textContent = `Dead heat on the shared window — ${abs} between them.`;
      } else {
        const leader = diff > 0 ? 'Book B (spill50)' : 'Book A (certified)';
        el.textContent = `${leader} leads by ${abs} on the shared window.`;
        el.style.color = diff > 0 ? '#D8B36A' : '#2FD6A3';
      }
    } else {
      el.textContent = 'Waiting for both books to share a window…';
    }
  }

  function renderChart(rowsA, rowsB) {
    const el = $('raceChart');
    if (typeof LightweightCharts === 'undefined') { el.textContent = 'chart library failed to load'; return; }
    const a = rebase(rowsA).pts, b = rebase(rowsB).pts;
    if (a.length < 2) { el.textContent = 'not enough shared-window data yet'; return; }
    const chart = LightweightCharts.createChart(el, {
      width: el.clientWidth, height: el.clientHeight || 340,
      layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#64748B',
                fontFamily: "'IBM Plex Mono', monospace", fontSize: 10 },
      grid: { vertLines: { color: 'rgba(51,65,85,0.35)' }, horzLines: { color: 'rgba(51,65,85,0.35)' } },
      rightPriceScale: { borderColor: 'rgba(51,65,85,0.6)' },
      timeScale: { borderColor: 'rgba(51,65,85,0.6)' },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      localization: { priceFormatter: v => fmtMoney(v) },
    });
    const sA = chart.addLineSeries({ color: '#2FD6A3', lineWidth: 2, title: 'A' });
    const sB = chart.addLineSeries({ color: '#D8B36A', lineWidth: 2, title: 'B' });
    sA.setData(a);
    if (b.length) sB.setData(b);
    sA.createPriceLine({ price: SEED, color: 'rgba(148,163,184,0.4)', lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'seed' });
    chart.timeScale().fitContent();
    window.addEventListener('resize', () => chart.applyOptions({ width: el.clientWidth }));
  }

  function renderTable(a, b) {
    const rows = [
      ['Equity', a?.equity, b?.equity, 'money'],
      ['Since 10 Aug (shared window)', a?.sinceShared, b?.sinceShared, 'signed'],
      ['Since seed (A: 16 Jul · B: 10 Aug)', a?.cum, b?.cum, 'signed'],
      ['Current drawdown', a?.curDD, b?.curDD, 'pct'],
      ['Max drawdown', a?.maxDD, b?.maxDD, 'pct'],
      ['Open positions', a?.open, b?.open, 'int'],
      ['Days in proof', a?.days, b?.days, 'int'],
    ];
    const fmt = (v, kind) => {
      if (v === null || v === undefined) return '—';
      if (kind === 'money') return fmtMoney(v);
      if (kind === 'signed') return fmtSigned(v);
      if (kind === 'pct') return fmtPct(v);
      return String(v);
    };
    const delta = (kind) => {
      if (!a || !b || a.sinceShared === null || b.sinceShared === null) return '—';
      return null;
    };
    $('raceTableBody').innerHTML = rows.map(([label, va, vb, kind]) => {
      let d = '—';
      if (va !== null && va !== undefined && vb !== null && vb !== undefined && kind !== 'int') {
        d = kind === 'pct' ? ((vb - va) * 100).toFixed(2) + 'pt' : fmtSigned(vb - va);
      }
      return `<tr><td>${label}</td><td>${fmt(va, kind)}</td><td>${fmt(vb, kind)}</td><td>${d}</td></tr>`;
    }).join('');
  }

  function renderDays(a, b) {
    if (a) {
      $('raceDayA').textContent = `${a.days} / ${DAYS_TARGET}`;
      $('raceBarA').style.width = Math.min(100, a.days / DAYS_TARGET * 100) + '%';
    }
    if (b) {
      $('raceDayB').textContent = `${b.days} / ${DAYS_TARGET}`;
      $('raceBarB').style.width = Math.min(100, b.days / DAYS_TARGET * 100) + '%';
    }
  }

  async function load() {
    try {
      const [rowsA, rowsB, posA, posB] = await Promise.all([
        fetchDaily('a'), fetchDaily('b'), fetchPositions('a'), fetchPositions('b'),
      ]);
      const a = bookStats(rowsA, posA.length);
      const b = bookStats(rowsB, posB.length);
      renderHero(a, b);
      renderChart(rowsA, rowsB);
      renderTable(a, b);
      renderDays(a, b);
      const upd = (b && b.updated) || (a && a.updated);
      if (upd) {
        $('raceLastSync').textContent = 'Last nightly step: ' +
          new Date(upd).toLocaleString('en-GB', { timeZone: 'Europe/London', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) + ' UK.';
      }
    } catch (e) {
      console.warn('race load failed', e);
    }
  }

  load();
  setInterval(() => { if (!document.hidden) load(); }, 15 * 60 * 1000);
})();
