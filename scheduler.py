"""
Jarvis MVP - Scheduler
Daily recap and periodic tasks
"""
import asyncio
from datetime import datetime, time
from database import get_db
from models import User


class JarvisScheduler:
    """Schedule periodic tasks like daily recap"""
    
    def __init__(self, telegram_bot):
        self.telegram_bot = telegram_bot
        self.running = False
    
    async def start(self):
        """Start all scheduled tasks"""
        self.running = True
        print("📅 Scheduler started")
        
        # Start daily recap task
        asyncio.create_task(self.daily_recap_loop())
    
    def stop(self):
        """Stop scheduler"""
        self.running = False
        print("📅 Scheduler stopped")
    
    async def daily_recap_loop(self):
        """
        Send daily recap at 20:00 UTC
        Runs in a loop, checking every minute
        """
        print(f"📊 Daily recap scheduled for 20:00 UTC")
        
        while self.running:
            try:
                now = datetime.utcnow()
                
                # Check if it's 20:00 UTC (within 1 minute window)
                if now.hour == 20 and now.minute == 0:
                    print(f"\n⏰ Daily recap time! Sending to all users...")
                    await self.send_daily_recaps()
                    
                    # Sleep for 2 minutes to avoid sending multiple times
                    await asyncio.sleep(120)
                else:
                    # Check every minute
                    await asyncio.sleep(60)
            
            except Exception as e:
                print(f"❌ Error in daily recap loop: {e}")
                await asyncio.sleep(60)
    
    async def send_daily_recaps(self):
        """Send daily recap to all active users"""
        try:
            with get_db() as db:
                users = db.query(User).filter(User.is_active == True).all()
                
                print(f"   Sending recap to {len(users)} user(s)...")
                
                for user in users:
                    try:
                        await self.telegram_bot.send_daily_recap(
                            telegram_id=user.telegram_id,
                            user_id=user.id
                        )
                        print(f"   ✅ Sent to user {user.telegram_id}")
                        
                        # Small delay between sends
                        await asyncio.sleep(0.5)
                    
                    except Exception as e:
                        print(f"   ❌ Failed for user {user.telegram_id}: {e}")
                
                print(f"✅ Daily recap complete!")
        
        except Exception as e:
            print(f"❌ Error sending daily recaps: {e}")
