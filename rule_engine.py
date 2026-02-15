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
                    pattern_type='​​​​​​​​​​​​​​​​
