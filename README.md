# 🤖 Jarvis MVP - Trading Risk Monitor

Real-time advisory alerts for Binance Futures trading. Monitors your positions 24/7 and sends Telegram notifications when risks are detected.

## ✨ Features

### 🚨 4 Core Risk Rules

1. **High Risk Alert** - Position risk exceeds 2%
2. **Liquidation Risk** - Distance to liquidation < 5%
3. **No Stop Loss** - Position opened without stop loss
4. **Revenge Pattern** - Emotional trading detected

### 📱 Telegram Alerts

- Real-time notifications with action buttons
- Acknowledge, Cooldown, or Reduce size
- Daily recap at 20:00 UTC
- Discipline score tracking

### 🎯 Advisory Only

- No blocking or forced actions
- Smart recommendations
- Gamified discipline scoring
- Build better habits

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL
- Binance Futures Testnet account
- Telegram account

### 1. Clone Repository
```bash
git clone https://github.com/YOUR_USERNAME/jarvis-mvp.git
cd jarvis-mvp
```

### 2. Install Dependencies
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Setup PostgreSQL
```bash
# Install PostgreSQL
# Ubuntu/Debian
sudo apt install postgresql postgresql-contrib

# macOS
brew install postgresql
brew services start postgresql

# Create database
createdb jarvis
```

### 4. Get API Keys

#### Binance Testnet
1. Go to https://testnet.binancefuture.com
2. Register (no email needed)
3. API Management → Create API Key
4. Enable: Reading, Futures
5. Disable: Withdrawals, Transfer

#### Telegram Bot
1. Open Telegram, search `@BotFather`
2. Send `/newbot`
3. Follow instructions
4. Copy bot token

### 5. Configure Environment

Create `.env` file:
```bash
# Binance API (testnet)
BINANCE_API_KEY=your_api_key_here
BINANCE_API_SECRET=your_secret_here
BINANCE_TESTNET=True

# Telegram Bot
TELEGRAM_BOT_TOKEN=your_bot_token_here

# Database
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/jarvis

# Rules (optional - defaults are fine)
MAX_RISK_PCT=2.0
MIN_LIQ_DISTANCE_PCT=5.0
NO_SL_TIMEOUT_MINUTES=3
REVENGE_WINDOW_MINUTES=15
COOLDOWN_MINUTES=30
```

### 6. Run!
```bash
python main.py
```

Server starts on http://localhost:8000

---

## 📖 Usage

### Find Your Telegram ID

1. Message `@userinfobot` on Telegram
2. Copy the ID number

### Register User
```bash
curl -X POST http://localhost:8000/users/register \
  -H "Content-Type: application/json" \
  -d '{
    "telegram_id": YOUR_TELEGRAM_ID,
    "binance_api_key": "YOUR_KEY",
    "binance_api_secret": "YOUR_SECRET",
    "telegram_username": "your_username"
  }'
```

### Telegram Commands

- `/start` - Initialize bot
- `/status` - Check positions
- `/score` - View discipline score
- `/help` - Get help

### Test Alert
```bash
curl -X POST "http://localhost:8000/test/alert?telegram_id=YOUR_TELEGRAM_ID"
```

---

## 🏗️ Architecture
```
┌─────────────────────────────────────────┐
│         FastAPI Server (main.py)        │
│                                         │
│  ┌──────────────────────────────────┐  │
│  │   Background Monitor (15s poll)  │  │
│  └────────────┬─────────────────────┘  │
│               │                         │
│               v                         │
│  ┌──────────────────────────────────┐  │
│  │   Rule Engine (rule_engine.py)   │  │
│  │   • High Risk                    │  │
│  │   • Liquidation Risk             │  │
│  │   • No Stop Loss                 │  │
│  │   • Revenge Pattern              │  │
│  └────────────┬─────────────────────┘  │
│               │                         │
│               v                         │
│  ┌──────────────────────────────────┐  │
│  │ Telegram Bot (telegram_bot.py)   │  │
│  │   • Send alerts                  │  │
│  │   • Action buttons               │  │
│  │   • Daily recap                  │  │
│  └──────────────────────────────────┘  │
└─────────────────────────────────────────┘
         │                    │
         v                    v
  ┌──────────┐        ┌──────────────┐
  │PostgreSQL│        │Binance Futures│
  │ Database │        │   Testnet    │
  └──────────┘        └──────────────┘
```

---

