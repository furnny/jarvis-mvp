"""
Jarvis MVP Database Models
SQLAlchemy ORM schemas
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, JSON, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class User(Base):
    """Telegram users with API credentials"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False, index=True)
    telegram_username = Column(String, nullable=True)
    
    # Binance API credentials (encrypted in production!)
    binance_api_key = Column(String, nullable=False)
    binance_api_secret = Column(String, nullable=False)
    
    # Settings
    is_active = Column(Boolean, default=True)
    max_risk_pct = Column(Float, default=2.0)
    min_liq_distance_pct = Column(Float, default=5.0)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    alerts = relationship("Alert", back_populates="user", cascade="all, delete-orphan")
    button_clicks = relationship("ButtonClick", back_populates="user", cascade="all, delete-orphan")


class Alert(Base):
    """Risk alerts sent to users"""
    __tablename__ = "alerts"
    
    id = Column(Integer, primary_key=True)
    alert_id = Column(String, unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    
    # Alert details
    rule_type = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    side = Column(String, nullable=False)
    
    # Position snapshot
    position_size = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=True)
    mark_price = Column(Float, nullable=True)
    leverage = Column(Float, nullable=True)
    
    # Risk metrics
    risk_pct = Column(Float, nullable=True)
    liq_distance_pct = Column(Float, nullable=True)
    has_stop_loss = Column(Boolean, default=False)
    
    # Full position data
    position_snapshot = Column(JSON, nullable=True)
    
    # Status
    is_acknowledged = Column(Boolean, default=False)
    telegram_message_id = Column(Integer, nullable=True)
    
    # Timestamps
    triggered_at = Column(DateTime, default=datetime.utcnow, index=True)
    acknowledged_at = Column(DateTime, nullable=True)
    
    # Relationships
    user = relationship("User", back_populates="alerts")
    button_clicks = relationship("ButtonClick", back_populates="alert", cascade="all, delete-orphan")


class ButtonClick(Base):
    """User actions on alert buttons"""
    __tablename__ = "button_clicks"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    alert_id = Column(Integer, ForeignKey("alerts.id"), nullable=False, index=True)
    
    # Button action
    button_type = Column(String, nullable=False)
    score_impact = Column(Integer, default=0)
    
    # Timestamp
    clicked_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    # Relationships
    user = relationship("User", back_populates="button_clicks")
    alert = relationship("Alert", back_populates="button_clicks")


class DisciplineScore(Base):
    """Daily discipline score snapshots"""
    __tablename__ = "discipline_scores"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    
    # Score data
    date = Column(DateTime, nullable=False, index=True)
    score = Column(Float, nullable=False)
    
    # Components
    total_alerts = Column(Integer, default=0)
    acknowledged_alerts = Column(Integer, default=0)
    positive_actions = Column(Integer, default=0)
    violations = Column(Integer, default=0)
    
    # Badge
    badge = Column(String, nullable=True)
    status = Column(String, nullable=True)
    
    # Calculated at
    calculated_at = Column(DateTime, default=datetime.utcnow)


class Trade(Base):
    """Trade history for revenge pattern detection"""
    __tablename__ = "trades"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    
    # Trade details
    symbol = Column(String, nullable=False)
    side = Column(String, nullable=False)
    size = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    
    # P&L
    pnl = Column(Float, nullable=True)
    is_win = Column(Boolean, nullable=True)
    
    # Timestamps
    opened_at = Column(DateTime, nullable=False, index=True)
    closed_at = Column(DateTime, nullable=True, index=True)
    
    # Metadata
    leverage = Column(Float, nullable=True)
    order_id = Column(String, nullable=True)
