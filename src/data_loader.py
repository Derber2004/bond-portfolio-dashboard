import requests
import time
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MOEX_BASE = "https://iss.moex.com/iss"

def fetch_json(url, params=None):
    """Обёртка для запросов с обработкой ошибок."""
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Ошибка при запросе {url}: {e}")
        return None

def get_bond_info_from_moex(isin):
    """
    Получает информацию об облигации по ISIN.
    Возвращает словарь с ключами: isin, ticker, name, nominal, coupon_rate,
    coupon_frequency, maturity_date, currency, type.
    """
    url = f"{MOEX_BASE}/securities/{isin}.json"
    data = fetch_json(url)
    if not data:
        return None
    
    # Основной блок информации
    sec_data = data.get("description", {}).get("data", [])
    if not sec_data:
        return None
    # Колонки в description: обычно: 'SECID','NAME','SHORTNAME','ISIN','REGNUMBER',
    # 'ISSUESIZE','FACEVALUE','FACEUNIT','ISSUEDATE','MATDATE','COUPONPERCENT',
    # 'COUPONPERIOD','COUPONVALUE','TYPENAME','GROUP','GROUPNAME','CURRENCYID'
    cols = data["description"]["columns"]
    row = sec_data[0]
    
    def get_val(col_name):
        try:
            idx = cols.index(col_name)
            return row[idx]
        except (ValueError, IndexError):
            return None
    
    isin_code = get_val("ISIN") or isin
    ticker = get_val("SECID")
    name = get_val("SHORTNAME") or get_val("NAME")
    nominal = float(get_val("FACEVALUE")) if get_val("FACEVALUE") else None
    coupon_rate = float(get_val("COUPONPERCENT")) if get_val("COUPONPERCENT") else None
    coupon_period = int(get_val("COUPONPERIOD")) if get_val("COUPONPERIOD") else None  # в днях
    maturity_date_str = get_val("MATDATE")  # YYYY-MM-DD
    currency = get_val("FACEUNIT") or get_val("CURRENCYID")
    # Тип возьмём из группы бумаг (например, "Облигации федерального займа" -> ОФЗ)
    group = get_val("GROUPNAME")
    if "федерального займа" in group.lower():
        bond_type = "ОФЗ"
    elif "корпоративные" in group.lower():
        bond_type = "корпоративная"
    else:
        bond_type = "прочая"
    
    # Определяем частоту купона в разах в год (приблизительно)
    if coupon_period:
        freq = round(365 / coupon_period)
    else:
        freq = 2  # по умолчанию

    return {
        "isin": isin_code,
        "ticker": ticker,
        "name": name,
        "nominal": nominal,
        "coupon_rate": coupon_rate,
        "coupon_frequency": freq,
        "maturity_date": maturity_date_str,
        "currency": currency,
        "type": bond_type,
        "credit_rating": None  # можно добавить позже из другого источника
    }

def fetch_price_history(isin, from_date, till_date):
    """
    Получает исторические цены и НКД для облигации за заданный период.
    Возвращает список словарей [{date, price, nkd}].
    """
    url = f"{MOEX_BASE}/history/engines/stock/markets/bonds/securities/{isin}.json"
    params = {
        "from": from_date,
        "till": till_date,
        "iss.meta": "off",
        "iss.json": "extended",
        "history.columns": "TRADEDATE,CLOSE,ACCRUEDINT",  # дата, цена закрытия, НКД
        "limit": 500
    }
    data = fetch_json(url, params)
    if not data:
        return []
    
    history_data = data.get("history", {}).get("data", [])
    cols = data["history"]["columns"]
    idx_date = cols.index("TRADEDATE")
    idx_close = cols.index("CLOSE")
    idx_nkd = cols.index("ACCRUEDINT")
    
    result = []
    for row in history_data:
        d = row[idx_date]
        price = row[idx_close]
        nkd = row[idx_nkd]
        if price is not None:
            result.append({
                "date": d,
                "price": float(price),  # цена в % от номинала
                "nkd": float(nkd) if nkd is not None else 0.0
            })
    return result

def load_bonds_and_prices(conn, isin_list):
    """
    Основная функция: для каждого ISIN загружает параметры облигации и
    историю цен за последний год, сохраняет в БД.
    """
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    
    for isin in isin_list:
        logger.info(f"Обработка {isin}")
        # Получаем информацию об облигации
        info = get_bond_info_from_moex(isin)
        if not info:
            logger.warning(f"Не удалось получить данные по {isin}, пропускаем.")
            continue
        
        # Добавляем/обновляем облигацию в базе
        add_bond(conn, **info)
        bond_id = get_bond_id_by_isin(conn, isin)
        if not bond_id:
            logger.error(f"Не удалось найти bond_id для {isin}")
            continue
        
        # Получаем историю цен и НКД
        prices = fetch_price_history(isin, start_date, end_date)
        logger.info(f"Загружено {len(prices)} записей цен для {isin}")
        for p in prices:
            add_price(conn, bond_id, p["date"], p["price"], p["nkd"])
        time.sleep(0.2)  # небольшая вежливая пауза

# Список ISIN для теста (можно расширить)
DEFAULT_ISINS = [
    "SU26226RMFS5",   # ОФЗ-ПД 26226
    "SU26238RMFS4",   # ОФЗ-ПД 26238
    "RU000A103R61",   # Газпром нефть 003P-01R (корп)
    "RU000A105A92",   # Замещающая облигация Газпрома (пример)
]