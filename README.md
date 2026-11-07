# UNKOWN BADSHA - Python + GitHub Actions (EURUSD, GBPUSD, USDJPY, BTCUSD, ETHUSD - 15m)

TradingView Pine v5 "Volume Order Blocks" indicator ka Python port.
- **ENTRY** = bullish OB, **EXIT** = bearish OB, plus mitigation alerts
- 15m candle close hone ke baad (5 min ke andar) **Email** aata hai
- Mail mein: box wali candle ka time, signal confirm hone ka time (IST), zone, aur abhi active boxes

## Setup
1. Repo GitHub par push karo
2. Gmail: 2-Step Verification on -> https://myaccount.google.com/apppasswords se App Password banao
3. Settings -> Secrets and variables -> Actions: `EMAIL_USER`, `EMAIL_PASS` (App Password), `EMAIL_TO`
4. Actions -> **Check vs TradingView chart** -> Run workflow -> output apne chart se compare karo
5. Sahi lage to **UNKOWN BADSHA** workflow chalne do

## Time ka matlab
- **Chart par label/box candle**: jahan TradingView par EXIT/ENTRY label dikhta hai (volume pivot wali candle)
- **Signal confirm hua**: pivot `LENGTH` (5) candle baad confirm hota hai; alert isi candle ke close par aata hai
- Time default IST (`TIMEZONE`), candle ka **open** time (TradingView ke axis jaisa)

## Data source
| Source | Kaisa |
|---|---|
| `dukascopy` (default) | Free, stable. Tick volume Dukascopy ka, FXCM se thoda alag -> kuch boxes alag aa sakte hain |
| `tradingview` | Unofficial (tvdatafeed). FXCM ka wahi volume jo chart par hai. Kabhi bhi band/block ho sakta hai |

Indicator volume pivot par chalta hai, isliye volume wala source zaroori hai (Yahoo spot EURUSD mein volume nahi).

## Limits
- Actions cron minimum 5 min, runs late ho sakte hain
- Private repo mein free minutes jaldi khatam hote hain; public repo unlimited

## Symbols
`run.yml` mein `SYMBOLS: EURUSD,GBPUSD,USDJPY,BTCUSD,ETHUSD`. Kam karne ke liye list se naam hata do.
Naya symbol jodne ke liye `indicator.py` ke `PAIRS` mein entry add karo. State har symbol ka alag `state.json` mein rehta hai.

## Crypto data (BTCUSD, ETHUSD)
Default `CRYPTO_SOURCE=bitstamp`: Bitstamp ka official free OHLC API, TradingView ke BITSTAMP chart wala hi data aur volume.
Dukascopy ka crypto volume alag hota hai, isliye boxes chart se match nahi karte. Default mein sirf ENTRY/EXIT mail aati hai. Mitigation ki mail chahiye to `SEND_MITIGATED: "1"`.

## Phone push notification (ntfy)
ntfy app install karo, ek lamba random topic naam subscribe karo, aur wahi naam GitHub secret `NTFY_TOPIC` mein daalo.
Topic naam public hota hai (jo jaane wo padh sakta hai), isliye lamba aur random rakho.
