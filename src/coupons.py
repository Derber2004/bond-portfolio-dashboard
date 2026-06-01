from datetime import date
from dateutil.relativedelta import relativedelta
import pandas as pd
import logging

logger = logging.getLogger(__name__)

def generate_coupon_schedule(maturity_date_str, frequency, coupon_rate, nominal, current_date=None):
    """
    Генерирует даты будущих купонных выплат (от сегодня до погашения).
    Возвращает список словарей [{date, coupon}] или пустой список, если не хватает данных.
    """
    # Проверка на None для всех обязательных параметров
    if maturity_date_str is None or frequency is None or coupon_rate is None or nominal is None:
        logger.warning("generate_coupon_schedule: один из параметров None, пропускаем.")
        return []

    if current_date is None:
        current_date = date.today()
    if isinstance(maturity_date_str, str):
        maturity_date = date.fromisoformat(maturity_date_str)
    else:
        maturity_date = maturity_date_str

    # Периодичность в месяцах
    months = 12 // frequency
    if months < 1:
        months = 1

    coupon_payment = nominal * (coupon_rate / 100.0) / frequency

    schedule = []
    d = maturity_date
    while d > current_date:
        schedule.append({"date": d, "coupon": coupon_payment})
        d -= relativedelta(months=months)
    schedule.reverse()
    return schedule


def get_portfolio_coupon_flow(conn, current_date=None):
    """
    Собирает все будущие купонные выплаты для всего портфеля.
    Возвращает DataFrame: Дата, ISIN, Название, Купон на бумагу, Количество, Общая сумма.
    Пропускает облигации, у которых нет необходимых данных (купон, номинал и т.д.).
    """
    if current_date is None:
        current_date = date.today()

    positions = conn.execute("""
        SELECT b.isin, b.name, b.nominal, b.coupon_rate, b.coupon_frequency, b.maturity_date,
               SUM(CASE WHEN t.type='BUY' THEN t.quantity ELSE -t.quantity END) as qty
        FROM bonds b
        JOIN transactions t ON b.id = t.bond_id
        GROUP BY b.id
        HAVING qty > 0
    """).fetchall()

    all_payments = []
    for row in positions:
        # Пропускаем бумаги без купона или номинала
        if row["coupon_rate"] is None or row["nominal"] is None or row["coupon_frequency"] is None:
            logger.warning(f"Пропускаем {row['isin']}: неполные данные (купон/номинал/частота).")
            continue

        schedule = generate_coupon_schedule(
            row["maturity_date"],
            row["coupon_frequency"],
            row["coupon_rate"],
            row["nominal"],
            current_date
        )
        qty = row["qty"]
        for pay in schedule:
            all_payments.append({
                "Дата": pay["date"],
                "ISIN": row["isin"],
                "Название": row["name"],
                "Купон на бумагу": pay["coupon"],
                "Количество": qty,
                "Общая сумма": pay["coupon"] * qty
            })

    df = pd.DataFrame(all_payments)
    if not df.empty:
        df = df.sort_values("Дата").reset_index(drop=True)
    return df