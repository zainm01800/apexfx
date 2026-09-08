"""Settled raw cash-minute inputs; no daily-price splice, guessed sigma or FX fallback."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import copy
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from ..forward_v14.data import (DataUnavailable, XNYS, BOE_XML_ENDPOINT,
    normalize_boe_xml, select_fx, utc_timestamp, latest_completed_session)

NY_TZ=ZoneInfo('America/New_York')
SETTLEMENT_GRACE=pd.Timedelta(minutes=30)
COLUMNS=['open','high','low','close','volume']

@dataclass(frozen=True, slots=True)
class HistoricalWarmup:
    atr_14: float
    volatility_14: float
    prior_close: float
    noise_sigmas: dict[int,float]
    fx_rate: float
    as_of_session: str
    fx_info: dict=field(default_factory=dict)
    provenance: dict=field(default_factory=dict)
    unavailable_reason: str|None=None

def current_ny_time(): return datetime.now(NY_TZ)

def session_times(day):
    day=pd.Timestamp(day).tz_localize(None).normalize()
    if not XNYS.is_session(day): raise DataUnavailable(f'{day.date()}: not an XNYS session')
    return pd.Timestamp(XNYS.session_open(day)),pd.Timestamp(XNYS.session_close(day))

def is_us_market_hours(dt=None):
    now=utc_timestamp(dt or current_ny_time());day=now.tz_convert(NY_TZ).date().isoformat()
    if not XNYS.is_session(day):return False
    opening,closing=session_times(day)
    return opening<=now<closing

def validate_session_minutes(bars,day,now=None):
    opening,closing=session_times(day)
    if utc_timestamp(now or datetime.now(timezone.utc))<closing+SETTLEMENT_GRACE:
        raise DataUnavailable(f'{day}: cash session has not settled')
    if bars.empty or not set(COLUMNS).issubset(bars):raise DataUnavailable(f'{day}: minute OHLCV missing')
    dates=pd.DatetimeIndex(bars.index)
    if dates.tz is None:raise DataUnavailable('Minute timestamps must have an explicit timezone')
    dates=dates.tz_convert('UTC').as_unit('ns')
    expected=pd.date_range(opening,closing,freq='min',inclusive='left').as_unit('ns')
    if dates.has_duplicates or not dates.equals(expected):
        raise DataUnavailable(f'{day}: missing, duplicate, unordered or off-session minute')
    out=bars[COLUMNS].copy();out.index=dates
    values=out.to_numpy(float);prices=values[:,:4]
    if (not np.isfinite(values).all() or (prices<=0).any() or (values[:,4]<=0).any()
        or (prices[:,1]+1e-9<prices.max(axis=1)).any() or (prices[:,2]-1e-9>prices.min(axis=1)).any()):
        raise DataUnavailable(f'{day}: invalid minute OHLCV')
    return out

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

def freeze_session(bars,day,dividend,fx_info,provenance,now):
    bars=validate_session_minutes(bars,day,now)
    if not np.isfinite(dividend) or dividend<0:raise DataUnavailable('Invalid known dividend')
    opening,closing=session_times(day)
    record=dict(date=day,bars=[[t.isoformat(),*map(float,row)] for t,row in zip(bars.index,bars.to_numpy())],
        dividend=float(dividend),fx_info=copy.deepcopy(fx_info),source=copy.deepcopy(provenance),
        frozen_at_utc=utc_timestamp(now).isoformat(),normal_session=len(bars)==390,
        opening_utc=opening.isoformat(),closing_utc=closing.isoformat())
    record['sha256']=sha256(canonical(record)).hexdigest()
    return record

def thaw_session(record,now=None):
    body={k:v for k,v in record.items() if k!='sha256'}
    if sha256(canonical(body)).hexdigest()!=record.get('sha256'):raise DataUnavailable('Frozen minute input hash mismatch')
    rows=record['bars'];bars=pd.DataFrame([r[1:] for r in rows],columns=COLUMNS,index=pd.to_datetime([r[0] for r in rows],utc=True))
    return validate_session_minutes(bars,record['date'],now or record['frozen_at_utc'])

def build_historical_warmup(archive,day,*,require_noise=True):
    """Exact prior scheduled slots, raw cash closes, ex-date dividends and 14 normal-session noise."""
    current=archive[day];info=current['fx_info']
    if not info:raise DataUnavailable(f'{day}: no publication-qualified FX; no substitute fixing')
    opening,_=session_times(day)
    observed=utc_timestamp(info['source_date']);available=pd.Timestamp(info['available_at_utc'])
    if available.tzinfo is None:raise DataUnavailable('FX publication timestamp lacks timezone')
    available=available.tz_convert('UTC')
    age=(pd.Timestamp(day)-observed.tz_localize(None).normalize()).days
    rate=float(info['rate'])
    if not np.isfinite(rate) or rate<=0 or not 1<=age<=6 or available>opening:
        raise DataUnavailable(f'{day}: invalid, stale or unpublished fixing')
    history=list(XNYS.sessions_in_range(pd.Timestamp(day)-pd.Timedelta(days=65),pd.Timestamp(day)))[:-1]
    prior15=history[-15:]
    normal=[d for d in history if (session_times(d)[1]-session_times(d)[0])==pd.Timedelta(minutes=390)][-14:]
    needed=set(prior15)|(set(normal) if require_noise else set())
    missing=[str(d.date()) for d in needed if str(d.date()) not in archive]
    if len(prior15)<15 or missing:
        return HistoricalWarmup(float('nan'),float('nan'),float('nan'),{},rate,'',info,
            {'required_missing_sessions':sorted(missing)},'insufficient_frozen_cash_minute_history')
    frames={str(d.date()):thaw_session(archive[str(d.date())]) for d in needed}
    closes=[float(frames[str(d.date())].close.iloc[-1]) for d in prior15]
    returns=[];ranges=[]
    for i,d in enumerate(prior15[1:],1):
        key=str(d.date());f=frames[key];dividend=float(archive[key]['dividend'])
        anchor=closes[i-1]-dividend
        returns.append((closes[i]+dividend)/closes[i-1]-1)
        ranges.append(max(float(f.high.max()-f.low.min()),abs(float(f.high.max())-anchor),abs(float(f.low.min())-anchor)))
    sigma={}
    if require_noise:
        for offset in range(30,361,30):
            sigma[offset]=float(np.mean([abs(float(frames[str(d.date())].close.iloc[offset-1])/float(frames[str(d.date())].open.iloc[0])-1) for d in normal]))
    return HistoricalWarmup(float(np.mean(ranges)),float(np.std(returns,ddof=1)),
        closes[-1]-float(current['dividend']),sigma,rate,str(prior15[-1].date()),info,
        {'prior_session_hashes':{str(d.date()):archive[str(d.date())]['sha256'] for d in sorted(needed)},
         'current_session_hash':current['sha256'],'feature_basis':'raw minute cash OHLC; ex-date dividend-adjusted returns'})

def fetch_settled_inputs(now=None):
    """Read only. Short provider retention bootstraps an archive; absent history stays unavailable."""
    import yfinance as yf
    import httpx
    now=utc_timestamp(now or datetime.now(timezone.utc));latest=latest_completed_session(now)
    start=(now-pd.Timedelta(days=7)).date().isoformat();end=(now+pd.Timedelta(days=1)).date().isoformat()
    ticker=yf.Ticker('SPY')
    raw=ticker.history(start=start,end=end,interval='1m',auto_adjust=False,back_adjust=False,
        actions=False,repair=False,prepost=False)
    if raw.empty:raise DataUnavailable('No raw SPY minute history')
    raw=raw.rename(columns=lambda c:str(c).lower());raw.index=pd.DatetimeIndex(raw.index)
    if raw.index.tz is None:raise DataUnavailable('Provider minute timezone missing')
    raw.index=raw.index.tz_convert('UTC')
    daily=ticker.history(start=start,end=end,interval='1d',auto_adjust=False,back_adjust=False,
        actions=True,repair=False,prepost=False)
    if daily.empty or 'Dividends' not in daily:raise DataUnavailable('Corporate action history unavailable')
    # Daily index labels are exchange dates: do not shift midnight UTC into yesterday New York.
    action_dates=[str(pd.Timestamp(d).date()) for d in daily.index]
    if len(action_dates)!=len(set(action_dates)):raise DataUnavailable('Duplicate corporate action session labels')
    dividends={str(pd.Timestamp(d).date()):float(v) for d,v in daily['Dividends'].items()}
    with httpx.Client(timeout=45,follow_redirects=True,headers={'User-Agent':'ApexFX-ForwardPaper/1.0'}) as client:
        response=client.get(BOE_XML_ENDPOINT,params={'CodeVer':'new','xml.x':'yes',
            'Datefrom':(now-pd.Timedelta(days=30)).strftime('%d/%b/%Y'),'Dateto':now.strftime('%d/%b/%Y'),
            'SeriesCodes':'XUDLUSS','VPD':'Y','VFD':'Y'})
        response.raise_for_status();fx=normalize_boe_xml(response.content)
    provenance={'source':'Yahoo raw cash-minute proxy; not certified executable/SIP quotes',
        'price_adjustment':'auto_adjust=False; back_adjust=False; repair=False',
        'retrieved_at_utc':now.isoformat(),'fx_series':'BoE XUDLUSS USD per GBP',
        'fx_xml_sha256':sha256(response.content).hexdigest()}
    output={};issues={}
    for d in XNYS.sessions_in_range(start,latest):
        day=str(d.date());opening,closing=session_times(day)
        bars=raw.loc[(raw.index>=opening)&(raw.index<closing),COLUMNS]
        try:
            if day not in dividends:raise DataUnavailable('No exact-session corporate action observation')
            info=select_fx(fx,day,SimpleNamespace(maximum_fx_age_days=6))
            output[day]=freeze_session(bars,day,dividends[day],info,provenance,now)
        except DataUnavailable as exc:issues[day]=str(exc)
    return {'sessions':output,'issues':issues,'latest':str(latest.date()),'retrieved_at_utc':now.isoformat()}

# Old live/partial APIs deliberately removed: callers must use frozen settled sessions.
