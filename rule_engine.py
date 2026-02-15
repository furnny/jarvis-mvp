"""
Jarvis MVP - Rule Engine
4 core risk detection rules with alert generation
"""
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from config import settings, RULES
from binance_client import BinanceClient


class RuleEngine:
    """
    Core risk detection engine
    Checks positions against 4 rules and generates alerts
    """
    
    def __init__(self, binance_client: BinanceClient):
        self.client = binance_client
        self.last_alert_times = {}  # Track cooldowns per rule
    
    def check_all_rules(self, position: Dict) -> List[Dict]:
        """
        Run all 4 rules against a position
        
        Returns:
            List of alert dicts (empty if no violations)
        """
        alerts = []
        
        # Rule 1: High Risk
        if alert := self.check_high_risk(position):
            alerts.append(alert)
        
        # Rule 2: Liquidation Risk
        if alert := self.check_liquidation_risk(position):
            alerts.append(alert)
        
        # Rule 3: No Stop Loss
        if alert := self.check_no_stop_loss(position):
            alerts.append(alert)
        
        return alerts
    
    def check_revenge_pattern(self, user_id: int) -> Optional[Dict]:
        """
        Rule 4: Revenge Trading Pattern (separate check, not per-position)
        Requires trade history analysis
        
        Returns:
            Alert dict if pattern detected, None otherwise
        """
        try:
            trades = self.client.get_recent_trades(limit=20)
            
            if len(trades) < 2:
                return None
            
            now = datetime.utcnow()
            
            # Pattern 1: Multiple losses + quick re-entry
            recent_trades = [
                t for t in trades 
                if (now - t['closed_time_dt']).total_seconds() < settings.REVENGE_WINDOW_MINUTES * 60
            ]
            
            if len(recent_trades) >= 2:
                # Check for losing streak
                losses = [t for t in recent_trades if not t['is_win']]
                
                if len(losses) >= 2:
                    # Check if new position opened within 5 minutes of last loss
                    last_loss_time = losses[0]['closed_time_dt']
                    positions = self.client.get_positions()
                    
                    for pos in positions:
                        if (now - last_loss_time).total_seconds() < 300:  # 5 minutes
                            return self._create_alert(
                                rule_type='revenge',
                                position=positions[0] if positions else {},
                                pattern_type='Quick re-entry after losses',
                                details={
                                    'recent_losses': len(losses),
                                    'time_since_last_loss': f"{int((now - last_loss_time).total_seconds() / 60)}m"
                                }
                            )
            
            # Pattern 2: High trade frequency (>5 trades in 30 minutes)
            thirty_min_ago = now - timedelta(minutes=30)
            recent_count = len([
                t for t in trades 
                if t['closed_time_dt'] > thirty_min_ago
            ])
            
            if recent_count >= 5:
                return self._create_alert(
                    rule_type='revenge',
                    position={},
                    pattern_type='High frequency trading',
                    details={
                        'trade_count': recent_count,
                        'timeframe': '30 minutes'
                    }
                )
            
            return None
            
        except Exception as e:
            print(f"⚠️ Error checking revenge pattern: {e}")
            return None
    
    def check_high_risk(self, position: Dict) -> Optional[Dict]:
        """
        Rule 1: Risk% exceeds threshold
        
        Condition: position['risk_pct'] > MAX_RISK_PCT
        """
        risk_pct = position.get('risk_pct', 0)
        
        if risk_pct > settings.MAX_RISK_PCT:
            # Check cooldown
            if not self._should_alert('high_risk', position['symbol']):
                return None
            
            # Calculate recommended size reduction
            current_risk = risk_pct
            target_risk = settings.MAX_RISK_PCT
            reduction_pct = ((current_risk - target_risk) / current_risk) * 100
            
            return self._create_alert(
                rule_type='high_risk',
                position=position,
                message=f"Risk {risk_pct}% exceeds limit ({settings.MAX_RISK_PCT}%)",
                suggestion=f"Reduce size by ~{int(reduction_pct)}%",
                severity='warning'
            )
        
        return None
    
    def check_liquidation_risk(self, position: Dict) -> Optional[Dict]:
        """
        Rule 2: Liquidation price too close
        
        Condition: position['liq_distance_pct'] < MIN_LIQ_DISTANCE_PCT
        """
        liq_distance = position.get('liq_distance_pct', 999)
        
        if liq_distance < settings.MIN_LIQ_DISTANCE_PCT:
            # Check cooldown
            if not self._should_alert('liq_risk', position['symbol']):
                return None
            
            return self._create_alert(
                rule_type='liq_risk',
                position=position,
                message=f"Liquidation {liq_distance:.1f}% away (min safe: {settings.MIN_LIQ_DISTANCE_PCT}%)",
                suggestion="Add margin or reduce leverage",
                severity='critical'
            )
        
        return None
    
    def check_no_stop_loss(self, position: Dict) -> Optional[Dict]:
        """
        Rule 3: Position has no stop loss after timeout
        
        Condition: position open > NO_SL_TIMEOUT_MINUTES AND no stop loss set
        """
        has_sl = position.get('has_stop_loss', False)
        
        if not has_sl:
            # Check cooldown (longer for this rule to avoid spam)
            if not self._should_alert('no_sl', position['symbol']):
                return None
            
            # Calculate suggested SL based on risk
            risk_pct = position.get('risk_pct', 2.0)
            entry_price = position.get('entry_price', 0)
            side = position.get('side', 'Long')
            
            if side == 'Long':
                sl_price = entry_price * (1 - risk_pct / 100)
            else:
                sl_price = entry_price * (1 + risk_pct / 100)
            
            return self._create_alert(
                rule_type='no_sl',
                position=position,
                message="No stop loss detected",
                suggestion=f"Set SL at ${sl_price:,.2f} (~{risk_pct}% risk)",
                severity='warning'
            )
        
        return None
    
    def _create_alert(
        self, 
        rule_type: str, 
        position: Dict,
        message: str = "",
        suggestion: str = "",
        pattern_type: str = "",
        details: Dict = None,
        severity: str = "warning"
    ) -> Dict:
        """
        Create standardized alert dictionary
        """
        rule_config = RULES.get(rule_type, {})
        
        alert = {
            'alert_id': self._generate_alert_id(rule_type, position.get('symbol', 'SYSTEM')),
            'rule_type': rule_type,
            'rule_name': rule_config.get('name', rule_type),
            'emoji': rule_config.get('emoji', '⚠️'),
            'severity': severity,
            'message': message,
            'suggestion': suggestion,
            'pattern_type': pattern_type,
            'details': details or {},
            
            # Position data
            'symbol': position.get('symbol', ''),
            'side': position.get('side_normalized', ''),
            'size': position.get('size', 0),
            'entry_price': position.get('entry_price', 0),
            'mark_price': position.get('mark_price', 0),
            'leverage': position.get('leverage', 1),
            'risk_pct': position.get('risk_pct', 0),
            'liq_distance_pct': position.get('liq_distance_pct', 0),
            'has_stop_loss': position.get('has_stop_loss', False),
            'unrealized_pnl': position.get('unrealized_pnl', 0),
            
            # Full position snapshot
            'position_snapshot': position,
            
            # Timestamp
            'triggered_at': datetime.utcnow()
        }
        
        # Update last alert time
        self._update_last_alert_time(rule_type, position.get('symbol', ''))
        
        return alert
    
    def _should_alert(self, rule_type: str, symbol: str) -> bool:
        """
        Check if enough time has passed since last alert (cooldown)
        
        Returns:
            True if should send alert, False if in cooldown
        """
        key = f"{rule_type}:{symbol}"
        cooldown = settings.ALERT_COOLDOWNS.get(rule_type, 300)
        
        if key in self.last_alert_times:
            last_time = self.last_alert_times[key]
            elapsed = (datetime.utcnow() - last_time).total_seconds()
            
            if elapsed < cooldown:
                return False  # Still in cooldown
        
        return True
    
    def _update_last_alert_time(self, rule_type: str, symbol: str):
        """Update last alert timestamp for cooldown tracking"""
        key = f"{rule_type}:{symbol}"
        self.last_alert_times[key] = datetime.utcnow()
    
    def _generate_alert_id(self, rule_type: str, symbol: str) -> str:
        """
        Generate unique alert ID
        
        Format: alert_YYYYMMDD_HHMMSS_ruletype_symbol
        """
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        return f"alert_{timestamp}_{rule_type}_{symbol}"
