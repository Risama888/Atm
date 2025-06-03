import requests
import pandas as pd
import numpy as np
from ta.trend import EMAIndicator, SuperTrend, IchimokuIndicator
import time
import telegram
import logging
import matplotlib.pyplot as plt
import mplfinance as mpf
import io

# === KONFIGURASI ===
TELEGRAM_TOKEN = '7795073622:AAFEHjnKKNAUv2SEwkhLpvblMqolLNjSP48'
CHAT_ID = '6157064978'

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT']
INTERVAL = '1h'
LIMIT = 500

TP_LEVELS = [0.005, 0.01, 0.015, 0.02, 0.025]
STOP_LOSS_PERCENT = 0.01

bot = telegram.Bot(token=TELEGRAM_TOKEN)
positions = {}

# === TELEGRAM ===
def send_telegram(message):
    try:
        bot.send_message(chat_id=CHAT_ID, text=message)
    except Exception as e:
        logging.error(f"Telegram error: {e}")

def send_chart_telegram(df, symbol, signal):
    df_plot = df.copy()
    df_plot.index.name = 'Date'
    df_plot = df_plot[-100:]

    addplots = [
        mpf.make_addplot(df_plot['supertrend'], color='lime' if signal == 'BUY' else 'red'),
        mpf.make_addplot(df_plot['ema200'], color='blue'),
        mpf.make_addplot(df_plot['tenkan_sen'], color='orange'),
        mpf.make_addplot(df_plot['kijun_sen'], color='purple'),
    ]

    title = f"{symbol} - Signal: {signal}"

    fig, axlist = mpf.plot(
        df_plot,
        type='candle',
        style='charles',
        volume=False,
        title=title,
        addplot=addplots,
        returnfig=True,
        figratio=(16, 9),
        figscale=1.2
    )

    buf = io.BytesIO()
    fig.savefig(buf, format='png')
    buf.seek(0)
    plt.close(fig)
    bot.send_photo(chat_id=CHAT_ID, photo=buf)

# === DATA ===
def get_klines(symbol, interval='1h', limit=500):
    url = f"https://api.binance.com/api/v3/klines"
    params = {'symbol': symbol, 'interval': interval, 'limit': limit}
    response = requests.get(url, params=params)
    data = response.json()
    df = pd.DataFrame(data, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_asset_volume', 'number_of_trades',
        'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
    ])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    df = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
    return df

def apply_indicators(df):
    supertrend = SuperTrend(high=df['high'], low=df['low'], close=df['close'], window=10, multiplier=3.0)
    df['supertrend'] = supertrend.super_trend()
    df['supertrend_direction'] = supertrend.super_trend_direction()

    ichimoku = IchimokuIndicator(high=df['high'], low=df['low'], window1=9, window2=26, window3=52)
    df['tenkan_sen'] = ichimoku.ichimoku_conversion_line()
    df['kijun_sen'] = ichimoku.ichimoku_base_line()

    df['ema200'] = EMAIndicator(close=df['close'], window=200).ema_indicator()
    return df

def generate_signal(df):
    row = df.iloc[-1]
    trend = 'uptrend' if row['close'] > row['ema200'] else 'downtrend'

    if (row['supertrend_direction'] == 1 and row['tenkan_sen'] > row['kijun_sen'] and trend == 'uptrend'):
        return 'BUY'
    elif (row['supertrend_direction'] == -1 and row['tenkan_sen'] < row['kijun_sen'] and trend == 'downtrend'):
        return 'SELL'
    else:
        return 'HOLD'

# === POSISI ===
def manage_positions(symbol, price, signal, df):
    global positions

    if symbol in positions:
        entry = positions[symbol]['entry']
        pos_type = positions[symbol]['type']
        current_tp = positions[symbol].get('tp_reached', 0)

        # Sinyal lawan
        if (pos_type == 'BUY' and signal == 'SELL') or (pos_type == 'SELL' and signal == 'BUY'):
            send_telegram(f"🔄 CLOSE {pos_type} {symbol} at {price:.2f} (opposite signal)")
            del positions[symbol]
            manage_positions(symbol, price, signal, df)
            return

        # TP dan SL
        for i in range(current_tp, len(TP_LEVELS)):
            tp = TP_LEVELS[i]
            level = i + 1

            if pos_type == 'BUY':
                tp_target = entry * (1 + tp)
                sl_target = entry * (1 - STOP_LOSS_PERCENT)
                if price >= tp_target:
                    send_telegram(f"🎯 TP{level} HIT (BUY) {symbol} at {price:.2f}")
                    positions[symbol]['tp_reached'] = level
                    if level == 5:
                        send_telegram(f"✅ CLOSE BUY {symbol} at TP5")
                        del positions[symbol]
                    break
                elif price <= sl_target:
                    send_telegram(f"⚠️ STOP LOSS (BUY) {symbol} at {price:.2f}")
                    del positions[symbol]
                    break

            elif pos_type == 'SELL':
                tp_target = entry * (1 - tp)
                sl_target = entry * (1 + STOP_LOSS_PERCENT)
                if price <= tp_target:
                    send_telegram(f"🎯 TP{level} HIT (SELL) {symbol} at {price:.2f}")
                    positions[symbol]['tp_reached'] = level
                    if level == 5:
                        send_telegram(f"✅ CLOSE SELL {symbol} at TP5")
                        del positions[symbol]
                    break
                elif price >= sl_target:
                    send_telegram(f"⚠️ STOP LOSS (SELL) {symbol} at {price:.2f}")
                    del positions[symbol]
                    break

    else:
        if signal == 'BUY':
            positions[symbol] = {'type': 'BUY', 'entry': price, 'tp_reached': 0}
            send_telegram(f"🟩 OPEN BUY {symbol} at {price:.2f}")
            send_chart_telegram(df, symbol, signal)

        elif signal == 'SELL':
            positions[symbol] = {'type': 'SELL', 'entry': price, 'tp_reached': 0}
            send_telegram(f"🟥 OPEN SELL {symbol} at {price:.2f}")
            send_chart_telegram(df, symbol, signal)

# === MAIN LOOP ===
def main():
    while True:
        for symbol in SYMBOLS:
            try:
                df = get_klines(symbol, INTERVAL, LIMIT)
                df = apply_indicators(df)
                signal = generate_signal(df)
                price = df['close'].iloc[-1]
                manage_positions(symbol, price, signal, df)
                print(f"{symbol}: {signal} at {price:.2f}")
            except Exception as e:
                logging.error(f"Error on {symbol}: {e}")
        time.sleep(30)  # 5 menit
