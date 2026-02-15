"""
Jarvis MVP - Telegram Bot
Send alerts with action buttons and handle user interactions
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode
from typing import Dict, List
from datetime import datetime
from config import settings, BUTTON_CONFIGS, get_score_tier, RULES
from database import get_db
from models import User, Alert, ButtonClick, DisciplineScore
import json


class JarvisTelegramBot:
    """Telegram bot for sending alerts and handling user actions"""
    
    def __init__(self):
        self.app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
        self._setup_handlers()
    
    def _setup_handlers(self):
        """Register command and callback handlers"""
        # Commands
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("score", self.cmd_score))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        
        # Button callbacks
        self.app.add_handler(CallbackQueryHandler(self.handle_button_click))
    
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        
        welcome_msg = f"""
🤖 **Jarvis Risk Monitor**

Hey {user.first_name}! I'm your trading guardian angel.

I'll monitor your Binance Futures positions 24/7 and alert you when:
⚠️ Risk exceeds {settings.MAX_RISK_PCT}%
🔴 Liquidation gets too close
🛡️ You forget to set stop loss
🧠 Revenge trading patterns detected

**Quick Commands:**
/status - Check current positions
/score - View discipline score
/help - Get help

Ready to protect your capital! 🚀
        """
        
        await update.message.reply_text(
            welcome_msg,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show current positions status"""
        await update.message.reply_text(
            "📊 Fetching your positions...\n\n"
            "(This will show real-time position data once connected)"
        )
    
    async def cmd_score(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show discipline score"""
        telegram_id = update.effective_user.id
        
        try:
            with get_db() as db:
                user = db.query(User).filter(User.telegram_id == telegram_id).first()
                
                if not user:
                    await update.message.reply_text(
                        "❌ User not found. Please register first with /start"
                    )
                    return
                
                # Calculate current score
                score = self._calculate_discipline_score(user.id, db)
                badge, status = get_score_tier(score)
                
                score_msg = f"""
📊 **Discipline Score**

{badge} **{score:.0f}/100** - {status}

🎯 Keep it up! Higher scores unlock better insights.

_Score updates daily based on:_
• Alert acknowledgments
• Positive actions taken
• Rule violations avoided
                """
                
                await update.message.reply_text(
                    score_msg,
                    parse_mode=ParseMode.MARKDOWN
                )
        
        except Exception as e:
            print(f"❌ Error in /score command: {e}")
            await update.message.reply_text("⚠️ Error fetching score. Try again later.")
    
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show help message"""
        help_msg = """
🆘 **Jarvis Help**

**How It Works:**
I monitor your Binance Futures positions every 15 seconds and alert you when risks are detected.

**Alert Types:**
⚠️ **High Risk** - Position risk exceeds limit
🔴 **Liquidation Risk** - Too close to liquidation
🛡️ **No Stop Loss** - Missing stop loss protection
🧠 **Revenge Pattern** - Emotional trading detected

**Action Buttons:**
✅ Acknowledge - Mark alert as seen
🧊 Cooldown 30m - Take a break (+5 points)
📉 Reduce size - Commit to reducing risk (+3 points)
🛡️ Setting SL - Commit to adding stop loss (+5 points)

**Your Score:**
Higher discipline scores mean better trading habits. Take positive actions to improve!

**Commands:**
/status - Current positions
/score - Discipline score
/help - This message

Questions? Just ask! 💬
        """
        
        await update.message.reply_text(
            help_msg,
            parse_mode=ParseMode.MARKDOWN
        )
    
    async def send_alert(
        self, 
        telegram_id: int, 
        alert: Dict,
        buttons: List[str] = None
    ) -> int:
        """
        Send risk alert to user with action buttons
        
        Args:
            telegram_id: User's Telegram ID
            alert: Alert dict from rule engine
            buttons: List of button types to show
        
        Returns:
            Message ID of sent message
        """
        try:
            # Format alert message
            message = self._format_alert_message(alert)
            
            # Create inline keyboard
            if buttons is None:
                buttons = self._get_default_buttons(alert['rule_type'])
            
            keyboard = self._create_keyboard(alert['alert_id'], buttons)
            
            # Send message
            sent_message = await self.app.bot.send_message(
                chat_id=telegram_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard
            )
            
            return sent_message.message_id
        
        except Exception as e:
            print(f"❌ Error sending alert to {telegram_id}: {e}")
            return None
    
    def _format_alert_message(self, alert: Dict) -> str:
        """Format alert as Telegram message"""
        emoji = alert.get('emoji', '⚠️')
        rule_name = alert.get('rule_name', 'Alert')
        symbol = alert.get('symbol', '')
        side = alert.get('side', '')
        size = alert.get('size', 0)
        message = alert.get('message', '')
        suggestion = alert.get('suggestion', '')
        
        # Build message
        msg = f"{emoji} **Jarvis Advisory**\n\n"
        
        if symbol:
            msg += f"**{symbol}** • {side} • {size:.4f}\n"
        
        msg += f"**{rule_name}**\n"
        msg += f"{message}\n\n"
        
        if suggestion:
            msg += f"💡 **Suggestion:**\n{suggestion}\n\n"
        
        # Add position details
        if alert.get('risk_pct'):
            msg += f"📊 Risk: {alert['risk_pct']:.1f}%\n"
        
        if alert.get('liq_distance_pct'):
            msg += f"🎯 Liq Distance: {alert['liq_distance_pct']:.1f}%\n"
        
        if alert.get('leverage'):
            msg += f"⚡ Leverage: {alert['leverage']}x\n"
        
        if alert.get('unrealized_pnl'):
            pnl = alert['unrealized_pnl']
            pnl_emoji = "📈" if pnl > 0 else "📉"
            msg += f"{pnl_emoji} Unrealized P&L: ${pnl:.2f}\n"
        
        msg += f"\n_Alert ID: {alert['alert_id'][-8:]}_"
        
        return msg
    
    def _get_default_buttons(self, rule_type: str) -> List[str]:
        """Get default button configuration for each rule type"""
        button_map = {
            'high_risk': ['ack', 'reduce', 'cooldown'],
            'liq_risk': ['ack', 'add_margin', 'reduce'],
            'no_sl': ['ack', 'set_sl', 'cooldown'],
            'revenge': ['ack', 'cooldown', 'view_stats']
        }
        
        return button_map.get(rule_type, ['ack', 'cooldown', 'reduce'])
    
    def _create_keyboard(self, alert_id: str, button_types: List[str]) -> InlineKeyboardMarkup:
        """Create inline keyboard with action buttons"""
        buttons = []
        
        for btn_type in button_types:
            if btn_type in BUTTON_CONFIGS:
                config = BUTTON_CONFIGS[btn_type]
                callback_data = json.dumps({
                    'action': btn_type,
                    'alert_id': alert_id
                })
                
                buttons.append(
                    InlineKeyboardButton(
                        text=config['label'],
                        callback_data=callback_data
                    )
                )
        
        # Arrange buttons in rows (2 per row)
        keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        
        return InlineKeyboardMarkup(keyboard)
    
    async def handle_button_click(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle action button clicks"""
        query = update.callback_query
        await query.answer()
        
        try:
            # Parse callback data
            data = json.loads(query.data)
            action = data['action']
            alert_id = data['alert_id']
            
            telegram_id = update.effective_user.id
            
            # Save to database
            with get_db() as db:
                user = db.query(User).filter(User.telegram_id == telegram_id).first()
                
                if not user:
                    await query.edit_message_text("❌ User not found")
                    return
                
                # Find alert
                alert = db.query(Alert).filter(Alert.alert_id == alert_id).first()
                
                if not alert:
                    await query.edit_message_text("❌ Alert not found")
                    return
                
                # Record button click
                button_config = BUTTON_CONFIGS.get(action, {})
                score_impact = button_config.get('score', 0)
                
                click = ButtonClick(
                    user_id=user.id,
                    alert_id=alert.id,
                    button_type=action,
                    score_impact=score_impact,
                    clicked_at=datetime.utcnow()
                )
                db.add(click)
                
                # Mark alert as acknowledged
                if not alert.is_acknowledged:
                    alert.is_acknowledged = True
                    alert.acknowledged_at = datetime.utcnow()
                
                db.commit()
                
                # Send confirmation
                response_msg = self._get_action_response(action, alert, score_impact)
                
                await query.edit_message_text(
                    text=f"{query.message.text}\n\n{response_msg}",
                    parse_mode=ParseMode.MARKDOWN
                )
        
        except Exception as e:
            print(f"❌ Error handling button click: {e}")
            await query.edit_message_text("⚠️ Error processing action")
    
    def _get_action_response(self, action: str, alert: Alert, score_impact: int) -> str:
        """Get response message for button action"""
        responses = {
            'ack': "✅ Acknowledged",
            'cooldown': f"🧊 Great decision! Taking a 30-minute break. (+{score_impact} points)",
            'reduce': f"📉 Smart move! Committing to reduce risk. (+{score_impact} points)",
            'set_sl': f"🛡️ Excellent! Setting stop loss is key. (+{score_impact} points)",
            'add_margin': f"💰 Good call! Adding margin for safety. (+{score_impact} points)",
            'view_stats': "📊 Opening stats..."
        }
        
        return responses.get(action, "✅ Action recorded")
    
    def _calculate_discipline_score(self, user_id: int, db) -> float:
        """
        Calculate discipline score for user
        
        Formula: 100 - (violations * 5) + (positive_actions * 2)
        """
        from datetime import timedelta
        
        # Last 7 days
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        
        # Count alerts (violations)
        total_alerts = db.query(Alert).filter(
            Alert.user_id == user_id,
            Alert.triggered_at >= seven_days_ago
        ).count()
        
        # Count acknowledged alerts
        ack_alerts = db.query(Alert).filter(
            Alert.user_id == user_id,
            Alert.triggered_at >= seven_days_ago,
            Alert.is_acknowledged == True
        ).count()
        
        # Count positive actions (score > 0)
        positive_actions = db.query(ButtonClick).join(Alert).filter(
            ButtonClick.user_id == user_id,
            ButtonClick.clicked_at >= seven_days_ago,
            ButtonClick.score_impact > 0
        ).count()
        
        # Calculate violations (alerts not acknowledged)
        violations = total_alerts - ack_alerts
        
        # Calculate score
        score = 100 - (violations * 5) + (positive_actions * 2)
        
        # Clamp between 0-100
        score = max(0, min(100, score))
        
        return score
    
    async def send_daily_recap(self, telegram_id: int, user_id: int):
        """Send daily recap to user"""
        try:
            with get_db() as db:
                # Get today's stats
                today = datetime.utcnow().date()
                today_start = datetime.combine(today, datetime.min.time())
                
                alerts_today = db.query(Alert).filter(
                    Alert.user_id == user_id,
                    Alert.triggered_at >= today_start
                ).all()
                
                total_alerts = len(alerts_today)
                ack_count = sum(1 for a in alerts_today if a.is_acknowledged)
                
                # Count by rule type
                rule_counts = {}
                for alert in alerts_today:
                    rule_type = alert.rule_type
                    rule_counts[rule_type] = rule_counts.get(rule_type, 0) + 1
                
                # Get top 3 violations
                top_violations = sorted(
                    rule_counts.items(),
                    key=lambda x: x[1],
                    reverse=True
                )[:3]
                
                # Calculate score
                score = self._calculate_discipline_score(user_id, db)
                badge, status = get_score_tier(score)
                
                # Build recap message
                recap_msg = f"""
📊 **Daily Trading Summary**

**{today.strftime('%A, %B %d, %Y')}**

Alerts sent: {total_alerts}
Acknowledged: {ack_count}/{total_alerts}

"""
                
                if top_violations:
                    recap_msg += "⚠️ **Top Violations:**\n"
                    for i, (rule, count) in enumerate(top_violations, 1):
                        rule_name = RULES.get(rule, {}).get('name', rule)
                        recap_msg += f"{i}. {rule_name} - {count}x\n"
                    recap_msg += "\n"
                
                recap_msg += f"""
🎯 **Discipline Score:** {badge} {score:.0f}/100

💡 **Focus Tomorrow:**
"""
                
                # Suggestion based on top violation
                if top_violations:
                    top_rule = top_violations[0][0]
                    suggestions = {
                        'high_risk': 'Size your positions more conservatively',
                        'liq_risk': 'Use lower leverage to stay safe',
                        'no_sl': 'Always set stop loss immediately',
                        'revenge': 'Take breaks between trades'
                    }
                    recap_msg += suggestions.get(top_rule, 'Keep following your rules!')
                else:
                    recap_msg += "Keep up the excellent discipline! 🏆"
                
                await self.app.bot.send_message(
                    chat_id=telegram_id,
                    text=recap_msg,
                    parse_mode=ParseMode.MARKDOWN
                )
        
        except Exception as e:
            print(f"❌ Error sending daily recap to {telegram_id}: {e}")
