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
