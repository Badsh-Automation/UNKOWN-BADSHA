"""UNKOWN BADSHA - Volume Order Block indicator (Pine v5 -> Python).

Har run par: data fetch -> last CLOSED bar tak calculate -> naye ENTRY / EXIT /
MITIGATED events Telegram par bhejo. Duplicate se bachne ke liye state.json use hota hai.
"""
import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ---- Settings (env se override ho sakti hain) -------------------------------
SYMBOL = os.getenv("SYMBOL", "GC=F")        # Gold futures (volume milta hai)
INTERVAL = os.getenv("INTERVAL", "5m")      # 1m, 5m, 15m, 1h ...
PERIOD = os.getenv("PERIOD", "5d")
LENGTH = int(os.getenv("LENGTH", "5"))      # Volume Pivot Length
MITIGATION = os.getenv("MITIGATION", "Wick")  # "Wick" ya "Close"
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT = os.getenv("CHAT_ID")
# Email (Gmail: App Password use karo, normal password nahi)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
EMAIL_TO = os.getenv("EMAIL_TO") or EMAIL_USER
STATE_FILE = Path(__file__).parent / "state.json"

INTERVAL_MIN = {"1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30, "60m": 60, "1h": 60, "90m": 90}


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
        # os := high[length] > upper ? 0 : low[length] < lower ? 1 : os[1]
        if i >= length and not np.isnan(upper[i]):
            if h[i - length] > upper[i]:
                os_state = 0
            elif l[i - length] < lower[i]:
                os_state = 1

        # Naya order block
        if not np.isnan(phv[i]):
            j = i - length
            if os_state == 1:
                ob = dict(top=hl2[j], btm=l[j], avg=(hl2[j] + l[j]) / 2, left=t[j])
                bull_obs.insert(0, ob)
                events.append(dict(i=i, time=t[i], type="ENTRY", ob=ob))
            else:
                ob = dict(top=h[j], btm=hl2[j], avg=(h[j] + hl2[j]) / 2, left=t[j])
                bear_obs.insert(0, ob)
                events.append(dict(i=i, time=t[i], type="EXIT", ob=ob))

        # Mitigation
        if not np.isnan(tgt_bull[i]):
            keep = [o for o in bull_obs if not tgt_bull[i] < o["btm"]]
            if len(keep) != len(bull_obs):
                events.append(dict(i=i, time=t[i], type="BULL_MITIGATED", ob=None))
            bull_obs = keep
        if not np.isnan(tgt_bear[i]):
            keep = [o for o in bear_obs if not tgt_bear[i] > o["top"]]
            if len(keep) != len(bear_obs):
                events.append(dict(i=i, time=t[i], type="BEAR_MITIGATED", ob=None))
            bear_obs = keep

    return events, bull_obs, bear_obs


# ---- Data / alerts -----------------------------------------------------------
def fetch() -> pd.DataFrame:
    df = yf.download(SYMBOL, period=PERIOD, interval=INTERVAL, progress=False, auto_adjust=False)
    if df.empty:
        sys.exit("Data nahi mila (symbol/interval check karo)")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df.index = pd.to_datetime(df.index, utc=True)
    # Abhi chalti hui (incomplete) bar hatao -> repaint se bachne ke liye
    step = pd.Timedelta(minutes=INTERVAL_MIN.get(INTERVAL, 5))
    if pd.Timestamp.now(tz="UTC") < df.index[-1] + step:
        df = df.iloc[:-1]
    return df


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
    print(text)
    if EMAIL_USER and EMAIL_PASS:
        send_email(text)
    if TG_TOKEN and TG_CHAT:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT, "text": text},
            timeout=15,
        )
        if not r.ok:
            print("Telegram error:", r.text)


def fmt(ev) -> str:
    ts = ev["time"].strftime("%Y-%m-%d %H:%M UTC")
    if ev["type"] == "ENTRY":
        o = ev["ob"]
        return (f"🟢 UNKOWN BADSHA: ENTRY (bullish OB)\n{SYMBOL} {INTERVAL} | {ts}\n"
                f"Zone: {o['btm']:.2f} - {o['top']:.2f} | Avg: {o['avg']:.2f}")
    if ev["type"] == "EXIT":
        o = ev["ob"]
        return (f"🟠 UNKOWN BADSHA: EXIT (bearish OB)\n{SYMBOL} {INTERVAL} | {ts}\n"
                f"Zone: {o['btm']:.2f} - {o['top']:.2f} | Avg: {o['avg']:.2f}")
    label = "Bullish" if ev["type"] == "BULL_MITIGATED" else "Bearish"
    return f"⚪ {label} order block mitigated\n{SYMBOL} {INTERVAL} | {ts}"


def main():
    df = fetch()
    events, bull, bear = compute(df)

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    last_seen = pd.Timestamp(state["last_time"]) if "last_time" in state else None
    last_bar = df.index[-1]

    if last_seen is None:
        new = [e for e in events if e["time"] == last_bar]   # pehli run: spam nahi
    else:
        new = [e for e in events if e["time"] > last_seen]   # beech ke miss hue bars bhi

    for e in new:
        send(fmt(e))
    if not new:
        print(f"Koi naya signal nahi | last closed bar {last_bar} | "
              f"active bull={len(bull)} bear={len(bear)}")

    STATE_FILE.write_text(json.dumps({"last_time": last_bar.isoformat()}))


if __name__ == "__main__":
    main()
