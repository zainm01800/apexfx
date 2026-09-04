// Synthetic, local-only presentation fixture. Never a production book seed.
export function forwardFixture(book) {
  const strict=book==='v6';
  return {schema_version:1,book_id:book,generated_at_utc:'2026-09-10T23:30:00Z',state:{status:'LOCAL_UI_FIXTURE',halted:false},
    metadata:{book_id:book,profile:strict?'strict_3_6_static':'standard_5_10_static',account_currency:'GBP',initial_equity:100000,paper_only:true,broker_enabled:false,funded_qualified:false,experimental:true,activation_recorded_at_utc:'2026-09-04T23:30:00Z',last_processed_session:'2026-09-10',session_count:3,spec_sha256:'LOCAL-TEST-ONLY'},
    daily:[{date:'2026-09-04',equity:100000,cash:100000,is_seed:true,drawdown_from_peak:0},{date:'2026-09-08',equity:99920,cash:99920,drawdown_from_peak:.0008},{date:'2026-09-09',equity:100080,cash:100080,drawdown_from_peak:0},{date:'2026-09-10',equity:100250,cash:100100,open_pnl:150,day_pnl:170,cum_pnl:250,drawdown_from_peak:0,external_daily_floor:strict?97080:95080,external_maximum_floor:strict?94000:90000}],
    positions:[{instrument:'SPY',direction:'long',units:37.5,entry_price:100,last_px:105,stop:95,open_pnl:150,entry_time:'2026-09-08T13:30:00Z',decision_date:'2026-09-04',decision_recorded_at_utc:'2026-09-04T23:30:00Z',decision_atr:3.33333,lagged_vix:18.5,initial_total_risk:153,current_risk_gbp:301.5,scheduled_exit_session:'2026-09-15',signal_rationale:'LOCAL TEST FIXTURE: RSI2 below 10 while closing above the 200-session average.'}],
    pending:[{instrument:'XLV',direction:'short',eligible_fill_session:'2026-09-11',decision_date:'2026-09-10',decision_recorded_at_utc:'2026-09-10T23:30:00Z',decision_atr:3,lagged_vix:32,signal_rationale:'LOCAL TEST FIXTURE: example queued decision, not an actual engine instruction.'}],
    trades:[{instrument:'XLE',direction:'short',units:10,entry_price:90,exit_price:77.1875,entry_time:'2026-09-08T13:30:00Z',exit_time:'2026-09-10T13:30:00Z',stop:95,net_pnl_gbp:101.5,exit_reason:'time_exit',signal_rationale:'LOCAL TEST FIXTURE: closed-card rendering only.'}],
  };
}
