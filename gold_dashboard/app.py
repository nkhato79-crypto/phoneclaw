import os
import pandas as pd
import numpy as np
import json
import plotly.graph_objects as go
import plotly.utils
from plotly.subplots import make_subplots
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import sys
import warnings
import datetime
import requests

warnings.filterwarnings('ignore')

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from model import Kronos, KronosTokenizer, KronosPredictor
    MODEL_AVAILABLE = True
except ImportError:
    MODEL_AVAILABLE = False
    print("Warning: Kronos model not available, predictions will use simulation mode")

app = Flask(__name__)
CORS(app)

tokenizer = None
model = None
predictor = None

AVAILABLE_MODELS = {
    'kronos-mini': {
        'name': 'Kronos-mini',
        'model_id': 'NeoQuasar/Kronos-mini',
        'tokenizer_id': 'NeoQuasar/Kronos-Tokenizer-2k',
        'context_length': 2048,
        'params': '4.1M',
        'description': 'Fast predictions, lower accuracy'
    },
    'kronos-small': {
        'name': 'Kronos-small',
        'model_id': 'NeoQuasar/Kronos-small',
        'tokenizer_id': 'NeoQuasar/Kronos-Tokenizer-base',
        'context_length': 512,
        'params': '24.7M',
        'description': 'Balanced speed and accuracy'
    },
    'kronos-base': {
        'name': 'Kronos-base',
        'model_id': 'NeoQuasar/Kronos-base',
        'tokenizer_id': 'NeoQuasar/Kronos-Tokenizer-base',
        'context_length': 512,
        'params': '102.3M',
        'description': 'Best prediction quality'
    }
}


def fetch_gold_data_fmp(api_key, interval='1hour', outputsize=500):
    """Fetch gold (XAU/USD) data from Financial Modeling Prep API."""
    if interval in ('1min', '5min', '15min', '30min', '1hour', '4hour'):
        url = f"https://financialmodelingprep.com/api/v3/historical-chart/{interval}/XAUUSD"
        params = {'apikey': api_key}
    else:
        url = "https://financialmodelingprep.com/api/v3/historical-price-full/XAUUSD"
        params = {'apikey': api_key, 'timeseries': outputsize}

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict) and 'historical' in data:
        rows = data['historical']
    else:
        return None

    df = pd.DataFrame(rows)
    df['timestamps'] = pd.to_datetime(df['date'])
    df = df.sort_values('timestamps').reset_index(drop=True)

    for col in ['open', 'high', 'low', 'close']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    if 'volume' in df.columns:
        df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
    else:
        df['volume'] = 0

    df = df[['timestamps', 'open', 'high', 'low', 'close', 'volume']].dropna()
    return df