## 📁 Project Structure
```
jarvis-mvp/
├── .env                    # Environment variables (API keys)
├── .gitignore             # Git ignore rules
├── requirements.txt        # Python dependencies
├── README.md              # This file
├── config.py              # Settings and rule configurations
├── database.py            # Database connection
├── models.py              # SQLAlchemy models
├── binance_client.py      # Binance API wrapper
├── rule_engine.py         # Risk detection logic
├── telegram_bot.py        # Telegram bot handlers
├── scheduler.py           # Daily recap scheduler
└── main.py                # FastAPI app + orchestration
```

---

## 🔧 API Endpoints

- `GET /` - Health check
- `GET /health` - Detailed system health
- `POST /users/register` - Register new user
- `GET /users/{telegram_id}/alerts` - Get alert history
- `GET /users/{telegram_id}/score` - Get discipline score
- `GET /users/{telegram_id}/positions` - Get current positions
- `POST /test/alert` - Send test alert
- `GET /stats` - System statistics

---

## 🎮 Configuration

Edit `.env` to customize:
```bash
MAX_RISK_PCT=2.0              # Max position risk %
MIN_LIQ_DISTANCE_PCT=5.0      # Min liquidation distance %
NO_SL_TIMEOUT_MINUTES=3       # Alert if no SL after X minutes
REVENGE_WINDOW_MINUTES=15     # Timeframe to detect revenge trading
COOLDOWN_MINUTES=30           # Suggested cooldown period
POLL_INTERVAL_SECONDS=15      # Position check frequency
DAILY_RECAP_HOUR=20           # UTC hour for daily recap
```

---

## 📊 Discipline Score

**Formula:**
```
Score = 100 - (violations × 5) + (positive_actions × 2)
```

**Tiers:**
- 🏆 90-100: Diamond (Excellent)
- 💎 75-89: Platinum (Good)
- 🥈 60-74: Silver (Careful)
- 🥉 40-59: Bronze (Warning)
- ⚠️ 0-39: Alert (Critical)

**Earn Points:**
- 🧊 Cooldown 30m: +5
- 🛡️ Setting SL: +5
- 📉 Reduce size: +3
- 💰 Adding margin: +3

---

## 🐛 Troubleshooting

### "Database connection failed"
- Check PostgreSQL is running
- Verify DATABASE_URL in `.env`
- Ensure database exists: `createdb jarvis`

### "Binance API error"
- Verify API keys in `.env`
- Check testnet mode: `BINANCE_TESTNET=True`
- Ensure API has Futures permission

### "Telegram bot not responding"
- Verify TELEGRAM_BOT_TOKEN
- Start bot in Telegram: `/start`
- Check server logs

### "No alerts received"
- Open position on testnet.binancefuture.com
- Wait 15 seconds for poll
- Check server logs for errors

---

## 🚀 Deployment

### Railway (Recommended)

1. Install Railway CLI: `npm install -g @railway/cli`
2. Login: `railway login`
3. Initialize: `railway init`
4. Add PostgreSQL: `railway add`
5. Deploy: `railway up`

### Render

1. Push code to GitHub
2. Go to render.com
3. "New Web Service"
4. Connect GitHub repo
5. Add environment variables
6. Deploy!

---

## 🔐 Security

**⚠️ IMPORTANT:**
- Never commit `.env` to git
- Use encrypted secrets in production
- Rotate API keys regularly
- Limit API permissions (no withdrawals!)

---

## 📝 TODO / Roadmap

- [ ] Web dashboard UI
- [ ] Multiple exchange support
- [ ] Custom rule builder
- [ ] Mobile app
- [ ] Advanced analytics
- [ ] Social features (leaderboard)

---

## 🤝 Contributing

This is an MVP. Feedback welcome!

---

## 📄 License

MIT License - Do whatever you want with it!

---

## 🙏 Credits

Built with:
- FastAPI
- python-telegram-bot
- python-binance
- SQLAlchemy
- PostgreSQL

---

**Happy Trading! 🚀**

Built to protect capital, one alert at a time.
```

**"Commit new file"** 클릭

---

# 🎉 **완료!!!**

## ✅ 11/11 파일 전부 업로드!
```
✅ requirements.txt
✅ .gitignore
✅ config.py
✅ models.py
✅ database.py
✅ binance_client.py
✅ rule_engine.py
✅ telegram_bot.py
✅ scheduler.py
✅ main.py
✅ README.md
