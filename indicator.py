"""UNKOWN BADSHA - Smart Volume Confirmed OB indicator (Pine v5 -> Python), EURUSD 15m.

Run modes:
  python indicator.py                  -> sab symbols ke naye signals email karo (state.json se duplicate roke)
  python indicator.py check 10 [SYM]   -> last 10 signals + active boxes print karo (chart se match karne ke liye)
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
SYMBOLS = [x.strip().upper() for x in os.getenv("SYMBOLS", "EURUSD,GBPUSD,USDJPY,BTCUSD,ETHUSD").split(",") if x.strip()]
INTERVAL = os.getenv("INTERVAL", "15m")               # 5m | 15m | 30m
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "15"))
LENGTH = int(os.getenv("LENGTH", "3"))                # Volume Pivot Length
MITIGATION = os.getenv("MITIGATION", "Wick")          # Wick | Close
EXT_LAST = int(os.getenv("EXT_LAST", "3"))            # chart par kitne box dikhte hain (bull 3 / bear 3)
TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Kolkata"))  # mail ka time is timezone mein

# ---- Smart Volume settings (Pine inputs, screenshot wali values default) ----
MIN_STRENGTH = os.getenv("MIN_STRENGTH", "High")                   # High | Extreme
SHOW_WEAK = os.getenv("SHOW_WEAK", "0") == "1"                     # Show Weak Volume OB
VOL_LEN = int(os.getenv("VOL_LEN", "20"))                          # Volume Average Length
USE_LEGACY = os.getenv("USE_LEGACY", "1") == "1"                   # Legacy Multipliers (2.0 / 2.5)
HIGH_FACTOR = float(os.getenv("HIGH_FACTOR", "1.5"))
EXTREME_FACTOR = float(os.getenv("EXTREME_FACTOR", "2.5"))
DETECT_MODE = os.getenv("DETECT_MODE", "RVOL only")                # RVOL only | RVOL + Z-Score
Z_HIGH = float(os.getenv("Z_HIGH", "1"))
Z_EXTREME = float(os.getenv("Z_EXTREME", "2"))
USE_PCT = os.getenv("USE_PCT", "0") == "1"                         # Percentile Filter
PCT_MIN = float(os.getenv("PCT_MIN", "90"))

# Har symbol: Dukascopy instrument, TradingView (symbol, exchange), price decimals
PAIRS = {
    "EURUSD": dict(dk="INSTRUMENT_FX_MAJORS_EUR_USD", tv=("EURUSD", "FXCM"), dec=5),
    "GBPUSD": dict(dk="INSTRUMENT_FX_MAJORS_GBP_USD", tv=("GBPUSD", "FXCM"), dec=5),
    "USDJPY": dict(dk="INSTRUMENT_FX_MAJORS_USD_JPY", tv=("USDJPY", "FXCM"), dec=3),
    "BTCUSD": dict(dk="INSTRUMENT_VCCY_BTC_USD", tv=("BTCUSD", "BITSTAMP"), dec=2, bs="btcusd"),
    "ETHUSD": dict(dk="INSTRUMENT_VCCY_ETH_USD", tv=("ETHUSD", "BITSTAMP"), dec=2, bs="ethusd"),
}

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO") or EMAIL_USER
CRYPTO_SOURCE = os.getenv("CRYPTO_SOURCE", "bitstamp")   # bitstamp | dukascopy (BTC/ETH ke liye)
SKIP_INVALID = os.getenv("SKIP_INVALID", "1") == "1"      # 1 = jo box mail bhejte waqt tak toot chuka ho, uska alert nahi
SEND_MITIGATED = os.getenv("SEND_MITIGATED", "0") == "1"  # 0 = sirf ENTRY/EXIT mail (default), 1 = mitigation bhi
NTFY_TOPIC = os.getenv("NTFY_TOPIC")   # phone push (ntfy app)
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


def volume_flags(v: np.ndarray):
    """Smart Volume Engine: har bar ke liye (isHigh, isExtreme) - Pine section 5 jaisa."""
    n = len(v)
    s = pd.Series(v)
    ma = s.rolling(VOL_LEN).mean().to_numpy()
    std = s.rolling(VOL_LEN).std(ddof=0).to_numpy()      # ta.stdev = population std

    with np.errstate(divide="ignore", invalid="ignore"):
        rvol = np.where(ma > 0, v / ma, 0.0)
        z = np.where(std > 0, (v - ma) / std, 0.0)

    # ta.percentrank: pichhle VOL_LEN bars mein kitne % <= current
    pct = np.full(n, np.nan)
    for i in range(VOL_LEN, n):
        prev = v[i - VOL_LEN : i]
        pct[i] = (prev <= v[i]).sum() / VOL_LEN * 100.0
    pct_ok = np.ones(n, bool) if not USE_PCT else (np.nan_to_num(pct, nan=-1) >= PCT_MIN)

    hi_mult = 2.0 if USE_LEGACY else HIGH_FACTOR
    ex_mult = 2.5 if USE_LEGACY else max(EXTREME_FACTOR, HIGH_FACTOR)

    rvol_only = DETECT_MODE == "RVOL only"
    hi_cond = (rvol > hi_mult) & (np.ones(n, bool) if rvol_only else (z >= Z_HIGH)) & pct_ok
    ex_cond = (rvol > ex_mult) & (np.ones(n, bool) if rvol_only else (z >= Z_EXTREME)) & pct_ok

    data_ok = (np.arange(n) >= VOL_LEN) & (v > 0)
    is_high = data_ok & (hi_cond | ex_cond)
    is_extreme = data_ok & ex_cond
    return is_high, is_extreme


def compute(df: pd.DataFrame, length: int = LENGTH, mitigation: str = MITIGATION):
    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    v = np.nan_to_num(df["Volume"].to_numpy(float))
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

    # OB candle (bar i-length) ka volume strong hona chahiye (isHigh[length] / isExtreme[length])
    is_high, is_extreme = volume_flags(v)
    strong_arr = is_high if MIN_STRENGTH == "High" else is_extreme

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
            approved = bool(strong_arr[j]) or SHOW_WEAK
            if approved:
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


def fetch_dukascopy(sym: str) -> pd.DataFrame:
    import dukascopy_python as dk
    from dukascopy_python import instruments as ins

    iv = {"5m": dk.INTERVAL_MIN_5, "15m": dk.INTERVAL_MIN_15, "30m": dk.INTERVAL_MIN_30}[INTERVAL]
    end = datetime.now(timezone.utc).replace(tzinfo=None)
    start = end - timedelta(days=LOOKBACK_DAYS)
    return _norm(dk.fetch(getattr(ins, PAIRS[sym]["dk"]), iv, dk.OFFER_SIDE_BID, start, end))


def fetch_tradingview(sym: str) -> pd.DataFrame:
    """Unofficial (tvdatafeed). Naive time runner ke local time mein aata hai (GitHub runner = UTC)."""
    from tvDatafeed import Interval, TvDatafeed

    iv = {"5m": Interval.in_5_minute, "15m": Interval.in_15_minute, "30m": Interval.in_30_minute}[INTERVAL]
    bars = int(LOOKBACK_DAYS * 24 * 60 / STEP_MIN)
    tv_sym, tv_ex = PAIRS[sym]["tv"]
    df = TvDatafeed().get_hist(tv_sym, tv_ex, iv, n_bars=min(bars, 5000))
    if df is None or df.empty:
        raise RuntimeError("TradingView se data nahi mila")
    return _norm(df)


def fetch_bitstamp(sym: str) -> pd.DataFrame:
    """Bitstamp ka official public OHLC (TradingView ke BITSTAMP chart wala hi data, volume bhi wahi)."""
    need = int(LOOKBACK_DAYS * 24 * 60 / STEP_MIN)
    rows, end = [], None
    while len(rows) < need:
        params = {"step": STEP_MIN * 60, "limit": 1000}
        if end:
            params["end"] = end
        r = requests.get(f"https://www.bitstamp.net/api/v2/ohlc/{PAIRS[sym]['bs']}/", params=params, timeout=20)
        r.raise_for_status()
        part = r.json()["data"]["ohlc"]
        if not part:
            break
        rows = part + rows
        end = int(part[0]["timestamp"]) - 1
        if len(part) < 1000:
            break
    if not rows:
        raise RuntimeError("Bitstamp se data nahi mila")
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True)
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    return _norm(df)


def fetch(sym: str) -> pd.DataFrame:
    if PAIRS[sym].get("bs") and CRYPTO_SOURCE == "bitstamp":
        df = fetch_bitstamp(sym)
    elif DATA_SOURCE == "tradingview":
        df = fetch_tradingview(sym)
    else:
        df = fetch_dukascopy(sym)
    if df.empty:
        raise RuntimeError("Data nahi mila")
    if df["Volume"].sum() == 0:
        raise RuntimeError("Volume zero hai - is data source se indicator nahi chalega")
    return df


# ---- Formatting --------------------------------------------------------------
def loc(ts: pd.Timestamp) -> str:
    return ts.tz_convert(TZ).strftime("%a %d %b %H:%M %Z")


def zone(o, dec) -> str:
    return f"{o['btm']:.{dec}f} - {o['top']:.{dec}f} (avg {o['avg']:.{dec}f})"


def active_summary(bull, bear, dec) -> str:
    lines = ["", "Abhi chart par active boxes:"]
    for name, obs in (("EXIT (bear)", bear), ("ENTRY (bull)", bull)):
        if not obs:
            lines.append(f"  {name}: koi nahi")
        for o in obs[:EXT_LAST]:
            lines.append(f"  {name}: {zone(o, dec)} | candle {loc(o['left'])}")
    return "\n".join(lines)


def fmt(ev, bull, bear, sym) -> str:
    confirm = ev["time"] + STEP  # signal candle ka close = alert ka asli time
    head = f"{sym} {INTERVAL}"
    dec = PAIRS[sym]["dec"]
    if ev["type"] in ("ENTRY", "EXIT"):
        icon, name = ("🟢", "ENTRY (bullish OB)") if ev["type"] == "ENTRY" else ("🟠", "EXIT (bearish OB)")
        o = ev["ob"]
        body = (f"{icon} UNKOWN BADSHA: {name} | {head}\n\n"
                f"Chart par label/box candle: {loc(o['left'])}\n"
                f"Signal confirm hua (candle close): {loc(confirm)}\n"
                f"Zone: {zone(o, dec)}")
    else:
        side = "Bullish" if ev["type"] == "BULL_MITIGATED" else "Bearish"
        body = (f"⚪ {side} order block mitigated | {head}\n\n"
                f"Time (candle close): {loc(confirm)}")
    late = int((pd.Timestamp.now(tz="UTC") - confirm).total_seconds() // 60)
    sent = f"Mail bheji gayi: {loc(pd.Timestamp.now(tz='UTC'))} (candle close ke {max(late, 0)} min baad)"
    return body + "\n" + sent + "\n" + active_summary(bull, bear, dec)


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


def send_push(text: str):
    """ntfy.sh se phone par push notification (header latin-1 hota hai, isliye title mein emoji nahi)."""
    first = text.splitlines()[0]
    title = first.encode("ascii", "ignore").decode().strip(" |")
    tag = "green_circle" if "ENTRY" in first else "orange_circle" if "EXIT" in first else "white_circle"
    try:
        r = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=text.encode("utf-8"),
            headers={"Title": title, "Priority": "high", "Tags": tag},
            timeout=15,
        )
        if not r.ok:
            print("Push error:", r.status_code, r.text[:200])
    except Exception as e:
        print("Push error:", e)


def send(text: str):
    print(text, "\n" + "-" * 40)
    if EMAIL_USER and EMAIL_PASS:
        send_email(text)
    if NTFY_TOPIC:
        send_push(text)


# ---- Main --------------------------------------------------------------------
def check(n: int, only=None):
    for sym in ([only] if only else SYMBOLS):
        print("=" * 60)
        try:
            df = fetch(sym)
        except Exception as e:
            print(f"{sym}: ERROR {e}")
            continue
        events, bull, bear = compute(df)
        dec = PAIRS[sym]["dec"]
        src = "bitstamp" if PAIRS[sym].get("bs") and CRYPTO_SOURCE == "bitstamp" else DATA_SOURCE
        print(f"{sym} | Source={src} | {INTERVAL} | bars={len(df)} | last closed bar open: {loc(df.index[-1])}\n")
        print(f"Pichle {n} events (naye neeche):")
        for e in events[-n:]:
            o = e["ob"]
            extra = f" | candle {loc(o['left'])} | {zone(o, dec)}" if o else ""
            print(f"  {e['type']:<15} confirm {loc(e['time'] + STEP)}{extra}")
        print(active_summary(bull, bear, dec))


def load_state() -> dict:
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    if "last_time" in state:                      # purana single-symbol format
        state = {"EURUSD": state["last_time"]}
    return state


def main():
    state = load_state()
    for sym in SYMBOLS:
        try:
            df = fetch(sym)
            events, bull, bear = compute(df)
        except Exception as e:                    # ek symbol fail ho to baaki chalte rahein
            print(f"{sym}: ERROR {e}")
            continue

        last_bar = df.index[-1]
        last_seen = pd.Timestamp(state[sym]) if sym in state else None
        if last_seen is None:
            new = [e for e in events if e["time"] == last_bar]   # pehli run: spam nahi
        else:
            new = [e for e in events if e["time"] > last_seen]   # miss hue bars bhi

        if not SEND_MITIGATED:
            new = [e for e in new if e["type"] in ("ENTRY", "EXIT")]
        if SKIP_INVALID:
            def alive(e):
                active = bull if e["type"] == "ENTRY" else bear
                return any(e["ob"] is o for o in active)
            valid = [e for e in new if e["type"] not in ("ENTRY", "EXIT") or alive(e)]
            for e in new:
                if e not in valid:
                    print(f"{sym}: {e['type']} (candle {loc(e['ob']['left'])}) skip - box pehle hi mitigate ho chuka")
            new = valid
        for e in new:
            send(fmt(e, bull, bear, sym))
        if not new:
            print(f"{sym}: koi naya signal nahi | last closed bar open {loc(last_bar)} | "
                  f"active bull={len(bull)} bear={len(bear)}")
        state[sym] = last_bar.isoformat()

    STATE_FILE.write_text(json.dumps(state, indent=1))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        check(int(sys.argv[2]) if len(sys.argv) > 2 else 10,
              sys.argv[3].upper() if len(sys.argv) > 3 and sys.argv[3].upper() != "ALL" else None)
    else:
        main()
