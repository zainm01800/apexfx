"""Read-only fresh daily data; no old research caches or brokerage routes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import os
from time import monotonic, sleep
import numpy as np
import pandas as pd

from ..forward_v14.data import (DataUnavailable, XNYS, PRICE_COLUMNS,
    latest_completed_session, normalize_symbol_frame, normalize_boe_xml,
    BOE_XML_ENDPOINT, utc_timestamp, session_close_utc)
from .spec import SYMBOLS, CIKS


def extract_filings(obj, symbol, cutoff):
    """Require complete current submissions and explicit acceptance timestamps."""
    rows=obj.get("filings",{}).get("recent")
    if not isinstance(rows,dict): raise DataUnavailable(f"{symbol}: SEC recent filings absent")
    fields=("form","items","accessionNumber","acceptanceDateTime","filingDate")
    count=len(rows.get("form",[]))
    if not count or any(not isinstance(rows.get(k),list) or len(rows[k])!=count for k in fields):
        raise DataUnavailable(f"{symbol}: SEC fields missing or misaligned")
    # A full recent list must cover the entire five-session exclusion window.
    dates=pd.to_datetime(rows["filingDate"],errors="coerce",utc=True)
    if dates.isna().any() or dates.min()>cutoff-pd.Timedelta(days=20):
        raise DataUnavailable(f"{symbol}: SEC recent history does not cover the decision window")
    result={}
    for i,form in enumerate(rows["form"]):
        if form not in ("10-Q","10-Q/A","10-K","10-K/A","8-K","8-K/A"):continue
        items=rows["items"][i]
        if not isinstance(items,str): raise DataUnavailable(f"{symbol}: malformed SEC items")
        if form.startswith("8-K") and "2.02" not in items.replace(" ","").split(","):continue
        stamp=pd.Timestamp(rows["acceptanceDateTime"][i])
        if stamp.tzinfo is None:raise DataUnavailable(f"{symbol}: SEC acceptance missing timezone")
        stamp=stamp.tz_convert("UTC")
        if stamp>cutoff:continue
        row=dict(symbol=symbol,accession=rows["accessionNumber"][i],form=form,items=items,
                 accepted_at_utc=stamp.isoformat(),filing_date=rows["filingDate"][i])
        if row["accession"] in result and result[row["accession"]]!=row:
            raise DataUnavailable(f"{symbol}: conflicting SEC accession")
        result[row["accession"]]=row
    return list(result.values())


def fetch_market(now=None):
    import httpx
    import yfinance as yf
    now=utc_timestamp(now or datetime.now(timezone.utc))
    latest=latest_completed_session(now)
    start=(latest-pd.Timedelta(days=800)).strftime("%Y-%m-%d")
    end=(latest+pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    expected=pd.DatetimeIndex(XNYS.sessions_in_range(start,latest))
    def price(symbol):
        raw=yf.Ticker(symbol).history(start=start,end=end,interval="1d",auto_adjust=True,
                        actions=False,repair=False,raise_errors=True)
        frame=normalize_symbol_frame(raw,symbol,latest=latest)
        if len(expected.difference(frame.index)):
            raise DataUnavailable(f"{symbol}: missing settled XNYS bars")
        return symbol,frame.loc[expected,list(PRICE_COLUMNS)].set_axis(expected.tz_localize("UTC"))
    with ThreadPoolExecutor(max_workers=3) as pool:
        panel=dict(pool.map(price,SYMBOLS))
    provenance=dict(equity_source="Yahoo adjusted OHLC, first-seen forward observations",
                    retrieved_at_utc=now.isoformat(),latest_completed_session=str(latest.date()),
                    symbols=list(SYMBOLS),sec_sources={})
    events=[]
    headers={"User-Agent":os.environ.get("SEC_USER_AGENT") or "ApexFX-Quantitative-Research zainm01800@gmail.com"}
    with httpx.Client(timeout=45,follow_redirects=True,headers=headers) as client:
        # Explicit maximum two starts per second; stop immediately on a rejection.
        next_request=monotonic()
        for symbol,cik in CIKS.items():
            sleep(max(0,next_request-monotonic()))
            next_request=monotonic()+.5
            url=f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
            response=client.get(url)
            if response.status_code!=200:raise DataUnavailable(f"{symbol}: SEC submissions HTTP {response.status_code}; no unfiltered fallback")
            events.extend(extract_filings(response.json(),symbol,session_close_utc(latest)))
            provenance["sec_sources"][symbol]=dict(url=url,sha256=sha256(response.content).hexdigest())
        response=client.get(BOE_XML_ENDPOINT,params=dict(CodeVer="new",**{"xml.x":"yes"},
            Datefrom=(latest-pd.Timedelta(days=40)).strftime("%d/%b/%Y"),Dateto=now.strftime("%d/%b/%Y"),
            SeriesCodes="XUDLUSS",VPD="Y",VFD="Y"))
        if response.status_code!=200:raise DataUnavailable(f"BoE FX HTTP {response.status_code}")
        fx=normalize_boe_xml(response.content)
        provenance["fx_response_sha256"]=sha256(response.content).hexdigest()
    return dict(panel=panel,fx=fx,events=events,latest=latest.tz_localize("UTC"),
                retrieved_at_utc=now,provenance=provenance)


def anchored_panel(market, state):
    """Keep each first-seen execution price immutable; reject revisions, not rewrite P&L.

    Uniform dividend/split adjustment rebases are normalized to the stored price
    scale. Nonuniform corrections fail closed. No historical bars are replaced.
    """
    if not state.get("bars"):return market["panel"]
    anchors=sorted(state["bars"])[-5:]
    out={}
    for symbol,frame in market["panel"].items():
        ratios=[]
        for day in anchors:
            stamp=pd.Timestamp(day)
            if stamp not in frame.index:raise DataUnavailable(f"{symbol}: stored anchor no longer in provider history")
            old=np.asarray(state["bars"][day][symbol],dtype=float)
            new=frame.loc[stamp,list(PRICE_COLUMNS)].to_numpy(float)
            ratios.extend(old/new)
        scale=float(np.median(ratios))
        if not np.allclose(ratios,scale,rtol=2e-6,atol=0):
            raise DataUnavailable(f"{symbol}: nonuniform historical revision; saved fills preserved")
        out[symbol]=frame*scale
    return out
