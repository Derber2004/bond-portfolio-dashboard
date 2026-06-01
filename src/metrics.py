import numpy as np
from datetime import date
from scipy.optimize import newton
import pandas as pd

def calc_ytm(price_percent, nominal, coupon_rate, frequency, maturity_date, current_date=None):
    """Доходность к погашению (YTM) через численное решение (Ньютон)."""
    if price_percent is None or nominal is None or coupon_rate is None or not maturity_date:
        return None

    if current_date is None:
        current_date = date.today()
    elif isinstance(current_date, str):
        current_date = date.fromisoformat(current_date)

    if isinstance(maturity_date, str):
        maturity_date = date.fromisoformat(maturity_date)

    days_to_maturity = (maturity_date - current_date).days
    if days_to_maturity <= 0:
        return None

    periods_per_year = frequency
    total_periods = int(np.ceil(days_to_maturity / 365.0 * periods_per_year))
    if total_periods == 0:
        total_periods = 1

    coupon_payment = nominal * (coupon_rate / 100.0) / periods_per_year
    dirty_price = price_percent / 100.0 * nominal

    def npv(y):
        y = y / 100.0
        pv = 0.0
        for i in range(1, total_periods + 1):
            t = i / periods_per_year
            cf = coupon_payment
            if i == total_periods:
                cf += nominal
            pv += cf / ((1 + y) ** t)
        return pv - dirty_price

    initial_guess = max(coupon_rate / 100.0, 0.001)
    try:
        ytm_decimal = newton(npv, initial_guess, tol=1e-6, maxiter=1000)
        return ytm_decimal * 100.0
    except (RuntimeError, ValueError):
        return None

def modified_duration(price_percent, nominal, coupon_rate, frequency, maturity_date, ytm=None):
    """Модифицированная дюрация (в процентах)."""
    if ytm is None:
        ytm = calc_ytm(price_percent, nominal, coupon_rate, frequency, maturity_date)
    if ytm is None:
        return None

    y = ytm / 100.0
    periods_per_year = frequency

    if isinstance(maturity_date, str):
        maturity_date = date.fromisoformat(maturity_date)
    current_date = date.today()
    days_to_maturity = (maturity_date - current_date).days
    total_periods = int(np.ceil(days_to_maturity / 365.0 * periods_per_year))

    coupon_payment = nominal * (coupon_rate / 100.0) / periods_per_year
    dirty_price = price_percent / 100.0 * nominal

    macaulay = 0.0
    for i in range(1, total_periods + 1):
        t = i / periods_per_year
        cf = coupon_payment
        if i == total_periods:
            cf += nominal
        pv_cf = cf / ((1 + y) ** t)
        macaulay += t * pv_cf
    macaulay /= dirty_price

    mod_dur = macaulay / (1 + y / periods_per_year)
    return mod_dur * 100

def convexity(price_percent, nominal, coupon_rate, frequency, maturity_date, ytm=None):
    """Выпуклость (convexity)."""
    if ytm is None:
        ytm = calc_ytm(price_percent, nominal, coupon_rate, frequency, maturity_date)
    if ytm is None:
        return None

    y = ytm / 100.0
    periods_per_year = frequency

    if isinstance(maturity_date, str):
        maturity_date = date.fromisoformat(maturity_date)
    current_date = date.today()
    days_to_maturity = (maturity_date - current_date).days
    total_periods = int(np.ceil(days_to_maturity / 365.0 * periods_per_year))

    coupon_payment = nominal * (coupon_rate / 100.0) / periods_per_year
    dirty_price = price_percent / 100.0 * nominal

    conv = 0.0
    for i in range(1, total_periods + 1):
        t = i / periods_per_year
        cf = coupon_payment
        if i == total_periods:
            cf += nominal
        pv_cf = cf / ((1 + y) ** t)
        conv += t * (t + 1) * pv_cf
    conv = conv / ((1 + y) ** 2) / dirty_price
    return conv / (periods_per_year ** 2)

