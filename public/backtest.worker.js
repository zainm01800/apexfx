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

importScripts(
  '/lib/ta.js',
  '/lib/regime.js',
  '/lib/confluence.js',
  '/lib/strategies.js',
  '/lib/metrics.js',
  '/lib/hypotheses.js',
  '/lib/runjob.js'
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
