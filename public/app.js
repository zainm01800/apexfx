// Extracted from inline <script> blocks in index.html

// ── OVERLAY PANEL + NAV GLUE SCRIPT ─────────────────────────────────────────
// Overlay panel controller
let _activeOverlay = null;
let _overlayLocked = false;

function toggleOverlay(name) {
  const panel = document.getElementById('overlay-panel');
  const titles = {watch:'Watchlist',alerts:'Price Alerts',scanner:'AI Scanner',draw:'Drawing Tools',inds:'Indicators'};

  // Locked + same panel clicked → unlock and close
  if (_overlayLocked && _activeOverlay === name) {
    _overlayLocked = false;
    closeOverlay();
    return;
  }

  // Unlocked + same panel clicked → close
  if (!_overlayLocked && _activeOverlay === name) {
    closeOverlay();
    return;
  }

  // Hide all sub-panels
  ['ov-watch','ov-alerts','ov-scanner','ov-draw','ov-inds'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });

  // Show target
  const target = document.getElementById('ov-' + name);
  if (target) target.style.display = name === 'inds' ? 'flex' : 'block';
  document.getElementById('ov-title').textContent = titles[name] || name;

  // If opening indicators panel, populate it
  if (name === 'draw') {
    // If draw panel is currently in the FPW, don't open the sidebar overlay
    if(_fpwMap['draw']){ _fpwFocus('draw'); return; }
    // Sync button states
    TOOL_BTNS.forEach(id => {
      const b = document.getElementById('dt-' + id);
      if(b) b.classList.toggle('on', id === activeTool);
    });
  }
  if (name === 'inds') {
    // If inds panel is in the FPW, focus that instead
    if(_fpwMap['inds']){ _fpwFocus('inds'); return; }
    const s = document.getElementById('ov-ind-search');
    if (s) s.value = '';
    ilCurrentTab = 'builtin';
    const tb = document.getElementById('iltab-builtin');
    const tc = document.getElementById('iltab-custom');
    if (tb) tb.classList.add('on');
    if (tc) tc.classList.remove('on');
    renderIndList('');
    setTimeout(() => { const s2 = document.getElementById('ov-ind-search'); if(s2) s2.focus(); }, 80);
  }

  panel.classList.add('open');
  if (_overlayLocked) {
    panel.classList.add('locked');
    document.getElementById('app').classList.add('panel-locked');
  }

  _activeOverlay = name;

  // Update nav button active states
  ['watch','alerts','scanner','draw','inds'].forEach(n => {
    const btn = document.getElementById('nav-' + n);
    if (btn) btn.classList.toggle('active', n === name);
  });

  _updateLockBtn();
  _syncAllDockedBtns();
}

function closeOverlay() {
  const panel = document.getElementById('overlay-panel');
  panel.classList.remove('open', 'locked');
  document.getElementById('app').classList.remove('panel-locked');
  ['watch','alerts','scanner','draw','inds'].forEach(n => {
    const btn = document.getElementById('nav-' + n);
    if (btn) btn.classList.remove('active');
  });
  _activeOverlay = null;
  _overlayLocked = false;
  _updateLockBtn();
  _syncAllDockedBtns();
  // Re-measure canvas now that panel has closed/undocked
  requestAnimationFrame(() => { try { sizeCanvases(); draw(); } catch(e){} });
  setTimeout(() => { try { sizeCanvases(); draw(); } catch(e){} }, 200);
}

function toggleOverlayLock() {
  const panel = document.getElementById('overlay-panel');
  _overlayLocked = !_overlayLocked;
  panel.classList.toggle('locked', _overlayLocked);
  // Toggle class on #app so margin-left CSS rule kicks in for #main-col
  document.getElementById('app').classList.toggle('panel-locked', _overlayLocked);
  _updateLockBtn();
  // Re-measure canvas after margin transition completes
  requestAnimationFrame(() => { try { sizeCanvases(); draw(); } catch(e){} });
  setTimeout(() => { try { sizeCanvases(); draw(); } catch(e){} }, 200);
}

function _updateLockBtn() {
  const btn = document.getElementById('ov-lock-btn');
  if (!btn) return;
  if (_overlayLocked) {
    btn.classList.add('pinned');
    btn.title = 'Unpin panel';
    btn.textContent = '⊟';
  } else {
    btn.classList.remove('pinned');
    btn.title = 'Pin panel open';
    btn.textContent = '⊞';
  }
}

// Click-outside: close panel if open and not pinned
document.addEventListener('mousedown', function(e) {
  if (_overlayLocked) return;           // pinned — never auto-close
  if (!_activeOverlay) return;          // already closed
  const panel = document.getElementById('overlay-panel');
  const nav   = document.getElementById('nav');
  if (panel && panel.contains(e.target)) return;  // clicked inside panel
  if (nav   && nav.contains(e.target))   return;  // clicked a nav button
  closeOverlay();
}, true);

// Forward scanner results to the embedded panel
const _origScanResults = document.getElementById('scan-results-inner');

// Map old IDs used by scanner JS to new locations
// scanner-results → scan-results-inner
Object.defineProperty(document, '_scannerResultsEl', {
  get: () => _origScanResults
});

// Status bar time updater
function updateStatusTime() {
  const el = document.getElementById('sb-time');
  if (!el) return;
  const now = new Date();
  el.textContent = now.toUTCString().slice(17, 25) + ' UTC';
}
setInterval(updateStatusTime, 1000);
updateStatusTime();

