"""
Jarvis MVP - Binance Futures API Client
Fetch USDT-M Futures positions and calculate risk metrics
"""
from binance.client import Client
from binance.exceptions import BinanceAPIException
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from config import settings


class BinanceClient:
    """Wrapper for Binance Futures API with risk calculations"""
    
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        if testnet:
            # Binance Futures Testnet
            self.client = Client(
                api_key=api_key,
                api_secret=api_secret,
                testnet=True
            )
            self.client.API_URL = 'https://testnet.binancefuture.com'
        else:
            # Production
            self.client = Client(api_key=api_key, api_secret=api_secret)
        
        self.api_key = api_key
        self.testnet = testnet
    
    def get_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        """
        Fetch all open USDT-M Futures positions
        
        Returns:
            List of position dicts with calculated risk metrics
        """
        try:
            # Get all positions
            account_info = self.client.futures_account()
            positions = account_info['positions']
            
            # Get account balance for risk calculation
            balance = float(account_info['totalWalletBalance'])
            
            # Filter only open positions and add risk calculations
            open_positions = []
            for pos in positions:
                position_amt = float(pos['positionAmt'])
                
                if position_amt != 0:  # Position is open
                    enriched = self._enrich_position(pos, balance)
                    if enriched:
                        open_positions.append(enriched)
            
            # Filter by symbol if specified
            if symbol:
                open_positions = [p for p in open_positions if p['symbol'] == symbol]
            
            return open_positions
            
        except BinanceAPIException as e:
            print(f"❌ Binance API error: {e}")
            return []
        except Exception as e:
            print(f"❌ Error fetching positions: {e}")
            return []
    
    def _enrich_position(self, position: Dict, balance: float) -> Optional[Dict]:
        """
        Add calculated risk metrics to position
        
        Calculations:
        - Risk%: Position Value / Account Balance * 100
        - Liq Distance%: |Mark Price - Liq Price| / Mark Price * 100
        """
        try:
            symbol = position['symbol']
            position_amt = float(position['positionAmt'])
            entry_price = float(position['entryPrice'])
            mark_price = float(position['markPrice'])
            leverage = int(position['leverage'])
            unrealized_pnl = float(position['unrealizedProfit'])
            liquidation_price = float(position['liquidationPrice'])
            
            # Side detection
            side = "Long" if position_amt > 0 else "Short"
            size = abs(position_amt)
            
            # Position value in USDT
            position_value = size * mark_price
            
            # Risk% = Position Value / Balance * 100
            risk_pct = (position_value / balance * 100) if balance > 0 else 0
            
            # Liquidation distance%
            if liquidation_price > 0:
                liq_distance_pct = abs(mark_price - liquidation_price) / mark_price * 100
            else:
                liq_distance_pct = 999  # No liquidation risk
            
            # Stop loss detection (check if stop order exists)
            has_sl = self._check_stop_loss(symbol)
            
            # Build enriched position dict
            enriched = {
                'symbol': symbol,
                'side': side,
                'side_normalized': side,
                'size': size,
                'entry_price': entry_price,
                'mark_price': mark_price,
                'leverage': leverage,
                'leverage_num': leverage,
                'unrealized_pnl': unrealized_pnl,
                'unrealized_pnl_usd': round(unrealized_pnl, 2),
                'liquidation_price': liquidation_price,
                
                # Calculated metrics
                'risk_pct': round(risk_pct, 2),
                'liq_distance_pct': round(liq_distance_pct, 2),
                'position_value_usd': round(position_value, 2),
                'has_stop_loss': has_sl,
                
                # Timestamps
                'updated_time_dt': datetime.utcnow(),
                'created_time_dt': datetime.utcnow(),
                
                # Raw data
                'raw': position
            }
            
            return enriched
            
        except Exception as e:
            print(f"⚠️ Error enriching position {position.get('symbol')}: {e}")
            return None
    
    def _check_stop_loss(self, symbol: str) -> bool:
        """
        Check if position has an active stop loss order
        
        Returns:
            True if stop loss exists
        """
        try:
            open_orders = self.client.futures_get_open_orders(symbol=symbol)
            
            for order in open_orders:
                order_type = order['type']
                if order_type in ['STOP_MARKET', 'STOP', 'TAKE_PROFIT_MARKET', 'TAKE_PROFIT']:
                    return True
            
            return False
            
        except Exception as e:
            print(f"⚠️ Error checking stop loss for {symbol}: {e}")
            return False
    
    def get_account_balance(self) -> float:
        """
        Get USDT Futures wallet balance
        
        Returns:
            Total USDT balance
        """
        try:
            account = self.client.futures_account()
            balance = float(account['totalWalletBalance'])
            return balance
            
        except Exception as e:
            print(f"❌ Error fetching balance: {e}")
            return 0.0
    
    def get_recent_trades(self, symbol: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """
        Fetch recent closed trades for revenge pattern detection
        
        Returns:
            List of realized PnL records with win/loss info
        """
        try:
            # Get realized PnL history (last 7 days)
            start_time = int((datetime.utcnow() - timedelta(days=7)).timestamp() * 1000)
            
            income_history = self.client.futures_income_history(
                incomeType='REALIZED_PNL',
                startTime=start_time,
                limit=limit
            )
            
            # Filter by symbol if specified
            if symbol:
                income_history = [i for i in income_history if i['symbol'] == symbol]
            
            # Add normalized fields
            trades = []
            for income in income_history:
                pnl = float(income['income'])
                if pnl != 0:  # Skip zero PnL entries
                    trade = {
                        'symbol': income['symbol'],
                        'closed_pnl': pnl,
                        'is_win': pnl > 0,
                        'closed_time_dt': datetime.fromtimestamp(income['time'] / 1000),
                        'transaction_id': income['tranId'],
                        'raw': income
                    }
                    trades.append(trade)
            
            # Sort by time (newest first)
            trades.sort(key=lambda x: x['closed_time_dt'], reverse=True)
            
            return trades
            
        except Exception as e:
            print(f"❌ Error fetching trades: {e}")
            return []
