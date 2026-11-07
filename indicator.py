"""UNKOWN BADSHA - Volume Order Block indicator (Pine v5 -> Python), EURUSD 15m.

Run modes:
  python indicator.py          -> naye signals email karo (state.json se duplicate roke)
  python indicator.py check 10 -> last 10 signals + active boxes print karo (chart se match karne ke liye)
"""
import json
import os
import smtplib
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

# ---- Settings (env se override) ---------------------------------------------
DATA_SOURCE = os.getenv("DATA_SOURCE", "dukascopy")   # dukascopy | tradingview
LABEL = os.getenv("LABEL", "EURUSD")
INTERVAL = os.getenv("INTERVAL", "15m")               # 5m | 15m | 30m
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "15"))
LENGTH = int(os.getenv("LENGTH", "5"))                # Volume Pivot Length
MITIGATION = os.getenv("MITIGATION", "Wick")          # Wick | Close
EXT_LAST = int(os.getenv("EXT_LAST", "3"))            # chart par kitne box dikhte hain
DEC = int(os.getenv("PRICE_DECIMALS", "5"))
TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Kolkata"))  # mail ka time is timezone mein
TV_SYMBOL = os.getenv("TV_SYMBOL", "EURUSD")
TV_EXCHANGE = os.getenv("TV_EXCHANGE", "FXCM")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO") or EMAIL_USER
STATE_FILE = Path(__file__).parent / "state.json"

STEP_MIN = {"5m": 5, "15m": 15, "30m": 30}[INTERVAL]
STEP = pd.Timedelta(minutes=STEP_MIN)


# ---- Pine helpers ------------------------------------------------------------
def pivot_high(vol: np.ndarray, length: int) -> np.ndarray:
    """ta.pivothigh(volume, length, length): bar i par value = volume[i-length]
    agar wo apne dono taraf ke `length` bars mein sabse bada hai."""
    out = np.full(len(vol), np.nan)
    for i in range(2 * length, len(vol)):
        window = vol[i - 2 * length : i + 1]
        if vol[i - length] >= window.max():
            out[i] = vol[i - length]
    return out


def compute(df: pd.DataFrame, length: int = LENGTH, mitigation: str = MITIGATION):
    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    v = df["Volume"].to_numpy(float)
    t = df.index
    n = len(df)
    hl2 = (h + l) / 2

    upper = pd.Series(h).rolling(length).max().to_numpy()
    lower = pd.Series(l).rolling(length).min().to_numpy()
    if mitigation == "Close":
        tgt_bull = pd.Series(c).rolling(length).min().to_numpy()
        tgt_bear = pd.Series(c).rolling(length).max().to_numpy()
    else:
        tgt_bull, tgt_bear = lower, upper

    phv = pivot_high(v, length)

    os_state = 0
    bull_obs, bear_obs = [], []   # newest pehle (Pine ke array.unshift jaisa)
    events = []

    for i in range(n):
        if i >= length and not np.isnan(upper[i]):
            if h[i - length] > upper[i]:
                os_state = 0
            elif l[i - length] < lower[i]:
                os_state = 1

        if not np.isnan(phv[i]):
            j = i - length
            if os_state == 1:
                ob = dict(top=hl2[j], btm=l[j], avg=(hl2[j] + l[j]) / 2, left=t[j])
                bull_obs.insert(0, ob)
                events.append(dict(time=t[i], type="ENTRY", ob=ob))
            else:
                ob = dict(top=h[j], btm=hl2[j], avg=(h[j] + hl2[j]) / 2, left=t[j])
                bear_obs.insert(0, ob)
                events.append(dict(time=t[i], type="EXIT", ob=ob))

        if not np.isnan(tgt_bull[i]):
            keep = [o for o in bull_obs if not tgt_bull[i] < o["btm"]]
            if len(keep) != len(bull_obs):
                events.append(dict(time=t[i], type="BULL_MITIGATED", ob=None))
            bull_obs = keep
        if not np.isnan(tgt_bear[i]):
            keep = [o for o in bear_obs if not tgt_bear[i] > o["top"]]
            if len(keep) != len(bear_obs):
                events.append(dict(time=t[i], type="BEAR_MITIGATED", ob=None))
            bear_obs = keep

    return events, bull_obs, bear_obs


# ---- Data sources ------------------------------------------------------------
def _norm(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=str.capitalize)[["Open", "High", "Low", "Close", "Volume"]].dropna()
    idx = pd.to_datetime(df.index)
    df.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    # Chalti hui (incomplete) bar hatao -> sirf closed bars (repaint nahi)
    if pd.Timestamp.now(tz="UTC") < df.index[-1] + STEP:
        df = df.iloc[:-1]
    return df


def fetch_dukascopy() -> pd.DataFrame:
    import dukascopy_python as dk
    from dukascopy_python.instruments import INSTRUMENT_FX_MAJORS_EUR_USD

    iv = {"5m": dk.INTERVAL_MIN_5, "15m": dk.INTERVAL_MIN_15, "30m": dk.INTERVAL_MIN_30}[INTERVAL]
    end = datetime.now(timezone.utc).replace(tzinfo=None)
    start = end - timedelta(days=LOOKBACK_DAYS)
    return _norm(dk.fetch(INSTRUMENT_FX_MAJORS_EUR_USD, iv, dk.OFFER_SIDE_BID, start, end))


