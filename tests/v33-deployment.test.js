import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import handler from '../api/paper.js';
import {summarize,PROFILES,tradeCard} from '../public/forward-model.js';
import {forwardFixture} from './fixtures/forward-ui.mjs';

test('V33 reads its own strict profile without touching V27B',async()=>{
 const old=globalThis.fetch,payload=forwardFixture('v33');
 globalThis.fetch=async url=>{
  assert.match(String(url),/id=eq.__apex_book_v33_forward_paper_runtime__/);
  assert.doesNotMatch(String(url),/v27b/);
  return new Response(JSON.stringify([{feature_vector:payload}]),{status:200});
 };
 try{
  const response=await handler(new Request('https://example.test/api/paper?book=v33&table=state'));
  assert.equal(response.status,200);
  const model=summarize(await response.json(),'v33');
  assert.equal(model.equity,100250);assert.equal(PROFILES.v33.maximum,.10);
 }finally{globalThis.fetch=old;}
});

test('V33 never accepts the previous book specification or claims partial wins',()=>{
 const p=forwardFixture('v33');
 p.trades=[{lot_id:1,net_pnl_gbp:25,lot_fully_closed:false}];
 const m=summarize(p,'v33');assert.equal(m.closedPnl,25);assert.equal(m.winRate,null);
 p.metadata.profile='higher_5_12_joint';assert.throws(()=>summarize(p,'v33'));
});

test('new book has tabs, research disclosure and detailed paper cards',()=>{
 const read=p=>fs.readFileSync(new URL('../'+p,import.meta.url),'utf8');
 assert.match(read('public/engine-book.html'),/data-book="v33"/);
 assert.match(read('public/compare-books.js'),/'v33'/);
 assert.match(read('public/progress.html'),/engine-book.html\?book=v33/);
 assert.match(read('public/forward-books.js'),/£92,500/);
 const card=tradeCard({instrument:'AAPL',sleeve:'fast',direction:1,units:10,entry_price:100,stop_price:95,
  decision_atr:2,stop_atr_multiple:2.5,signal_rationale:'Prior-close cost filter passed',
  partials_policy:'Proportional risk reductions; no profit-target partials',management_rule:'Five-session time exit'});
 assert.match(card,/Prior-close cost filter passed/);assert.match(card,/95.00/);assert.match(card,/Five-session time exit/);
});

test('blocked activation never labels the seed date as verified market inputs',()=>{
 for(const book of ['v27b','v33']){
  const p=forwardFixture(book);
  p.daily=[p.daily[0]];p.metadata.last_processed_session=null;p.metadata.session_count=0;
  p.state.status='blocked';
  assert.equal(summarize(p,book).through,null);
  p.metadata.last_input_session='2026-09-04';
  assert.equal(summarize(p,book).through,'2026-09-04');
 }
});