def calculate_historical_var(conn, confidence=0.95, horizon_days=10):
    """
    Исторический VaR портфеля на основе дневных изменений цен облигаций.
    Использует последние доступные цены для всех бумаг в портфеле и их веса.
    Возвращает VaR в денежном выражении.
    """
    # Получаем текущие позиции с последней ценой через оконную функцию
    positions = conn.execute("""
        WITH last_prices AS (
            SELECT bond_id, price, nkd,
                   ROW_NUMBER() OVER (PARTITION BY bond_id ORDER BY date DESC) as rn
            FROM prices
        )
        SELECT b.isin, b.nominal,
               SUM(CASE WHEN t.type='BUY' THEN t.quantity ELSE -t.quantity END) as qty,
               lp.price as current_price, lp.nkd
        FROM bonds b
        JOIN transactions t ON b.id = t.bond_id
        JOIN last_prices lp ON b.id = lp.bond_id AND lp.rn = 1
        GROUP BY b.id
        HAVING qty > 0
    """).fetchall()

    if not positions:
        return None

    values = []
    daily_returns = []
    total_value = 0.0
    for pos in positions:
        val = pos["qty"] * (pos["current_price"] / 100.0 * pos["nominal"] + pos["nkd"])
        values.append(val)
        total_value += val

    for pos in positions:
        isin = pos["isin"]
        price_history = conn.execute("""
            SELECT date, price 
            FROM prices p
            JOIN bonds b ON p.bond_id = b.id
            WHERE b.isin = ?
            ORDER BY date ASC
        """, (isin,)).fetchall()
        if len(price_history) < 2:
            continue
        prices = [row["price"] for row in price_history]
        returns = np.diff(prices) / np.array(prices[:-1]) * 100
        daily_returns.append(returns)

    if not daily_returns:
        return None

    min_len = min(len(r) for r in daily_returns)
    aligned = np.array([r[-min_len:] for r in daily_returns])
    weights = np.array([v / total_value for v in values])
    portfolio_daily = (aligned.T * weights).sum(axis=1)

    var_daily_percent = np.percentile(portfolio_daily, 100 * (1 - confidence))
    var_daily = total_value * var_daily_percent / 100.0
    var_horizon = var_daily * np.sqrt(horizon_days)
    return -var_horizon

def get_portfolio_metrics(conn):
    """Возвращает словарь с ключевыми портфельными метриками."""
    from src.database import get_portfolio_positions

    positions = get_portfolio_positions(conn)
    if not positions:
        return None

    bonds_info = {}
    for row in conn.execute("SELECT isin, nominal, coupon_rate, coupon_frequency, maturity_date FROM bonds").fetchall():
        bonds_info[row['isin']] = row

    total_value = 0.0
    weighted_ytm_sum = 0.0
    weighted_dur_sum = 0.0
    weighted_conv_sum = 0.0

    for pos in positions:
        isin = pos["isin"]
        qty = pos["total_qty"]
        price_row = conn.execute("""
            SELECT p.price, p.nkd
            FROM prices p
            JOIN bonds b ON p.bond_id = b.id
            WHERE b.isin = ?
            ORDER BY p.date DESC LIMIT 1
        """, (isin,)).fetchone()
        if not price_row:
            continue
        price = price_row["price"]
        nkd = price_row["nkd"]
        nominal = pos["nominal"]

        value = qty * (price / 100.0 * nominal + nkd)
        total_value += value

        info = bonds_info.get(isin)
        if info:
            ytm = calc_ytm(price, info['nominal'], info['coupon_rate'], info['coupon_frequency'], info['maturity_date'])
            if ytm is not None:
                weighted_ytm_sum += ytm * value
            dur = modified_duration(price, info['nominal'], info['coupon_rate'], info['coupon_frequency'], info['maturity_date'], ytm)
            if dur is not None:
                weighted_dur_sum += dur * value
            conv = convexity(price, info['nominal'], info['coupon_rate'], info['coupon_frequency'], info['maturity_date'], ytm)
            if conv is not None:
                weighted_conv_sum += conv * value

    if total_value == 0:
        return None

    return {
        "total_value": total_value,
        "weighted_ytm": weighted_ytm_sum / total_value if weighted_ytm_sum else None,
        "weighted_duration": weighted_dur_sum / total_value if weighted_dur_sum else None,
        "weighted_convexity": weighted_conv_sum / total_value if weighted_conv_sum else None
    }