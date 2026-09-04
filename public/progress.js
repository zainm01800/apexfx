// Research evidence feed only. Account progress lives in the Books workspace.
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function money(x) {
  const n = Number(x);
  if (!isFinite(n)) return '—';
  return '£' + Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
}
function signedMoney(x) {
  const n = Number(x);
  if (!isFinite(n)) return { txt: '—', cls: '' };
  return {
    txt: (n > 0 ? '+' : n < 0 ? '−' : '') + '£' + Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 }),
    cls: n > 0 ? 'pos' : n < 0 ? 'neg' : '',
  };
}
function fixed(x, dp) {
  const n = Number(x);
  return isFinite(n) ? n.toFixed(dp) : '—';
}
function titleize(kind) {
  return String(kind || '').replace(/\.json$/, '').replace(/_/g, ' ');
}

const BADGE = { pass: 'PASS', reject: 'REJECT', measurement: 'MEASUREMENT' };
function feedItem(e) {
  const nums = [];
  if (e.sharpe != null) nums.push(`<span><b>Sharpe</b> ${fixed(e.sharpe, 2)}</span>`);
  if (e.dsr != null) nums.push(`<span><b>DSR</b> ${fixed(e.dsr, 3)}</span>`);
  if (e.pbo != null) nums.push(`<span><b>PBO</b> ${fixed(e.pbo, 3)}</span>`);
  if (e.cpcvPaths != null && e.cpcvFracPositive != null) {
    nums.push(`<span><b>CPCV</b> ${Math.round(e.cpcvFracPositive * e.cpcvPaths)}/${e.cpcvPaths} paths +</span>`);
  }
  if (e.nTrials != null) nums.push(`<span><b>trials</b> ${e.nTrials}</span>`);
  return `<div class="pg-feed-item">
    <div class="pg-feed-date">${esc(e.date || '—')}</div>
    <div class="pg-feed-main">
      <div class="pg-feed-title">
        <span class="pg-badge ${esc(e.verdict)}">${BADGE[e.verdict] || esc(e.verdict)}</span>
        ${esc(titleize(e.kind))}
        ${e.book ? `<span class="pg-feed-book mono">${esc(e.book)}</span>` : ''}
      </div>
      ${nums.length ? `<div class="pg-feed-nums">${nums.join('')}</div>` : ''}
      ${e.takeaway ? `<div class="pg-feed-take">${esc(e.takeaway)}</div>` : ''}
    </div>
  </div>`;
}

function renderFeed(d) {
  const feedEl = document.getElementById('pgFeed');
  const metaEl = document.getElementById('feedMeta');
  const footEl = document.getElementById('feedFoot');

  const g = d.gates;
  if (!g || !g.entries.length) {
    feedEl.innerHTML = '<div class="pg-error">Gate record unavailable' + (d.gatesError ? ' — ' + esc(d.gatesError) : '') + '.</div>';
    return;
  }
  feedEl.innerHTML = g.entries.map(feedItem).join('');

  const bits = [];
  if (d.experiments) {
    bits.push('trial ledger n=' + d.experiments.total);
    const top = Object.entries(d.experiments.byKind || {}).sort((a, b) => b[1] - a[1]).slice(0, 3);
    if (top.length) bits.push('most-run: ' + top.map(([k, n]) => titleize(k) + ' ×' + n).join(', '));
  }
  metaEl.textContent = bits.join(' · ');

  footEl.textContent = 'Gate verdicts are deflated by the full trial ledger — every experiment counts against the bar (DSR > 0.95, PBO < 0.5, CPCV majority-positive paths). Newest '
    + (g.filesScanned || g.entries.length) + ' gate reports shown; rejections are progress too — each one closes a false lead.';
}

// ── Auto-research queue ──────────────────────────────────────────────────────
function renderQueue(d) {
  const el = document.getElementById('pgQueue');
  const items = Array.isArray(d.proposals) ? d.proposals : [];
  if (!items.length) return; // keep the empty state
  el.innerHTML = items.map((p) => `<div class="pg-queue-item">${esc(p.title || p.summary || JSON.stringify(p))}</div>`).join('');
}


async function init() {
  try {
    const response = await fetch('/api/progress');
    if (!response.ok) throw new Error('Research feed unavailable');
    const data = await response.json();
    renderFeed(data); renderQueue(data);
  } catch { document.getElementById('pgFeed').innerHTML = '<div class="pg-error">Research feed unavailable. The frozen study summary above is still accessible.</div>'; }
}
init();
