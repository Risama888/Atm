# binance_trading_bot.py

import requests
import pandas as pd
import numpy as np
import time
import datetime as dt
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import mplfinance as mpf
import io
from telegram import Bot
from telegram.error import TelegramError

# ==== Konfigurasi ====
TELEGRAM_TOKEN = '7795073622:AAFEHjnKKNAUv2SEwkhLpvblMqolLNjSP48'
CHAT_ID = '-1002561504370'
INTERVAL = '30m'
SYMBOLS = ['BTCUSDT', 'ETHUSDT']
EMA_PERIOD = 200
TP_LEVELS = [0.005, 0.01, 0.015, 0.02, 0.025]
STOP_LOSS_PERCENT = 0.01
bot = Bot(token=TELEGRAM_TOKEN)
open_positions = {}

# ==== Fungsi untuk ambil data ====
def get_klines(symbol, interval, limit=300):
    url = f'https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}'
    data = requests.get(url).json()
    df = pd.DataFrame(data, columns=['time','open','high','low','close','volume','close_time','q','n','taker_base_vol','taker_quote_vol','ignore'])
    df['time'] = pd.to_datetime(df['time'], unit='ms')
    df.set_index('time', inplace=True)
    df = df[['open','high','low','close','volume']].astype(float)
    return df

# ==== Indikator ====
def apply_indicators(df):
    df['ema200'] = df['close'].ewm(span=EMA_PERIOD, adjust=False).mean()
    atr = df['high'] - df['low']
    atr = atr.rolling(10).mean()
    hl2 = (df['high'] + df['low']) / 2
    df['supertrend'] = hl2 - (3 * atr)
    nine_period_high = df['high'].rolling(window=9).max()
    nine_period_low = df['low'].rolling(window=9).min()
    df['tenkan_sen'] = (nine_period_high + nine_period_low) / 2
    period26_high = df['high'].rolling(window=26).max()
    period26_low = df['low'].rolling(window=26).min()
    df['kijun_sen'] = (period26_high + period26_low) / 2
    return df

# ==== Telegram ====
def send_telegram(message):
    try:
        bot.send_message(chat_id=CHAT_ID, text=message, timeout=30)
    except TelegramError as e:
        print(f"Telegram Error (text): {e}")


def send_chart_telegram(df, symbol, signal, entry_price=None, sl=None, tp_list=None):
    try:
        df_plot = df.copy().tail(100)
        df_plot.index.name = 'Date'
        addplots = [
            mpf.make_addplot(df_plot['supertrend'], color='lime' if signal == 'BUY' else 'red'),
            mpf.make_addplot(df_plot['ema200'], color='blue'),
            mpf.make_addplot(df_plot['tenkan_sen'], color='orange'),
            mpf.make_addplot(df_plot['kijun_sen'], color='purple'),
        ]
        title = f"{symbol} - {signal}"
        fig, _ = mpf.plot(
            df_plot,
            type='candle',
            style='charles',
            title=title,
            volume=False,
            addplot=addplots,
            returnfig=True,
            figratio=(16, 9),
            figscale=1.2
        )
        buf = io.BytesIO()
        fig.savefig(buf, format='png')
        buf.seek(0)
        plt.close(fig)
        caption = f"📊 {symbol} Signal: {signal}\n"
        if entry_price:
            caption += f"🎯 Entry: {entry_price:.2f}\n"
        if sl:
            caption += f"🛡 SL: {sl:.2f}\n"
        if tp_list:
            caption += "\n".join([f"TP{i+1}: {tp:.2f}" for i, tp in enumerate(tp_list)])
        bot.send_photo(chat_id=CHAT_ID, photo=buf, caption=caption, timeout=60)
    except Exception as e:
        print(f"Telegram Error (chart): {e}")


# ==== Sinyal dan Manajemen Posisi ====
def get_signal(df):
    latest = df.iloc[-1]
    trend = 'UP' if latest['close'] > latest['ema200'] else 'DOWN'
    buy_signal = (
        trend == 'UP' and
        latest['close'] > latest['supertrend'] and
        latest['tenkan_sen'] > latest['kijun_sen']
    )
    sell_signal = (
        trend == 'DOWN' and
        latest['close'] < latest['supertrend'] and
        latest['tenkan_sen'] < latest['kijun_sen']
    )
    if buy_signal:
        return 'BUY'
    elif sell_signal:
        return 'SELL'
    return None


def manage_positions(symbol, signal, price, df):
    if symbol in open_positions:
        current = open_positions[symbol]
        if current['signal'] != signal:
            send_telegram(f"🔄 {symbol} signal changed to {signal}, closing {current['signal']} position")
            del open_positions[symbol]
    
    if symbol not in open_positions:
        sl = price * (1 - STOP_LOSS_PERCENT) if signal == 'BUY' else price * (1 + STOP_LOSS_PERCENT)
        tp_prices = [price * (1 + level) if signal == 'BUY' else price * (1 - level) for level in TP_LEVELS]
        open_positions[symbol] = {
            'entry': price,
            'signal': signal,
            'sl': sl,
            'tp': tp_prices,
            'hit': []
        }
        send_telegram(f"{'🟩' if signal=='BUY' else '🟥'} OPEN {signal} {symbol} at {price:.2f}\nSL: {sl:.2f}\n" +
                      "\n".join([f"TP{i+1}: {tp:.2f}" for i, tp in enumerate(tp_prices)]))
        send_chart_telegram(df, symbol, signal, price, sl, tp_prices)


def check_tp_sl(symbol, price):
    if symbol in open_positions:
        pos = open_positions[symbol]
        if (pos['signal'] == 'BUY' and price <= pos['sl']) or (pos['signal'] == 'SELL' and price >= pos['sl']):
            send_telegram(f"💥 SL hit for {symbol} at {price:.2f}")
            del open_positions[symbol]
            return
        for i, tp in enumerate(pos['tp']):
            if i in pos['hit']:
                continue
            if (pos['signal'] == 'BUY' and price >= tp) or (pos['signal'] == 'SELL' and price <= tp):
                send_telegram(f"✅ TP{i+1} hit for {symbol} at {price:.2f}")
                pos['hit'].append(i)

# ==== Main Loop ====
while True:
    for symbol in SYMBOLS:
        try:
            df = get_klines(symbol, INTERVAL)
            df = apply_indicators(df)
            signal = get_signal(df)
            price = df.iloc[-1]['close']
            check_tp_sl(symbol, price)
            if signal:
                manage_positions(symbol, signal, price, df)
        except Exception as e:
            print(f"Error processing {symbol}: {e}")
    time.sleep(60)
