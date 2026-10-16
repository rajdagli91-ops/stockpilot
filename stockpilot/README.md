# StockPilot

A personal stock-analysis system for Indian equities, built around one goal: **20% CAGR with a drawdown you can sit through (25–30%)**. It runs on your free GitHub account, and Claude is optional.

## What it does

| | |
|---|---|
| **Top 5 picks** | Scans every NSE equity, analyses the liquid ones (≥ ₹2 Cr traded/day) on 14 parameters, and shows the 5 best BUYs, at most 2 per sector. Each pick gets reasons for and against, plus a Claude button. |
| **BSE movers & news** | From BSE's daily price file and announcements: stocks trading **3× their usual volume** or with **major news** (results, orders, acquisitions, bonus/split, buyback, fund raising) or **red flags** (auditor resignation, default, pledge, SEBI action). These stocks get fully analysed, including BSE-only stocks. NSE announcements are tried too, but NSE usually blocks cloud servers. |
| **Global markets** | US, Asia and Europe indices, US VIX, India VIX, US 10-year yield, dollar index, USD/INR, Brent, gold and copper. These give a global mood score, and each Indian sector gets a tailwind/headwind rating that counts in every stock's score. |
| **IPOs** | Open and upcoming IPOs, rated on sector strength (from listed peers in the scan), price vs listed peers, growth, profitability, debt, fresh money vs promoters selling, and SME risk. Each gets **Apply / Neutral / Avoid** with reasons, plus a Claude button. |
| **Portfolios** | As many named portfolios as you want ("Raj – Portfolio 1", "Raj – Portfolio 2", "Father – A49E068"). Upload a broker Excel/CSV or add stocks by hand. Each holding gets **Buy more / Hold / Hold–review / Exit** with reasons. Portfolios are saved in your browser, plus an optional **encrypted** cloud copy. |
| **Scheduled reports** | **Pre-market** (whole market, done by ~8:45), **1 PM** and **3 PM**. Each covers every portfolio: what to exit, what to add, signal changes, concentration warnings, IPO calls. Shown on the site, and optionally sent to Telegram and/or email. |
| **Fetch** | **My portfolios** (~3 min) or **Full market** (~45 min), whenever you want. |
| **Smart search** | Press `/`. Search by symbol, name, sector, or phrases like `buy pharma`. Any stock can be analysed on demand. |
| **Claude (optional)** | On any stock, pick or IPO, sends a ~150-word summary and caps the reply length. You use your own Anthropic API key, or **Copy prompt** and paste it into Claude yourself. |

## Schedule (Monday–Friday, IST)

| Starts | Arrives by | What runs |
|---|---|---|
| 07:15 | ~08:45 | **Pre-market**: global markets, full NSE scan, BSE volume spikes and news, IPOs, top 5 picks, portfolio report |
| 12:45 | ~13:00 | **1 PM**: fresh prices for your stocks + top candidates, today's BSE news, report |
| 14:45 | ~15:00 | **3 PM**: same as 1 PM |

Runs start early because GitHub can delay scheduled runs by 10–30 minutes at busy times. Prices come from Yahoo Finance; during market hours they can lag the exchange by about 15 minutes.

## Privacy

- **Public in the repo / site:** market data, scores, picks, IPO ratings.
- **Encrypted (AES-256, your passphrase):** your portfolios (`config/portfolios.enc.json`) and your reports (`data/report.enc.json`).
- **Never leaves your browser:** your GitHub token and Claude key.

Your passphrase is saved in two places only: your browser (Settings) and the GitHub secret `PORTFOLIO_KEY`. A free **public** repo is safe to use.

---

## Setup (about 20 minutes, no coding)

1. **Create the repo.** On github.com: **+ → New repository**, name it `stockpilot`, select **Public**, then **Create repository**.
2. **Upload the files.** Click **uploading an existing file** and drag in everything from the unzipped folder, including the hidden `.github` folder. Then **Commit changes**.
   - On a Mac, press `Cmd+Shift+.` in Finder to show hidden folders.
