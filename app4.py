import json
import math
import requests
import re
import threading
import time
from flask import Flask, request
import MetaTrader5 as mt5
from config import (
    TELEGRAM_TOKEN,
    CHANNEL_USERNAME,
    MT5_LOGIN,
    MT5_SERVER,
    MT5_PASSWORD,
    MT5_PATH,
    TRADE_RISK,
    ORDER_TYPE  # Dynamically handle market or limit orders
)

class TradingBot:
    def __init__(self):
        self.app = Flask(__name__)
        self.setup_mt5()
        self.app.route("/webhook", methods=["POST"])(self.webhook)
        self.order_threads = {}
        self.forex_symbols = ['EURUSD', 'USDCAD', 'USDJPY', 'US100']
        self.stocks_symbols = ['PFE', 'BAC', 'AMZN', 'GOOG', 'NVDA', 'WMT', 'ZM', 'T', 'BABA']
        self.gold_silver_symbol = ['XAUUSD', 'XAGUSD']
        self.btc_symbol = ['BTCUSD']
        self.eth_symbol = ['ETHUSD']
        self.ltc_symbol = [ 'LTCUSD', 'ADAUSD']

    def setup_mt5(self):
        """Initialize MetaTrader 5 connection using credentials from the config."""
        if not mt5.initialize(
                path=MT5_PATH,
                login=MT5_LOGIN,
                server=MT5_SERVER,
                password=MT5_PASSWORD
        ):
            print("initialize() failed, error code =", mt5.last_error())
            quit()

    def calculate_lot_size(self, entry_price, stop_loss, symbol):
        """Calculate lot size based on risk management, stop loss, and config-based risk."""
        contract_size = mt5.symbol_info(symbol).trade_contract_size
        print(f"Contract size is: {contract_size}")
        risk_ticks = abs(entry_price - stop_loss)
        trade_risk = TRADE_RISK
        if risk_ticks == 0:
            raise ValueError("Stop loss distance is too small, unable to calculate risk")

        # lot_size = trade_risk / risk_ticks
        x = trade_risk / risk_ticks
        lot_size = x / contract_size
        symbol = symbol.strip()
        if symbol in self.forex_symbols:
            final_lot_size = lot_size
            print(f"Matched Forex symbol: {symbol}")
        elif symbol in self.stocks_symbols:
            # final_lot_size = math.floor(lot_size)
            # final_lot_size = float("{:.2f}".format(final_lot_size))
            lot_size = 100.00
            print(f"Matched Stock symbol: {symbol}")
        elif symbol in self.gold_silver_symbol:
            final_lot_size = lot_size
            print(f"Matched Gold symbol: {symbol}")
        elif symbol in self.btc_symbol:
            lot_size = 0.09
            print(f"Matched BTC symbol: {symbol}")
        elif symbol in self.ltc_symbol:
            lot_size = 30.0
            print(f"Matched LTC symbol: {symbol}")
        elif symbol in self.eth_symbol:
            lot_size = 1.5
            print(f"Matched ETH symbol: {symbol}")
        else:
            final_lot_size = lot_size
            print(f"No match found. Using default lot size: {final_lot_size}")

        LOT_PRECISION = 2
        lot_size = round(lot_size, LOT_PRECISION)
        return lot_size

    @staticmethod
    def format_price(price, symbol):
        """Format price to match the symbol's precision."""
        symbol_info = mt5.symbol_info(symbol)
        return round(price, symbol_info.digits)

    @staticmethod
    def send_telegram_message(message):
        """Send a message to a Telegram channel."""
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": CHANNEL_USERNAME, "text": message}
        requests.post(url, json=payload)

    def place_order(self, action, symbol, entry_price, lot_size, tp_levels, stop_loss):
        """Place an order using MetaTrader 5 with error handling."""
        order_type = mt5.ORDER_TYPE_BUY if action.lower() == 'buy' else mt5.ORDER_TYPE_SELL
        ticks = mt5.symbol_info_tick(symbol)
        order_request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot_size,
            "type": order_type,
            "price": entry_price if ORDER_TYPE.upper() == 'LIMIT' else ticks.ask,
            "sl": stop_loss,
            "tp": tp_levels[0] if tp_levels else None,
            "deviation": 20,
            "magic": 123456,
            "comment": "MB_Strategy",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(order_request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"Order placed successfully: {result.order}")
            if tp_levels:
                # Start a thread to monitor and adjust the order
                self.order_threads[result.order] = threading.Thread(
                    target=self.monitor_order,
                    args=(result.order, symbol, tp_levels, stop_loss)
                )
                self.order_threads[result.order].start()
            return {"status": "success", "order_id": result.order}, 200
        else:
            print("Order placement failed:", result.retcode)
            return {"status": "error", "message": "Order placement failed"}, 500

    def monitor_order(self, order_id, symbol, tp_levels, entry_price):
        """Monitor the order, take 50% at TP1, then modify to TP2 with SL at entry."""
        tp1_reached = False

        while not tp1_reached:
            time.sleep(1)
            positions = mt5.positions_get(ticket=order_id)

            if not positions:
                print(f"Order {order_id} not found.")
                break

            position = positions[0]
            current_price = mt5.symbol_info_tick(symbol).bid

            # Check if TP1 is reached
            if current_price >= tp_levels[0] - 0.1:
                tp1_reached = True
                print(f"TP1 reached for order {order_id}. Taking 50% profit and adjusting position.")

                # Calculate half volume, check against minimum volume requirement
                volume_half = round(position.volume / 2, 2)  # Adjust to precision allowed
                min_volume = mt5.symbol_info(symbol).volume_min

                if volume_half < min_volume:
                    print(f"Cannot close 50% of position {order_id} - volume below minimum required ({min_volume}).")
                    break

                # Prepare request to close 50% of the position
                close_request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "position": order_id,
                    "symbol": symbol,
                    "volume": volume_half,
                    "type": mt5.ORDER_TYPE_SELL if position.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                    "price": current_price,
                    "deviation": 20,
                    "magic": 234000,
                    "comment": "Partial close at TP1",
                }
                close_result = mt5.order_send(close_request)

                if close_result.retcode == mt5.TRADE_RETCODE_DONE:
                    print(f"50% of position {order_id} closed successfully at TP1.")

                    # Modify the remaining position: set SL to entry, TP to TP2
                    modify_request = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "position": order_id,
                        "sl": entry_price,
                        "tp": tp_levels[1] if len(tp_levels) > 1 else None,
                    }
                    modify_result = mt5.order_send(modify_request)

                    if modify_result.retcode == mt5.TRADE_RETCODE_DONE:
                        self.send_telegram_message(f"Order {order_id} modified to TP2 with SL at entry.")
                    else:
                        print(f"Failed to modify order {order_id}. Error: {modify_result.retcode}, {mt5.last_error()}")
                else:
                    print(
                        f"Failed to close 50% of position {order_id}. Error: {close_result.retcode}, {mt5.last_error()}")

    def webhook(self):
        """Handles incoming webhooks and processes the trading order."""
        message = request.data.decode("utf-8")
        print("Webhook message received:", message)
        self.send_telegram_message(message)

        action, symbol, entry_price, tp_levels, stop_loss = self.parse_plain_text_message(message)

        if not action or not symbol or entry_price is None or stop_loss is None:
            return {"status": "error", "message": "Failed to parse the message"}, 400

        symbol = symbol.replace("USDT", "USD")
        if symbol == "US500":
            symbol = "US500.cash"
        elif symbol == "US100":
            symbol = "US100.cash"

        entry_price = self.format_price(entry_price, symbol)
        stop_loss = self.format_price(stop_loss, symbol)
        if tp_levels:
            tp_levels = [self.format_price(tp, symbol) for tp in tp_levels]

        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None or not symbol_info.visible:
            if not mt5.symbol_select(symbol, True):
                print(f"Failed to select symbol {symbol}")
                return {"status": "error", "message": f"Failed to select symbol {symbol}"}, 400

        lot_size = self.calculate_lot_size(entry_price, stop_loss, symbol)

        return self.place_order(action, symbol, entry_price, lot_size, tp_levels, stop_loss)

    import re
    import json

    def parse_plain_text_message(self, message):
        """Parse plain text message to extract trading parameters based on Type 1 and Type 2 formats."""
        try:
            action, symbol, entry_price, tp_levels, stop_loss = None, None, None, [], None

            if re.search(r"TP-levels", message, re.IGNORECASE):
                action_match = re.search(r"(Buy|Sell)", message, re.IGNORECASE)
                action = action_match.group(0).capitalize() if action_match else None
                symbol_match = re.search(r",\s*([A-Z]+[A-Z0-9]*)", message)
                symbol = symbol_match.group(1) if symbol_match else None
                entry_price_match = re.search(r"price\s*=\s*([\d.]+)", message)
                entry_price = float(entry_price_match.group(1)) if entry_price_match else None
                tp_levels_match = re.findall(r"TP-levels\s*:\s*([\d.]+)", message)
                tp_levels = [float(tp) for tp in tp_levels_match] if tp_levels_match else []
                sl_match = re.search(r"SL\s*:\s*([\d.]+)", message)
                stop_loss = float(sl_match.group(1)) if sl_match else None

            elif re.search(r"Smart Signal Alert!", message, re.IGNORECASE):
                action_match = re.search(r"(Buy|Sell)", message, re.IGNORECASE)
                action = action_match.group(1).capitalize() if action_match else None
                symbol_match = re.search(r"(BTCUSDT|[A-Z]{6})", message)
                symbol = symbol_match.group(1) if symbol_match else None
                entry_price_match = re.search(r"Entry:\s*([\d.]+)", message)
                entry_price = float(entry_price_match.group(1)) if entry_price_match else None
                tp_levels_match = re.findall(r"TP(\d):\s*([\d.]+)", message)
                tp_levels = [float(tp[1]) for tp in tp_levels_match] if tp_levels_match else []
                sl_match = re.search(r"SL\s*:\s*([\d.]+)", message)
                stop_loss = float(sl_match.group(1)) if sl_match else None

            elif re.search(r"(Long entry|Short entry)", message, re.IGNORECASE):
                action_match = re.search(r"(Long|Short) entry", message, re.IGNORECASE)
                action = "Buy" if action_match.group(1).lower() == "long" else "Sell"
                symbol_match = re.search(r"Symbol:\s*([A-Z]+)", message)
                symbol = symbol_match.group(1) if symbol_match else None
                entry_price_match = re.search(r"Entry price:\s*([\d.]+)", message)
                entry_price = float(entry_price_match.group(1)) if entry_price_match else None
                tp_levels_match = re.findall(r"TP\d:\s*([\d.]+)", message)
                tp_levels = [float(tp) for tp in tp_levels_match] if tp_levels_match else []
                sl_match = re.search(r"SL:\s*([\d.]+)", message)
                stop_loss = float(sl_match.group(1)) if sl_match else None

            elif re.search(r"Symbol:", message, re.IGNORECASE) and not re.search(r"Direction:", message, re.IGNORECASE):
                symbol_match = re.search(r"Symbol:\s*([A-Z]+)", message)
                json_start = message.find("{")
                if symbol_match and json_start != -1:
                    symbol = symbol_match.group(1)
                    json_part = message[json_start:]
                    json_data = json.loads(json_part)
                    action = "Buy" if json_data.get('side', '').upper() == "LONG" else "Sell"
                    entry_price = float(json_data.get('entry', None))
                    tp_levels = [
                        float(json_data.get(f'tp{i}', None)) for i in range(1, 5)
                        if json_data.get(f'tp{i}', None)
                    ]
                    stop_loss = float(json_data.get('stop', None))
                else:
                    print("Unknown message format:", message)
                    return None, None, None, None, None

            elif re.search(r"Direction:", message, re.IGNORECASE):
                symbol_match = re.search(r"Symbol:\s*([A-Z0-9]+)", message)
                action_match = re.search(r"Direction:\s*(Buy|Sell)", message, re.IGNORECASE)
                if symbol_match and action_match:
                    symbol = symbol_match.group(1)
                    action = action_match.group(1).capitalize()
                    entry_price_match = re.search(r"Entry:\s*([\d.]+)", message)
                    entry_price = float(entry_price_match.group(1)) if entry_price_match else None
                    tp1_match = re.search(r"TP1:\s*([\d.]+)", message)
                    tp2_match = re.search(r"TP2:\s*([\d.]+)", message)
                    if tp1_match:
                        tp_levels.append(float(tp1_match.group(1)))
                    if tp2_match:
                        tp_levels.append(float(tp2_match.group(1)))
                    sl_match = re.search(r"SL:\s*([\d.]+)", message)
                    stop_loss = float(sl_match.group(1)) if sl_match else None
                else:
                    print("Unknown Type 4 message format:", message)
                    return None, None, None, None, None

            else:
                print("Unknown message format:", message)
                return None, None, None, None, None

            return action, symbol, entry_price, tp_levels, stop_loss

        except Exception as e:
            print(f"Failed to parse message: {e} for message: {message}")
            return None, None, None, None, None

    def run(self):
        """Start the Flask app."""
        self.app.run(host="0.0.0.0", port=80)


if __name__ == "__main__":
    bot = TradingBot()
    bot.run()
