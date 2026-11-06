# UNKOWN BADSHA - Python + GitHub Actions

TradingView Pine v5 "Volume Order Blocks" indicator ka Python port.
- **ENTRY** = bullish OB bana, **EXIT** = bearish OB bana, plus mitigation alerts
- Har 5 minute par GitHub Actions chalta hai, **Email** par alert bhejta hai (Telegram optional)

## Setup
1. Repo GitHub par push karo
2. Gmail mein 2-Step Verification on karo, phir https://myaccount.google.com/apppasswords se **App Password** banao
3. Settings -> Secrets and variables -> Actions -> ye secrets add karo:
   - `EMAIL_USER` = tumhara gmail (jisse bhejna hai)
   - `EMAIL_PASS` = 16-character App Password
   - `EMAIL_TO` = jis mail par alert chahiye (khali chhodo to EMAIL_USER par jayega)
4. Actions tab -> "UNKOWN BADSHA" -> Run workflow (test)

## Settings (workflow ke `env` mein)
| Var | Default | Matlab |
|---|---|---|
| SYMBOL | GC=F | yfinance symbol |
| INTERVAL | 5m | candle timeframe |
| LENGTH | 5 | Volume Pivot Length |
| MITIGATION | Wick | Wick / Close |

## Dhyan rakho
- Indicator **volume** use karta hai. `XAUUSD=X` (spot forex) mein volume nahi hota, isliye `GC=F` rakha hai. Spot gold ke liye broker/MT5 data lagana padega.
- Actions ka cron minimum 5 min hai aur runs late ho sakte hain.
- Sirf **closed** bar par signal aata hai (TradingView ke chart par live bar par box badal sakta hai).
- Pehle TradingView chart se 2-3 signals match karke verify kar lo.
- Local test: `pip install -r requirements.txt && python indicator.py`
