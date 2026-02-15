"""
Jarvis MVP - Main Application
FastAPI server with background position monitoring
"""
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager
import asyncio
from datetime import datetime
from typing import Dict, List

from config import settings
from database import init_db, get_db_dependency
from models import User, Alert, ButtonClick
from binance_client import BinanceClient
from rule_engine import RuleEngine
from telegram_bot import JarvisTelegramBot
from scheduler import JarvisScheduler

# Global instances
telegram_bot = None
scheduler = None
monitoring_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    global telegram_bot, scheduler, monitoring_task
    
    print("🚀 Starting Jarvis MVP...")
    
    # Initialize database
    init_db()
    print("✅ Database initialized")
    
    # Initialize Telegram bot
    telegram_bot = JarvisTelegramBot()
    print("✅ Telegram bot initialized")
    
    # Initialize scheduler
    scheduler = JarvisScheduler(telegram_bot)
    await scheduler.start()
    print("✅ Scheduler started")
    
    # Start background monitoring
    monitoring_task = asyncio.create_task(background_monitor())
    print("✅ Background monitoring started")
    
    # Start Telegram bot in background
    bot_task = asyncio.create_task(run_telegram_bot())
    print("✅ Telegram bot running")
    
    print("\n" + "="*50)
    print("🤖 Jarvis is now protecting your trades!")
    print("="*50 + "\n")
    
    yield
    
    # Cleanup
    print("\n🛑 Shutting down Jarvis...")
    scheduler.stop()
    monitoring_task.cancel()
    bot_task.cancel()
    print("✅ Shutdown complete")


