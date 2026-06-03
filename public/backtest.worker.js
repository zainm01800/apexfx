// APEX backtest Web Worker — PURE COMPUTE, off the main thread.
// The main thread fetches candles and POSTs results; the worker only runs the
// strategies (no fetch, no DOM, no credentials). One RUN message = one
// (pair, timeframe) job carrying its candle arrays.
//
//   main -> worker:  { type:'RUN', jobId, payload:{ bars, weekly, sym, assetClass, timeframe, runId, runTs, appVersion } }
//   worker -> main:  { type:'READY' }
//                    { type:'PROGRESS', jobId, idx, total, id }
//                    { type:'RESULT', jobId, rows, sym, timeframe }
//                    { type:'ERROR', jobId, error }
/* eslint-disable no-undef */
'use strict';

// Cache-bust the imports with the version the page passed in the worker URL
// (?b=...) — importScripts can otherwise serve a stale lib from the HTTP cache.
const _q = (self.location && self.location.search) ? self.location.search : '';
importScripts(
  '/lib/ta.js' + _q,
  '/lib/regime.js' + _q,
  '/lib/confluence.js' + _q,
  '/lib/strategies.js' + _q,
  '/lib/metrics.js' + _q,
  '/lib/hypotheses.js' + _q,
  '/lib/runjob.js' + _q
);

self.onmessage = function (e) {
  const m = e.data || {};
  if (m.type !== 'RUN') return;
  const { jobId, payload } = m;
  try {
    const rows = self.APEX.runjob.runJob(payload, (idx, total, id) => {
      self.postMessage({ type: 'PROGRESS', jobId, idx, total, id });
    });
    self.postMessage({ type: 'RESULT', jobId, rows, sym: payload.sym, timeframe: payload.timeframe });
  } catch (err) {
    self.postMessage({ type: 'ERROR', jobId, error: String((err && err.message) || err) });
  }
};

self.postMessage({ type: 'READY' });