3. **Allow writes.** **Settings → Actions → General → Workflow permissions** → **Read and write permissions** → **Save**.
4. **Turn on the website.** **Settings → Pages → Source: GitHub Actions**.
5. **Add your passphrase as a secret.** **Settings → Secrets and variables → Actions → New repository secret**. Name it `PORTFOLIO_KEY`; the value is a passphrase of 12+ characters that you'll also type into the site.
6. **Optional: get reports on your phone or email.** Add these secrets the same way:
   - **Telegram:** create a bot with @BotFather and put its token in `TELEGRAM_TOKEN`. Send the bot a message, open `https://api.telegram.org/bot<token>/getUpdates`, and put the `chat.id` you see in `TELEGRAM_CHAT_ID`.
   - **Email (Gmail):** `SMTP_USER` = your Gmail address, `SMTP_PASS` = a Gmail **app password** (Google Account → Security → App passwords), `REPORT_EMAIL` = where to send.
7. **First full scan.** **Actions → Update scores → Run workflow** → mode **full** → **Run** (about 45 min). Your site is then live at `https://<username>.github.io/stockpilot/`.
8. **Token for the buttons.** On GitHub: avatar → **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
   - **Repository access:** Only select repositories → `stockpilot`.
   - **Permissions:** **Actions: Read and write**, **Contents: Read and write**.
9. **On the site, open Settings.**
   - Paste the token and click **Test connection**.
   - Enter the **same passphrase** as in step 5.
   - Optionally add your Anthropic API key.
10. **Add your portfolios.** Go to **Portfolios**, create and name each one, upload each broker file, then click **Save to cloud**. From then on every report includes them, and any device with the token and passphrase can **Load from cloud**.

---

## Everyday use

| You want to… | Do this |
|---|---|
| Today's best buys | **Top picks** |
| How the world set up the day | **Market**: global mood, sector tailwinds, BSE news and volume spikes |
| Should I apply for an IPO? | **IPOs** |
| Check any stock | Press `/`, type a name or symbol |
| Fresh data now | **Fetch ▾ → My portfolios** or **Full market** |
| Today's reports | **Portfolios → Scheduled reports** (or Telegram/email) |
| Second opinion | **Analyse with Claude** on any stock, pick or IPO |
| Analyse an IPO properly | Add a row to `config/ipos.csv` with figures from its prospectus (RHP); the next run rates it |
| Change weights, thresholds, scan limits | `config/settings.json` |

## How a stock is rated

There are 14 parameters, each rated 0–100 and combined by weight. The 20% hurdle (expected return from growth, dividend yield and the P/E moving toward a fair level) carries the most weight.

- **Fundamentals:** expected return vs 20%, profit growth, revenue growth, ROE, debt/equity (skipped for banks), P/E ÷ growth, cash conversion.
- **Technicals:** trend vs 200-day average, 12-month return vs Nifty, RSI, volatility vs your drawdown limit.
- **News & filings:** headline tone over 30 days, red flags in BSE/NSE filings and news.
- **Global markets:** tailwind or headwind for the stock's sector.

**Signals:**
- **BUY:** score ≥ 66 and hurdle rating ≥ 60.
- **SELL / EXIT:** score < 45, or a weak hurdle with a falling trend.
- **HOLD:** everything else.

**For your holdings:** a BUY becomes **Buy more** (or **Hold** if it's already over 8% of the portfolio). A HOLD with expected return under 12% becomes **Hold / review**.

**Confidence** reflects how much data was found and whether fundamentals and technicals agree. It's not a probability. This is a rules-based screen to decide what to research, not investment advice.

## Known limits of free data

- **Yahoo Finance fundamentals** for Indian stocks are sometimes missing or a year old, especially for small caps and banks. Missing data lowers confidence; it doesn't count against the stock.
- **NSE** blocks most automated access from cloud servers. BSE files and announcements usually work, and most NSE companies also file on BSE. If a source fails, the run continues without it, and the log says what was skipped.
- **IPOs:** NSE's IPO list may be blocked, and no free feed provides prospectus financials. Add those in `config/ipos.csv`.
- **News tone** uses keyword matching on headlines. Read the headlines yourself.
- **Paused schedules:** GitHub pauses scheduled runs in repos with no activity for 60 days. Re-enable them under **Actions** if that happens.
- **Paid data later:** every source sits in `engine/providers/`. A paid provider (e.g. Screener Premium) can be added there without changing the scoring or the site.

## Run locally (optional)

```bash
pip install -r requirements.txt
python -m engine.run full --slot Pre-market   # needs internet, ~45 min
python -m engine.run portfolio                # your stocks only (set PORTFOLIO_KEY to read portfolios)
python -m tests.sample_run full               # offline sample data
cd docs && python -m http.server 8000         # http://localhost:8000
```
