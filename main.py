import json
import math
import requests
import re
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
        self.forex_symbols = ['EURUSD', 'USDCAD', 'USDJPY', 'US100']
        self.stocks_symbols = ['PFE', 'BAC', 'AMZN', 'GOOG', 'NVDA', 'WMT', 'ZM', 'T', 'BABA']
        self.gold_silver_symbol = ['XAUUSD', 'XAGUSD']
        self.btc_symbol = ['BTCUSD']

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

        #lot_size = trade_risk / risk_ticks
        x = trade_risk / risk_ticks
        lot_size = x / contract_size
        symbol = symbol.strip()
        if symbol in self.forex_symbols:
            final_lot_size = lot_size
            print(f"Matched Forex symbol: {symbol}")
        elif symbol in self.stocks_symbols:
            #final_lot_size = math.floor(lot_size)
            #final_lot_size = float("{:.2f}".format(final_lot_size))
            final_lot_size = 100.00
            print(f"Matched Stock symbol: {symbol}")
        elif symbol in self.gold_silver_symbol:
            final_lot_size = lot_size
            print(f"Matched Gold symbol: {symbol}")
        elif symbol in self.btc_symbol:
            final_lot_size = 0.09
            print(f"Matched BTC symbol: {symbol}")
        else:
            final_lot_size = lot_size
            print(f"No match found. Using default lot size: {final_lot_size}")

        LOT_PRECISION = 2
        final_lot_size = round(final_lot_size, LOT_PRECISION)
        return final_lot_size

    @staticmethod
    def format_price(price, symbol):
        """Format price to match the symbol's precision."""
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            raise ValueError(f"Symbol {symbol} is not found")

        digits = symbol_info.digits
        return round(price, digits)

    @staticmethod
    def send_telegram_message(message):
        """Send a message to a Telegram channel using config values."""
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": CHANNEL_USERNAME, "text": message}
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("Telegram message sent successfully.")
        else:
            print("Error sending Telegram message:", response.text)

    def place_order(self, action, symbol, entry_price, lot_size, tp_levels, stop_loss):
        """Place an order using MetaTrader 5 with error handling and handling LIMIT/MARKET orders."""
        try:
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                print(f"Failed to get symbol info for {symbol}")
                return {"status": "error", "message": "Failed to get symbol info"}, 500

            ticks = mt5.symbol_info_tick(symbol)
            if ticks is None:
                print(f"Failed to get tick data for {symbol}")
                return {"status": "error", "message": "Failed to get tick data"}, 500

            if ORDER_TYPE.upper() == 'LIMIT':
                if action.lower() == 'buy' and entry_price >= ticks.ask:
                    print(f"Invalid buy limit price: {entry_price}. Must be lower than ask: {ticks.ask}")
                    return {"status": "error", "message": "Invalid buy limit price"}, 400
                elif action.lower() == 'sell' and entry_price <= ticks.bid:
                    print(f"Invalid sell limit price: {entry_price}. Must be higher than bid: {ticks.bid}")
                    return {"status": "error", "message": "Invalid sell limit price"}, 400

                order_type = mt5.ORDER_TYPE_BUY_LIMIT if action.lower() == 'buy' else mt5.ORDER_TYPE_SELL_LIMIT
            else:
                order_type = mt5.ORDER_TYPE_BUY if action.lower() == 'buy' else mt5.ORDER_TYPE_SELL

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
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                error_code = mt5.last_error()
                print(f"Failed to place order: {result}, MT5 error: {error_code}")
                return {
                    "status": "error",
                    "message": "Order placement failed",
                    "details": str(result),
                }, 500

            print(f"Order placed successfully: {result.order}")
            return {"status": "success", "order_id": result.order}, 200

        except Exception as e:
            print(f"Failed to place order due to an exception: {e}")
            return {"status": "error", "message": str(e)}, 500

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

    def parse_plain_text_message(self, message):
        """Parse plain text message to extract trading parameters based on Type 1 and Type 2 formats."""
        try:
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
                # Type 3 parsing logic
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
                    # Look for the 'Symbol' and 'Direction' fields
                    symbol_match = re.search(r"Symbol:\s*([A-Z0-9]+)", message)
                    action_match = re.search(r"Direction:\s*(Buy|Sell)", message, re.IGNORECASE)

                    # Ensure that both symbol and action (Buy/Sell) are identified
                    if symbol_match and action_match:
                        symbol = symbol_match.group(1)
                        action = action_match.group(1).capitalize()

                        # Parse entry price, TP levels, and SL
                        entry_price_match = re.search(r"Entry:\s*([\d.]+)", message)
                        entry_price = float(entry_price_match.group(1)) if entry_price_match else None

                        # TP levels (could be more than one TP level)
                        tp_levels = []
                        tp1_match = re.search(r"TP1:\s*([\d.]+)", message)
                        tp2_match = re.search(r"TP2:\s*([\d.]+)", message)
                        if tp1_match:
                            tp_levels.append(float(tp1_match.group(1)))
                        if tp2_match:
                            tp_levels.append(float(tp2_match.group(1)))

                        # Stop Loss
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
            print(f"Failed to parse message: {e}")
            return None, None, None, None, None


if __name__ == "__main__":
    bot = TradingBot()
    bot.app.run(host='0.0.0.0', port=80)
