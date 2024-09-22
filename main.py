import os
import requests
import re
from flask import Flask, request
import MetaTrader5 as mt5
from config import TELEGRAM_TOKEN, CHANNEL_USERNAME, MT5_LOGIN, MT5_SERVER, MT5_PASSWORD, MT5_PATH, ORDER_TYPE, LEVERAGE


class TradingBot:
    def __init__(self):
        self.app = Flask(__name__)
        self.setup_mt5()
        self.app.route('/webhook', methods=['POST'])(self.webhook)

    def setup_mt5(self):
        """Initialize MetaTrader 5 connection."""
        if not mt5.initialize(path=MT5_PATH, login=MT5_LOGIN, server=MT5_SERVER, password=MT5_PASSWORD):
            print("initialize() failed, error code =", mt5.last_error())
            quit()

    @staticmethod
    def calculate_lot_size(account_balance, risk_percentage, stop_loss_distance, symbol, free_margin, leverage):
        """Calculate lot size based on account balance, risk, free margin, and leverage."""
        risk_amount = account_balance * (risk_percentage / 100.0)
        tick_value = mt5.symbol_info(symbol).trade_contract_size

        # Calculate the amount controlled by leverage
        effective_balance = account_balance * leverage

        # Cap the lot size based on free margin and effective balance
        lot_size = min(free_margin / (stop_loss_distance * tick_value),
                       risk_amount / (stop_loss_distance * tick_value),
                       effective_balance / (stop_loss_distance * tick_value))

        # Retrieve max lot size from symbol info
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is not None:
            max_lot_size = symbol_info.volume_max
            lot_size = min(lot_size, max_lot_size)
            print(f"Max allowed lot size for {symbol}: {max_lot_size}")

        print(f"Calculated lot size: {lot_size}, Effective balance: {effective_balance}")

        # Format lot size
        if symbol in ["AMZN", "AAPL", "WMT", "BAC"]:
            lot_size = round(lot_size)  # Round to the nearest integer
            lot_size = float(f"{lot_size:.2f}")  # Format with '.00'
        else:
            lot_size = round(lot_size, 2)  # Use float with two decimal places for other symbols

        return lot_size

    @staticmethod
    def format_price(price, symbol):
        """Format price to match the symbol's precision."""
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            raise ValueError(f"Symbol {symbol} is not found")

        # Format price based on symbol precision
        digits = symbol_info.digits
        return round(price, digits)

    @staticmethod
    def parse_plain_text_message(message):
        """Parse plain text message to extract trading parameters."""
        try:
            action_match = re.search(r"(Buy|Sell)", message)
            action = action_match.group(0) if action_match else None

            symbol_match = re.search(r"\d+,\s*(\w+)", message)
            symbol = symbol_match.group(1) if symbol_match else None

            entry_price_match = re.search(r"price\s*=\s*([\d.]+)", message)
            entry_price = float(entry_price_match.group(1)) if entry_price_match else None

            tp_levels_match = re.findall(r"TP-levels\s*:\s*([\d.]+)", message)
            tp_levels = [float(tp) for tp in tp_levels_match] if tp_levels_match else []

            sl_match = re.search(r"SL\s*:\s*([\d.]+)", message)
            stop_loss = float(sl_match.group(1)) if sl_match else None

            if not all([action, symbol, entry_price, stop_loss]):
                raise ValueError("Missing key trade information")

            return action, symbol, entry_price, tp_levels, stop_loss

        except (AttributeError, ValueError) as e:
            print(f"Error parsing message: {e}")
            return None, None, None, None, None

    @staticmethod
    def send_telegram_message(message):
        """Sends a message to a Telegram channel."""
        url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
        payload = {'chat_id': CHANNEL_USERNAME, 'text': message}
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("Telegram message sent successfully.")
        else:
            print("Error sending Telegram message:", response.text)

    def place_order(self, action, symbol, entry_price, stop_loss, take_profit, lot_size):
        """Place an order on MetaTrader 5."""
        order_type = mt5.ORDER_TYPE_BUY if action == "Buy" else mt5.ORDER_TYPE_SELL
        if ORDER_TYPE == 'MARKET':
            order_request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lot_size,
                "type": order_type,
                "sl": stop_loss,
                "tp": take_profit,
                "magic": 234000,
                "comment": "Noble Impulse",
            }
        else:  # Default to LIMIT order
            order_request = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": symbol,
                "volume": lot_size,
                "type": mt5.ORDER_TYPE_BUY_LIMIT if action == "Buy" else mt5.ORDER_TYPE_SELL_LIMIT,
                "price": entry_price,
                "sl": stop_loss,
                "tp": take_profit,
                "deviation": 50,
                "magic": 234000,
                "comment": "Noble Impulse",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_RETURN,
            }

        print(f"Order request: {order_request}")

        # Send the order
        result = mt5.order_send(order_request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            error_code = mt5.last_error()
            print(f"Failed to place order: {result}, MT5 error: {error_code}")
            return {'status': 'error', 'message': 'Order placement failed', 'details': str(result)}, 500

        print(f"Order placed successfully: {result.order}")
        return {'status': 'success', 'order_id': result.order}, 200

    def webhook(self):
        """Handles incoming webhooks and processes the trading order."""
        message = request.data.decode('utf-8')  # Decode plain text data from the request
        print("Webhook message received:", message)

        # Send the received data to Telegram
        self.send_telegram_message(message)

        # Parse the message to extract trade details
        action, symbol, entry_price, tp_levels, stop_loss = self.parse_plain_text_message(message)
        if not action or not symbol or not entry_price or not stop_loss:
            return {'status': 'error', 'message': 'Failed to parse the message'}, 400

        # Replace "US500" with "US500.cash"
        if symbol == "US500":
            symbol = "US500.cash"

        take_profit = float(tp_levels[0]) if tp_levels else None

        # Format prices
        entry_price = self.format_price(entry_price, symbol)
        stop_loss = self.format_price(stop_loss, symbol)
        take_profit = self.format_price(take_profit, symbol)

        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            print(f"Symbol {symbol} is not found")
            return {'status': 'error', 'message': f"Symbol {symbol} not found"}, 400
        if not symbol_info.visible:
            print(f"Symbol {symbol} is not visible, attempting to make it visible")
            if not mt5.symbol_select(symbol, True):
                print(f"Failed to select symbol {symbol}")
                return {'status': 'error', 'message': f"Failed to select symbol {symbol}"}, 400

        account_info = mt5.account_info()
        if account_info is None:
            print("Failed to retrieve account information")
            return {'status': 'error', 'message': 'Failed to retrieve account information'}, 500

        account_balance = account_info.balance
        free_margin = account_info.margin_free
        print(f"Account balance: {account_balance}, Free margin: {free_margin}")

        stop_loss_distance = abs(entry_price - stop_loss)
        risk_percentage = 0.5  # Risking 1% of the balance
        leverage = LEVERAGE  # Assume LEVERAGE is defined in your config
        lot_size = self.calculate_lot_size(account_balance, risk_percentage, stop_loss_distance, symbol, free_margin, leverage)
        print(f"Final lot size for order: {lot_size}")

        return self.place_order(action, symbol, entry_price, stop_loss, take_profit, lot_size)

    def run(self):
        """Run the Flask application."""
        host = os.getenv('FLASK_HOST', '0.0.0.0')  # Default host to accept external requests
        port = int(os.getenv('FLASK_PORT', 80))  # Default port is 80
        self.app.run(host=host, port=port, debug=True)

    @staticmethod
    def shutdown_mt5():
        """Shutdown the MetaTrader 5 connection."""
        mt5.shutdown()


if __name__ == '__main__':
    bot = TradingBot()
    try:
        bot.run()
    finally:
        bot.shutdown_mt5()