def fetch_tradingview() -> pd.DataFrame:
    """Unofficial (tvdatafeed). TradingView ka FXCM data = chart jaisa volume.
    Naive time runner ke local time mein aata hai (GitHub runner = UTC)."""
    from tvDatafeed import Interval, TvDatafeed

    iv = {"5m": Interval.in_5_minute, "15m": Interval.in_15_minute, "30m": Interval.in_30_minute}[INTERVAL]
    bars = int(LOOKBACK_DAYS * 24 * 60 / STEP_MIN)
    df = TvDatafeed().get_hist(TV_SYMBOL, TV_EXCHANGE, iv, n_bars=min(bars, 5000))
    if df is None or df.empty:
        sys.exit("TradingView se data nahi mila")
    return _norm(df)


def fetch() -> pd.DataFrame:
    df = fetch_tradingview() if DATA_SOURCE == "tradingview" else fetch_dukascopy()
    if df.empty:
        sys.exit("Data nahi mila")
    if df["Volume"].sum() == 0:
        sys.exit("Volume zero hai - is data source se indicator nahi chalega")
    return df


# ---- Formatting --------------------------------------------------------------
def loc(ts: pd.Timestamp) -> str:
    return ts.tz_convert(TZ).strftime("%a %d %b %H:%M %Z")


def zone(o) -> str:
    return f"{o['btm']:.{DEC}f} - {o['top']:.{DEC}f} (avg {o['avg']:.{DEC}f})"


def active_summary(bull, bear) -> str:
    lines = ["", "Abhi chart par active boxes:"]
    for name, obs in (("EXIT (bear)", bear), ("ENTRY (bull)", bull)):
        if not obs:
            lines.append(f"  {name}: koi nahi")
        for o in obs[:EXT_LAST]:
            lines.append(f"  {name}: {zone(o)} | candle {loc(o['left'])}")
    return "\n".join(lines)


def fmt(ev, bull, bear) -> str:
    confirm = ev["time"] + STEP  # signal candle ka close = alert ka asli time
    head = f"{LABEL} {INTERVAL}"
    if ev["type"] in ("ENTRY", "EXIT"):
        icon, name = ("🟢", "ENTRY (bullish OB)") if ev["type"] == "ENTRY" else ("🟠", "EXIT (bearish OB)")
        o = ev["ob"]
        body = (f"{icon} UNKOWN BADSHA: {name} | {head}\n\n"
                f"Chart par label/box candle: {loc(o['left'])}\n"
                f"Signal confirm hua (candle close): {loc(confirm)}\n"
                f"Zone: {zone(o)}")
    else:
        side = "Bullish" if ev["type"] == "BULL_MITIGATED" else "Bearish"
        body = (f"⚪ {side} order block mitigated | {head}\n\n"
                f"Time (candle close): {loc(confirm)}")
    return body + "\n" + active_summary(bull, bear)


# ---- Email -------------------------------------------------------------------
def send_email(text: str):
    msg = EmailMessage()
    msg["Subject"] = text.splitlines()[0]
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO
    msg.set_content(text)
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=20) as srv:
            srv.login(EMAIL_USER, EMAIL_PASS)
            srv.send_message(msg)
    except Exception as e:
        print("Email error:", e)


def send(text: str):
    print(text, "\n" + "-" * 40)
    if EMAIL_USER and EMAIL_PASS:
        send_email(text)


# ---- Main --------------------------------------------------------------------
def check(n: int):
    df = fetch()
    events, bull, bear = compute(df)
    print(f"Source={DATA_SOURCE} | {LABEL} {INTERVAL} | bars={len(df)} | last closed bar open: {loc(df.index[-1])}\n")
    print(f"Pichle {n} events (naye neeche):")
    for e in events[-n:]:
        o = e["ob"]
        extra = f" | candle {loc(o['left'])} | {zone(o)}" if o else ""
        print(f"  {e['type']:<15} confirm {loc(e['time'] + STEP)}{extra}")
    print(active_summary(bull, bear))


def main():
    df = fetch()
    events, bull, bear = compute(df)

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    last_seen = pd.Timestamp(state["last_time"]) if "last_time" in state else None
    last_bar = df.index[-1]

    if last_seen is None:
        new = [e for e in events if e["time"] == last_bar]   # pehli run: spam nahi
    else:
        new = [e for e in events if e["time"] > last_seen]   # miss hue bars bhi

    for e in new:
        send(fmt(e, bull, bear))
    if not new:
        print(f"Koi naya signal nahi | last closed bar open {loc(last_bar)} | "
              f"active bull={len(bull)} bear={len(bear)}")

    STATE_FILE.write_text(json.dumps({"last_time": last_bar.isoformat()}))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        check(int(sys.argv[2]) if len(sys.argv) > 2 else 10)
    else:
        main()
