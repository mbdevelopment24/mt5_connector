"""
Telegram bot configuration
"""
TELEGRAM_TOKEN = 'Token'
CHANNEL_USERNAME = 'Channel-ID'

"""
MetaTrader 5 configuration
"""
MT5_LOGIN = MT5Logon
MT5_SERVER = 'Server'
MT5_PASSWORD = 'Pass'
MT5_PATH = 'MT5_Path'

"""
RISK
"""
TRADE_RISK = 50

"""
Order configuration
Change to 'LIMIT' for limit orders
"""
ORDER_TYPE = 'MARKET'

"""
Take Profit / Stop Loss management
"""
TP1_PERCENT_TAKE = 50  # Take 50% at TP1
TP1_TOLERANCE_CENTS = 5  # Tolerance before TP1, in cents