# FastAPI app
app = FastAPI(
    title="Jarvis Risk Monitor",
    description="Real-time trading risk alerts for Binance Futures",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def run_telegram_bot():
    """Run Telegram bot in background"""
    try:
        await telegram_bot.app.initialize()
        await telegram_bot.app.start()
        await telegram_bot.app.updater.start_polling()
        
        # Keep running
        while True:
            await asyncio.sleep(1)
    
    except asyncio.CancelledError:
        print("🛑 Telegram bot stopped")
        await telegram_bot.app.stop()
    except Exception as e:
        print(f"❌ Telegram bot error: {e}")


async def background_monitor():
    """
    Background task: Monitor all users' positions every 15 seconds
    """
    print(f"👀 Monitoring loop started (interval: {settings.POLL_INTERVAL_SECONDS}s)")
    
    while True:
        try:
            await asyncio.sleep(settings.POLL_INTERVAL_SECONDS)
            
            # Get all active users
            with get_db_dependency() as db:
                users = db.query(User).filter(User.is_active == True).all()
                
                print(f"\n⏰ [{datetime.utcnow().strftime('%H:%M:%S')}] Checking {len(users)} user(s)...")
                
                for user in users:
                    try:
                        await check_user_positions(user, db)
                    except Exception as e:
                        print(f"❌ Error checking user {user.telegram_id}: {e}")
        
        except asyncio.CancelledError:
            print("🛑 Monitoring loop stopped")
            break
        except Exception as e:
            print(f"❌ Monitoring error: {e}")
            await asyncio.sleep(5)


async def check_user_positions(user: User, db: Session):
    """
    Check a single user's positions and send alerts if needed
    """
    try:
        # Initialize Binance client for this user
        client = BinanceClient(
            api_key=user.binance_api_key,
            api_secret=user.binance_api_secret,
            testnet=settings.BINANCE_TESTNET
        )
        
        # Get positions
        positions = client.get_positions()
        
        if not positions:
            return
        
        # Initialize rule engine
        engine = RuleEngine(client)
        
        # Check each position
        for position in positions:
            alerts = engine.check_all_rules(position)
            
            for alert in alerts:
                # Save alert to database
                db_alert = Alert(
                    alert_id=alert['alert_id'],
                    user_id=user.id,
                    rule_type=alert['rule_type'],
                    symbol=alert['symbol'],
                    side=alert['side'],
                    position_size=alert['size'],
                    entry_price=alert['entry_price'],
                    mark_price=alert['mark_price'],
                    leverage=alert['leverage'],
                    risk_pct=alert['risk_pct'],
                    liq_distance_pct=alert['liq_distance_pct'],
                    has_stop_loss=alert['has_stop_loss'],
                    position_snapshot=alert['position_snapshot'],
                    triggered_at=alert['triggered_at']
                )
                db.add(db_alert)
                db.commit()
                
                # Send Telegram alert
                message_id = await telegram_bot.send_alert(
                    telegram_id=user.telegram_id,
                    alert=alert
                )
                
                if message_id:
                    db_alert.telegram_message_id = message_id
                    db.commit()
                    
                    print(f"  🚨 Alert sent: {alert['rule_name']} - {alert['symbol']}")
        
        # Check revenge pattern
        revenge_alert = engine.check_revenge_pattern(user_id=user.id)
        
        if revenge_alert:
            # Save to database
            db_alert = Alert(
                alert_id=revenge_alert['alert_id'],
                user_id=user.id,
                rule_type=revenge_alert['rule_type'],
                symbol='SYSTEM',
                side='',
                position_size=0,
                position_snapshot=revenge_alert.get('details', {}),
                triggered_at=revenge_alert['triggered_at']
            )
            db.add(db_alert)
            db.commit()
            
            # Send Telegram alert
            message_id = await telegram_bot.send_alert(
                telegram_id=user.telegram_id,
                alert=revenge_alert
            )
            
            if message_id:
                db_alert.telegram_message_id = message_id
                db.commit()
                
                print(f"  🧠 Revenge pattern alert sent")
        
        # Update user's last_seen
        user.last_seen = datetime.utcnow()
        db.commit()
    
    except Exception as e:
        print(f"  ❌ Error checking user {user.telegram_id}: {e}")


# ==================== API ENDPOINTS ====================

@app.get("/")
async def root():
    """Health check"""
    return {
        "status": "running",
        "service": "Jarvis Risk Monitor",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/health")
async def health_check():
    """Detailed health check"""
    return {
        "status": "healthy",
        "database": "connected",
        "telegram_bot": "running" if telegram_bot else "not initialized",
        "monitoring": "active" if monitoring_task else "inactive",
        "settings": {
            "poll_interval": settings.POLL_INTERVAL_SECONDS,
            "max_risk": settings.MAX_RISK_PCT,
            "min_liq_distance": settings.MIN_LIQ_DISTANCE_PCT
        }
    }


@app.post("/users/register")
async def register_user(
    telegram_id: int,
    binance_api_key: str,
    binance_api_secret: str,
    telegram_username: str = None,
    db: Session = Depends(get_db_dependency)
):
    """Register a new user"""
    # Check if user exists
    existing = db.query(User).filter(User.telegram_id == telegram_id).first()
    
    if existing:
        raise HTTPException(status_code=400, detail="User already registered")
    
    # Validate Binance credentials
    try:
        client = BinanceClient(
            api_key=binance_api_key,
            api_secret=binance_api_secret,
            testnet=settings.BINANCE_TESTNET
        )
        balance = client.get_account_balance()
        
        if balance == 0:
            raise Exception("Invalid API credentials")
    
    except Exception as e:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid Binance API credentials: {str(e)}"
        )
    
    # Create user
    user = User(
        telegram_id=telegram_id,
        telegram_username=telegram_username,
        binance_api_key=binance_api_key,
        binance_api_secret=binance_api_secret,
        max_risk_pct=settings.MAX_RISK_PCT,
        min_liq_distance_pct=settings.MIN_LIQ_DISTANCE_PCT
    )
    
    db.add(user)
    db.commit()
    db.refresh(user)
    
    return {
        "success": True,
        "user_id": user.id,
        "message": "User registered successfully"
    }


@app.get("/users/{telegram_id}/alerts")
async def get_user_alerts(
    telegram_id: int,
    limit: int = 50,
    db: Session = Depends(get_db_dependency)
):
    """Get recent alerts for a user"""
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    alerts = db.query(Alert)\
        .filter(Alert.user_id == user.id)\
        .order_by(Alert.triggered_at.desc())\
        .limit(limit)\
        .all()
    
    return {
        "user_id": user.id,
        "telegram_id": telegram_id,
        "total_alerts": len(alerts),
        "alerts": [
            {
                "alert_id": a.alert_id,
                "rule_type": a.rule_type,
                "symbol": a.symbol,
                "side": a.side,
                "risk_pct": a.risk_pct,
                "triggered_at": a.triggered_at.isoformat(),
                "is_acknowledged": a.is_acknowledged
            }
            for a in alerts
        ]
    }


@app.get("/users/{telegram_id}/score")
async def get_discipline_score(
    telegram_id: int,
    db: Session = Depends(get_db_dependency)
):
    """Get user's discipline score"""
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Calculate score
    score = telegram_bot._calculate_discipline_score(user.id, db)
    
    from config import get_score_tier
    badge, status = get_score_tier(score)
    
    return {
        "user_id": user.id,
        "telegram_id": telegram_id,
        "score": score,
        "badge": badge,
        "status": status
    }


@app.get("/users/{telegram_id}/positions")
async def get_current_positions(
    telegram_id: int,
    db: Session = Depends(get_db_dependency)
):
    """Get user's current Binance positions"""
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    try:
        client = BinanceClient(
            api_key=user.binance_api_key,
            api_secret=user.binance_api_secret,
            testnet=settings.BINANCE_TESTNET
        )
        
        positions = client.get_positions()
        balance = client.get_account_balance()
        
        return {
            "user_id": user.id,
            "telegram_id": telegram_id,
            "balance": balance,
            "positions_count": len(positions),
            "positions": positions
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching positions: {str(e)}"
        )


@app.post("/test/alert")
async def send_test_alert(
    telegram_id: int,
    db: Session = Depends(get_db_dependency)
):
    """Send a test alert"""
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Create test alert
    test_alert = {
        'alert_id': f'alert_test_{int(datetime.utcnow().timestamp())}',
        'rule_type': 'high_risk',
        'rule_name': 'High Risk Alert (TEST)',
        'emoji': '⚠️',
        'symbol': 'BTCUSDT',
        'side': 'Long',
        'size': 0.1,
        'message': 'This is a test alert',
        'suggestion': 'No action needed - this is just a test',
        'risk_pct': 3.5,
        'liq_distance_pct': 6.2,
        'leverage': 10,
        'unrealized_pnl': 15.50,
        'triggered_at': datetime.utcnow()
    }
    
    # Send via Telegram
    message_id = await telegram_bot.send_alert(
        telegram_id=telegram_id,
        alert=test_alert
    )
    
    return {
        "success": True,
        "message_id": message_id,
        "alert": test_alert
    }


@app.get("/stats")
async def get_stats(db: Session = Depends(get_db_dependency)):
    """Get overall system statistics"""
    total_users = db.query(User).count()
    active_users = db.query(User).filter(User.is_active == True).count()
    total_alerts = db.query(Alert).count()
    
    from datetime import timedelta
    today = datetime.utcnow().date()
    today_start = datetime.combine(today, datetime.min.time())
    
    alerts_today = db.query(Alert).filter(Alert.triggered_at >= today_start).count()
    
    return {
        "total_users": total_users,
        "active_users": active_users,
        "total_alerts": total_alerts,
        "alerts_today": alerts_today,
        "uptime": "running"
    }


if __name__ == "__main__":
    import uvicorn
    
    print("""
    ╔══════════════════════════════════════╗
    ║                                      ║
    ║        🤖 JARVIS MVP v1.0           ║
    ║    Trading Risk Monitor              ║
    ║                                      ║
    ╚══════════════════════════════════════╝
    
    Starting server...
    """)
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