def generate_sample_gold_data(days=90, interval_minutes=60):
    """Generate realistic sample gold price data for demo mode."""
    np.random.seed(42)
    periods = days * (24 * 60 // interval_minutes)
    timestamps = pd.date_range(
        end=datetime.datetime.now(),
        periods=periods,
        freq=f'{interval_minutes}min'
    )

    base_price = 2350.0
    returns = np.random.normal(0.00002, 0.003, periods)
    prices = [base_price]
    for r in returns[1:]:
        prices.append(prices[-1] * (1 + r))

    df = pd.DataFrame({
        'timestamps': timestamps,
        'close': prices
    })
    df['open'] = df['close'].shift(1).fillna(df['close'])
    df['high'] = df[['open', 'close']].max(axis=1) * (1 + np.abs(np.random.normal(0, 0.002, periods)))
    df['low'] = df[['open', 'close']].min(axis=1) * (1 - np.abs(np.random.normal(0, 0.002, periods)))
    df['volume'] = np.random.randint(1000, 50000, periods).astype(float)

    return df[['timestamps', 'open', 'high', 'low', 'close', 'volume']]


def simulate_prediction(df, lookback, pred_len):
    """Generate simulated predictions when model is not loaded."""
    hist = df.iloc[-lookback:]
    last_close = hist['close'].iloc[-1]
    last_open = hist['open'].iloc[-1]
    last_high = hist['high'].iloc[-1]
    last_low = hist['low'].iloc[-1]
    avg_volume = hist['volume'].mean()
    volatility = hist['close'].pct_change().std()

    pred_close = [last_close]
    for _ in range(pred_len - 1):
        change = np.random.normal(0.0001, volatility)
        pred_close.append(pred_close[-1] * (1 + change))

    pred_df = pd.DataFrame({
        'open': [pred_close[0]] + pred_close[:-1],
        'close': pred_close,
        'high': [c * (1 + abs(np.random.normal(0, volatility * 0.5))) for c in pred_close],
        'low': [c * (1 - abs(np.random.normal(0, volatility * 0.5))) for c in pred_close],
        'volume': np.random.normal(avg_volume, avg_volume * 0.2, pred_len).clip(0)
    })

    return pred_df


def compute_trading_signals(hist_df, pred_df):
    """Compute buy/sell signals from prediction data."""
    signals = []
    pred_closes = pred_df['close'].values
    last_hist_close = hist_df['close'].iloc[-1]

    sma_5 = pd.Series(pred_closes).rolling(5, min_periods=1).mean()
    sma_20 = pd.Series(pred_closes).rolling(20, min_periods=1).mean()

    for i in range(len(pred_closes)):
        signal = 'HOLD'
        strength = 0.0

        price_change_pct = (pred_closes[i] - last_hist_close) / last_hist_close * 100
        if i > 0:
            short_momentum = (pred_closes[i] - pred_closes[max(0, i - 5)]) / pred_closes[max(0, i - 5)] * 100
        else:
            short_momentum = price_change_pct

        if sma_5.iloc[i] > sma_20.iloc[i] and price_change_pct > 0.1:
            signal = 'BUY'
            strength = min(abs(price_change_pct) / 2, 1.0)
        elif sma_5.iloc[i] < sma_20.iloc[i] and price_change_pct < -0.1:
            signal = 'SELL'
            strength = min(abs(price_change_pct) / 2, 1.0)

        if abs(short_momentum) > 0.5:
            strength = min(strength + 0.3, 1.0)

        signals.append({
            'index': i,
            'signal': signal,
            'strength': round(strength, 2),
            'price_change_pct': round(price_change_pct, 4),
            'predicted_price': round(float(pred_closes[i]), 2)
        })

    return signals


def compute_backtest(df, lookback=400, pred_len=120, step=50):
    """Run rolling backtest across the dataset."""
    results = []
    total_points = len(df)

    for start in range(0, total_points - lookback - pred_len, step):
        hist = df.iloc[start:start + lookback]
        actual = df.iloc[start + lookback:start + lookback + pred_len]

        if len(actual) < pred_len:
            break

        if MODEL_AVAILABLE and predictor is not None:
            try:
                required_cols = ['open', 'high', 'low', 'close']
                if 'volume' in df.columns:
                    required_cols.append('volume')
                x_df = hist[required_cols]
                x_timestamp = hist['timestamps'].reset_index(drop=True)
                y_timestamp = actual['timestamps'].reset_index(drop=True)

                pred_df = predictor.predict(
                    df=x_df, x_timestamp=x_timestamp, y_timestamp=y_timestamp,
                    pred_len=pred_len, T=1.0, top_p=0.9, sample_count=1
                )
            except Exception:
                pred_df = simulate_prediction(hist, lookback, pred_len)
        else:
            pred_df = simulate_prediction(hist, lookback, pred_len)

        pred_direction = 1 if pred_df['close'].iloc[-1] > pred_df['close'].iloc[0] else -1
        actual_direction = 1 if actual['close'].iloc[-1] > actual['close'].iloc[0] else -1

        mae = np.mean(np.abs(pred_df['close'].values - actual['close'].values))
        mape = np.mean(np.abs((pred_df['close'].values - actual['close'].values) / actual['close'].values)) * 100
        direction_correct = pred_direction == actual_direction

        results.append({
            'start_date': hist['timestamps'].iloc[0].isoformat(),
            'end_date': actual['timestamps'].iloc[-1].isoformat(),
            'mae': round(float(mae), 2),
            'mape': round(float(mape), 4),
            'direction_correct': direction_correct,
            'pred_final': round(float(pred_df['close'].iloc[-1]), 2),
            'actual_final': round(float(actual['close'].iloc[-1]), 2)
        })

    if not results:
        return {'error': 'Not enough data for backtesting'}

    direction_accuracy = sum(1 for r in results if r['direction_correct']) / len(results) * 100
    avg_mae = np.mean([r['mae'] for r in results])
    avg_mape = np.mean([r['mape'] for r in results])

    return {
        'total_windows': len(results),
        'direction_accuracy': round(direction_accuracy, 1),
        'avg_mae': round(float(avg_mae), 2),
        'avg_mape': round(float(avg_mape), 4),
        'results': results
    }


@app.route('/')
def index():
    return render_template('gold_dashboard.html')


@app.route('/api/gold-data', methods=['POST'])
def get_gold_data():
    """Fetch or generate gold price data."""
    try:
        data = request.get_json() or {}
        api_key = data.get('api_key', '')
        interval = data.get('interval', '1hour')

        if api_key:
            df = fetch_gold_data_fmp(api_key, interval=interval)
            if df is None:
                return jsonify({'error': 'Failed to fetch gold data from API'}), 400
            source = 'live'
        else:
            df = generate_sample_gold_data()
            source = 'sample'

        cache_path = os.path.join(os.path.dirname(__file__), '_gold_cache.csv')
        df.to_csv(cache_path, index=False)

        current_price = float(df['close'].iloc[-1])
        price_24h_ago = float(df['close'].iloc[-min(24, len(df))])
        change_24h = (current_price - price_24h_ago) / price_24h_ago * 100
        high_24h = float(df['high'].iloc[-min(24, len(df)):].max())
        low_24h = float(df['low'].iloc[-min(24, len(df)):].min())
        avg_volume = float(df['volume'].iloc[-min(24, len(df)):].mean())

        chart_df = df.tail(200)
        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03,
            row_heights=[0.7, 0.3]
        )
        fig.add_trace(go.Candlestick(
            x=chart_df['timestamps'], open=chart_df['open'],
            high=chart_df['high'], low=chart_df['low'], close=chart_df['close'],
            name='XAU/USD', increasing_line_color='#00c853', decreasing_line_color='#ff1744'
        ), row=1, col=1)

        sma_20 = chart_df['close'].rolling(20).mean()
        sma_50 = chart_df['close'].rolling(50).mean()
        fig.add_trace(go.Scatter(x=chart_df['timestamps'], y=sma_20, name='SMA 20',
                                 line=dict(color='#ffd600', width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=chart_df['timestamps'], y=sma_50, name='SMA 50',
                                 line=dict(color='#2979ff', width=1)), row=1, col=1)

        colors = ['#00c853' if c >= o else '#ff1744'
                  for c, o in zip(chart_df['close'], chart_df['open'])]
        fig.add_trace(go.Bar(x=chart_df['timestamps'], y=chart_df['volume'],
                             name='Volume', marker_color=colors, opacity=0.5), row=2, col=1)

        fig.update_layout(
            template='plotly_dark', height=500,
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            xaxis_rangeslider_visible=False,
            margin=dict(l=50, r=20, t=30, b=30),
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
            font=dict(color='#e0e0e0')
        )
        fig.update_xaxes(gridcolor='rgba(255,255,255,0.1)')
        fig.update_yaxes(gridcolor='rgba(255,255,255,0.1)')

        return jsonify({
            'success': True,
            'source': source,
            'data_points': len(df),
            'current_price': round(current_price, 2),
            'change_24h': round(change_24h, 2),
            'high_24h': round(high_24h, 2),
            'low_24h': round(low_24h, 2),
            'avg_volume': round(avg_volume, 0),
            'chart': json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/predict-gold', methods=['POST'])
def predict_gold():
    """Run Kronos prediction on gold data."""
    try:
        data = request.get_json() or {}
        lookback = int(data.get('lookback', 400))
        pred_len = int(data.get('pred_len', 120))
        temperature = float(data.get('temperature', 1.0))
        top_p = float(data.get('top_p', 0.9))

        cache_path = os.path.join(os.path.dirname(__file__), '_gold_cache.csv')
        if not os.path.exists(cache_path):
            return jsonify({'error': 'No gold data loaded. Fetch data first.'}), 400

        df = pd.read_csv(cache_path)
        df['timestamps'] = pd.to_datetime(df['timestamps'])

        if len(df) < lookback:
            return jsonify({'error': f'Need at least {lookback} data points, have {len(df)}'}), 400

        hist_df = df.tail(lookback + pred_len)
        x_df = hist_df.head(lookback)

        if MODEL_AVAILABLE and predictor is not None:
            try:
                required_cols = ['open', 'high', 'low', 'close']
                if 'volume' in df.columns:
                    required_cols.append('volume')

                x_timestamp = x_df['timestamps'].reset_index(drop=True)
                remaining = hist_df.tail(pred_len)
                y_timestamp = remaining['timestamps'].reset_index(drop=True) if len(remaining) >= pred_len else None

                if y_timestamp is None or len(y_timestamp) < pred_len:
                    last_ts = x_timestamp.iloc[-1]
                    time_diff = x_timestamp.iloc[-1] - x_timestamp.iloc[-2]
                    y_timestamp = pd.Series(pd.date_range(start=last_ts + time_diff, periods=pred_len, freq=time_diff))

                pred_df = predictor.predict(
                    df=x_df[required_cols], x_timestamp=x_timestamp,
                    y_timestamp=y_timestamp, pred_len=pred_len,
                    T=temperature, top_p=top_p, sample_count=1
                )
                prediction_mode = 'kronos'
            except Exception as e:
                pred_df = simulate_prediction(x_df, lookback, pred_len)
                prediction_mode = f'simulation (model error: {str(e)[:80]})'
        else:
            pred_df = simulate_prediction(x_df, lookback, pred_len)
            prediction_mode = 'simulation'

        signals = compute_trading_signals(x_df, pred_df)

        last_ts = x_df['timestamps'].iloc[-1]
        time_diff = x_df['timestamps'].iloc[-1] - x_df['timestamps'].iloc[-2]
        pred_timestamps = pd.date_range(start=last_ts + time_diff, periods=pred_len, freq=time_diff)

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                            row_heights=[0.7, 0.3])

        fig.add_trace(go.Candlestick(
            x=x_df['timestamps'], open=x_df['open'], high=x_df['high'],
            low=x_df['low'], close=x_df['close'],
            name='Historical', increasing_line_color='#00c853', decreasing_line_color='#ff1744'
        ), row=1, col=1)

        fig.add_trace(go.Candlestick(
            x=pred_timestamps, open=pred_df['open'], high=pred_df['high'],
            low=pred_df['low'], close=pred_df['close'],
            name='Predicted', increasing_line_color='#69f0ae', decreasing_line_color='#ff8a80'
        ), row=1, col=1)

        buy_signals = [s for s in signals if s['signal'] == 'BUY' and s['strength'] > 0.3]
        sell_signals = [s for s in signals if s['signal'] == 'SELL' and s['strength'] > 0.3]

        if buy_signals:
            fig.add_trace(go.Scatter(
                x=[pred_timestamps[s['index']] for s in buy_signals],
                y=[s['predicted_price'] * 0.998 for s in buy_signals],
                mode='markers', name='Buy Signal',
                marker=dict(symbol='triangle-up', size=12, color='#00e676')
            ), row=1, col=1)

        if sell_signals:
            fig.add_trace(go.Scatter(
                x=[pred_timestamps[s['index']] for s in sell_signals],
                y=[s['predicted_price'] * 1.002 for s in sell_signals],
                mode='markers', name='Sell Signal',
                marker=dict(symbol='triangle-down', size=12, color='#ff5252')
            ), row=1, col=1)

        hist_colors = ['#00c853' if c >= o else '#ff1744'
                       for c, o in zip(x_df['close'], x_df['open'])]
        pred_colors = ['#69f0ae' if c >= o else '#ff8a80'
                       for c, o in zip(pred_df['close'], pred_df['open'])]
        fig.add_trace(go.Bar(x=x_df['timestamps'], y=x_df['volume'],
                             name='Hist Volume', marker_color=hist_colors, opacity=0.5), row=2, col=1)
        fig.add_trace(go.Bar(x=pred_timestamps, y=pred_df['volume'],
                             name='Pred Volume', marker_color=pred_colors, opacity=0.3), row=2, col=1)

        fig.update_layout(
            template='plotly_dark', height=500,
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            xaxis_rangeslider_visible=False,
            margin=dict(l=50, r=20, t=30, b=30),
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
            font=dict(color='#e0e0e0')
        )
        fig.update_xaxes(gridcolor='rgba(255,255,255,0.1)')
        fig.update_yaxes(gridcolor='rgba(255,255,255,0.1)')

        current_price = float(x_df['close'].iloc[-1])
        pred_final = float(pred_df['close'].iloc[-1])
        pred_change = (pred_final - current_price) / current_price * 100
        pred_high = float(pred_df['high'].max())
        pred_low = float(pred_df['low'].min())

        buy_count = sum(1 for s in signals if s['signal'] == 'BUY')
        sell_count = sum(1 for s in signals if s['signal'] == 'SELL')
        overall = 'BULLISH' if buy_count > sell_count else ('BEARISH' if sell_count > buy_count else 'NEUTRAL')

        return jsonify({
            'success': True,
            'prediction_mode': prediction_mode,
            'chart': json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder),
            'metrics': {
                'current_price': round(current_price, 2),
                'predicted_price': round(pred_final, 2),
                'predicted_change_pct': round(pred_change, 2),
                'predicted_high': round(pred_high, 2),
                'predicted_low': round(pred_low, 2),
                'outlook': overall,
                'buy_signals': buy_count,
                'sell_signals': sell_count,
                'hold_signals': len(signals) - buy_count - sell_count
            },
            'signals': signals[:20]
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/backtest', methods=['POST'])
def backtest():
    """Run backtesting on gold data."""
    try:
        data = request.get_json() or {}
        lookback = int(data.get('lookback', 400))
        pred_len = int(data.get('pred_len', 120))
        step = int(data.get('step', 100))

        cache_path = os.path.join(os.path.dirname(__file__), '_gold_cache.csv')
        if not os.path.exists(cache_path):
            return jsonify({'error': 'No gold data loaded. Fetch data first.'}), 400

        df = pd.read_csv(cache_path)
        df['timestamps'] = pd.to_datetime(df['timestamps'])

        results = compute_backtest(df, lookback, pred_len, step)

        if 'error' in results:
            return jsonify({'error': results['error']}), 400

        fig = go.Figure()
        dates = [r['start_date'] for r in results['results']]
        maes = [r['mae'] for r in results['results']]
        directions = [r['direction_correct'] for r in results['results']]

        fig.add_trace(go.Bar(
            x=dates, y=maes, name='MAE',
            marker_color=['#00c853' if d else '#ff1744' for d in directions]
        ))

        fig.update_layout(
            template='plotly_dark', height=350,
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=50, r=20, t=30, b=30),
            xaxis_title='Window Start', yaxis_title='MAE ($)',
            font=dict(color='#e0e0e0')
        )
        fig.update_xaxes(gridcolor='rgba(255,255,255,0.1)')
        fig.update_yaxes(gridcolor='rgba(255,255,255,0.1)')

        return jsonify({
            'success': True,
            'total_windows': results['total_windows'],
            'direction_accuracy': results['direction_accuracy'],
            'avg_mae': results['avg_mae'],
            'avg_mape': results['avg_mape'],
            'chart': json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/load-model', methods=['POST'])
def load_model():
    global tokenizer, model, predictor
    try:
        if not MODEL_AVAILABLE:
            return jsonify({'error': 'Kronos model library not installed'}), 400

        data = request.get_json() or {}
        model_key = data.get('model_key', 'kronos-small')
        device = data.get('device', 'cpu')

        if model_key not in AVAILABLE_MODELS:
            return jsonify({'error': f'Unknown model: {model_key}'}), 400

        cfg = AVAILABLE_MODELS[model_key]
        tokenizer = KronosTokenizer.from_pretrained(cfg['tokenizer_id'])
        model = Kronos.from_pretrained(cfg['model_id'])
        predictor = KronosPredictor(model, tokenizer, device=device, max_context=cfg['context_length'])

        return jsonify({
            'success': True,
            'message': f'{cfg["name"]} loaded ({cfg["params"]}) on {device}'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/model-status')
def model_status():
    return jsonify({
        'available': MODEL_AVAILABLE,
        'loaded': predictor is not None,
        'models': AVAILABLE_MODELS
    })


if __name__ == '__main__':
    print("Starting Gold Trading Dashboard...")
    print(f"Kronos model available: {MODEL_AVAILABLE}")
    app.run(debug=True, host='0.0.0.0', port=7071)
