import test from 'node:test';
import assert from 'node:assert/strict';
import { summarize, tradeCard } from '../public/forward-model.js';
import { summarizeLegacy, fxObservationOverdue } from '../public/legacy-forward-model.js';
import { forwardFixture } from './fixtures/forward-ui.mjs';
import fs from 'node:fs';

test('unconfirmed V27B instruction never appears queued or executable',()=>{
  const html=tradeCard({symbol:'SPY',direction:1,decision_durability:'unconfirmed_not_executable',management_rule:'Monthly rebalance / 3 ATR trailing stop'},'pending');
  assert.match(html,/Unconfirmed · cannot execute/);assert.doesNotMatch(html,/Queued|Intraday · 15:59/);
});
test('simulated entry time cannot masquerade as contemporaneous decision evidence',()=>{
  const html=tradeCard({instrument:'SPY',entry_time:'2026-09-08T14:00:00Z'},'trades');
  assert.match(html,/Decision recorded at<\/dt><dd>Not supplied/);
  assert.doesNotMatch(html,/Recorded before fill/);
});
test('V27B partial exit profits count but unfinished lots do not become winning trades',()=>{
  const payload=forwardFixture('v27b');
  payload.trades=[{lot_id:1,net_pnl_gbp:100,lot_fully_closed:false},{lot_id:2,net_pnl_gbp:-20,lot_fully_closed:false},{lot_id:2,net_pnl_gbp:30,lot_fully_closed:true}];
  const m=summarize(payload,'v27b');
  assert.equal(m.closedPnl,110);assert.equal(m.completedLots,1);assert.equal(m.winRate,1);
});
test('repaired Book S without complete hourly observations cannot report zero maximum drawdown',()=>{
  const payload={book_id:'s',metadata:{book_id:'s',account_currency:'USD',initial_equity:100000,paper_only:true,broker_enabled:false,accounting_version:'quote_cash_v2'},daily:[{date:'2026-09-08',equity:100600,cash:100449,drawdown:0}],positions:[],trades:[],pending:[]};
  const m=summarizeLegacy(payload,'s');assert.equal(m.hourlyRiskIncomplete,true);assert.equal(m.maxDD,null);
});
test('Book S freshness follows observed closed hours, not page reload time',()=>{
  assert.equal(fxObservationOverdue('2026-09-08T09:00:00Z','2026-09-08T13:00:00Z'),true);
  assert.equal(fxObservationOverdue('2026-09-08T13:00:00Z','2026-09-08T13:30:00Z'),false);
  assert.equal(fxObservationOverdue('2026-09-04T21:00:00Z','2026-09-06T12:00:00Z'),false);
});

test('published pages do not advertise unverified Book S profits or cross-currency winners',()=>{
  for(const path of ['engine-book.html','progress.html','ab-race.html','compare-books.js','forward-books.js']){
    const source=fs.readFileSync(new URL('../public/'+path,import.meta.url),'utf8');
    assert.doesNotMatch(source,/#1 (MOST )?PROFIT|\+\$2,947|\+US\$607\.30|5\.5-year causal audit/);
  }
});

test('V27B is discoverable on both comparison and evidence pages',()=>{
  for(const path of ['compare-books.js','progress.html','engine-book.html']){
    assert.match(fs.readFileSync(new URL('../public/'+path,import.meta.url),'utf8'),/v27b/);
  }
});
