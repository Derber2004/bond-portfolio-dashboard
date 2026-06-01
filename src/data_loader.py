import requests
import time
from datetime import datetime, timedelta
import logging
from datetime import date
import random
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MOEX_BASE = "https://iss.moex.com/iss"

# Импорты для БД
from src.database import add_bond, add_price, get_bond_id_by_isin, get_last_price_date

def fetch_json(url, params=None):
    """Обёртка для запросов с обработкой ошибок."""
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Ошибка при запросе {url}: {e}")
        return None

def get_secid_and_board(isin):
    url = f"{MOEX_BASE}/securities.json"
    params = {"q": isin}
    data = fetch_json(url, params)
    if not data:
        return None, None
    # ... (остальной код, обязательно извлечение BOARDID)

    # Нормализация ответа
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "securities" in item:
                data = item
                break
        else:
            return None, None
    if not isinstance(data, dict):
        return None, None

    sec_block = data.get("securities")
    if not sec_block:
        return None, None

    if isinstance(sec_block, dict):
        sec_data = sec_block.get("data", [])
        columns = sec_block.get("columns", [])
    elif isinstance(sec_block, list):
        sec_data = []
        columns = []
        for block in sec_block:
            if isinstance(block, dict):
                sec_data.extend(block.get("data", []))
                if not columns:
                    columns = block.get("columns", [])
    else:
        return None, None

    if not sec_data or not columns:
        return None, None

    isin_idx = columns.index("ISIN") if "ISIN" in columns else None
    secid_idx = columns.index("SECID") if "SECID" in columns else None
    boardid_idx = columns.index("BOARDID") if "BOARDID" in columns else None

    if isin_idx is None or secid_idx is None:
        return None, None

    for row in sec_data:
        if row[isin_idx] == isin:
            secid = row[secid_idx] if secid_idx is not None else None
            boardid = row[boardid_idx] if boardid_idx is not None else None
            return secid, boardid

    return None, None

def get_bond_info_from_moex(isin):
    """
    Получает детальную информацию об облигации, используя SECID.
    """
    secid, boardid = get_secid_and_board(isin)
    if not secid:
        logger.warning(f"Не удалось найти SECID для {isin}")
        return None

    # Запрос информации по бумаге
    url = f"{MOEX_BASE}/engines/stock/markets/bonds/securities/{secid}.json"
    params = {"iss.meta": "off", "iss.json": "extended"}
    data = fetch_json(url, params)
    if not data:
        return None

    # Обработка ответа
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "description" in item:
                data = item
                break
        else:
            logger.warning(f"Нет блока description для {secid}")
            return None
    if not isinstance(data, dict):
        return None

    desc = data.get("description")
    if not desc:
        return None

    desc_data = desc.get("data", [])
    if not desc_data:
        return None
    columns = desc["columns"]
    row = desc_data[0]

    def get_val(col_name):
        try:
            idx = columns.index(col_name)
            return row[idx]
        except (ValueError, IndexError):
            return None

    isin_code = get_val("ISIN") or isin
    ticker = get_val("SECID") or secid
    name = get_val("SHORTNAME") or get_val("NAME")
    nominal = float(get_val("FACEVALUE")) if get_val("FACEVALUE") else 1000.0
    coupon_rate = float(get_val("COUPONPERCENT")) if get_val("COUPONPERCENT") else 0.0
    coupon_period = int(get_val("COUPONPERIOD")) if get_val("COUPONPERIOD") else 182
    maturity_date_str = get_val("MATDATE")
    currency = get_val("FACEUNIT") or get_val("CURRENCYID") or "SUR"
    group = get_val("GROUPNAME")

    if group and isinstance(group, str):
        if "федерального займа" in group.lower():
            bond_type = "ОФЗ"
        elif "корпоративные" in group.lower():
            bond_type = "корпоративная"
        else:
            bond_type = "прочая"
    else:
        bond_type = "прочая"

    freq = round(365 / coupon_period) if coupon_period else 2

    return {
        "isin": isin_code,
        "ticker": ticker,
        "name": name,
        "nominal": nominal,
        "coupon_rate": coupon_rate,
        "coupon_frequency": freq,
        "maturity_date": maturity_date_str,
        "currency": currency,
        "bond_type": bond_type,
        "credit_rating": None
    }
def fetch_price_history(isin, from_date, till_date, boardid):
    """
    Получает исторические цены и НКД, используя SECID и BOARDID.
    """
    secid, _ = get_secid_and_board(isin)
    if not secid:
        logger.warning(f"Не удалось получить SECID для истории {isin}")
        return []

    # Правильный URL из официальной библиотеки MOEX ISS
    url = f"{MOEX_BASE}/history/engines/stock/markets/bonds/boards/{boardid}/securities/{secid}.json"
    params = {
        "from": from_date,
        "till": till_date,
        "iss.meta": "off",
        "iss.json": "extended",
        "history.columns": "TRADEDATE,CLOSE,ACCRUEDINT",
        "limit": 500
    }
    data = fetch_json(url, params)
    if not data:
        return []

    # Обработка ответа (аналогично предыдущим версиям, с учётом возможного списка)
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "history" in item:
                data = item
                break
        else:
            logger.warning(f"Нет блока history для {secid}")
            return []

    if not isinstance(data, dict):
        return []

    history_block = data.get("history")
    if not history_block:
        return []

    if isinstance(history_block, dict):
        rows = history_block.get("data", [])
        columns = history_block.get("columns", [])
    elif isinstance(history_block, list):
        rows = []
        columns = []
        for block in history_block:
            if isinstance(block, dict):
                rows.extend(block.get("data", []))
                if not columns:
                    columns = block.get("columns", [])
    else:
        return []

    if not rows or not columns:
        return []

    idx_date = columns.index("TRADEDATE") if "TRADEDATE" in columns else None
    idx_close = columns.index("CLOSE") if "CLOSE" in columns else None
    idx_nkd = columns.index("ACCRUEDINT") if "ACCRUEDINT" in columns else None

    if idx_date is None or idx_close is None:
        return []

    result = []
    for row in rows:
        d = row[idx_date]
        price = row[idx_close]
        nkd = row[idx_nkd] if idx_nkd is not None else 0.0
        if price is not None:
            result.append({
                "date": d,
                "price": float(price),
                "nkd": float(nkd) if nkd is not None else 0.0
            })
    return result

def load_bonds_and_prices(conn, isin_list):
    """Загружает облигации и историю цен для списка ISIN."""
    end_date = datetime.now().strftime("%Y-%m-%d")
    default_start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

    for isin in isin_list:
        logger.info(f"Обработка {isin}")
        # Получаем SECID и BOARDID
        secid, boardid = get_secid_and_board(isin)
        if not secid or not boardid:
            logger.warning(f"Не удалось найти SECID/BOARDID для {isin}")
            continue

        info = get_bond_info_from_moex(isin)
        if not info:
            logger.warning(f"Не удалось получить данные по {isin}, пропускаем.")
            continue

        add_bond(conn, **info)
        bond_id = get_bond_id_by_isin(conn, isin)
        if not bond_id:
            logger.error(f"Не удалось найти bond_id для {isin}")
            continue

        last_date = get_last_price_date(conn, isin)
        if last_date and last_date >= end_date:
            logger.info(f"Данные по {isin} актуальны. Пропускаем.")
            continue

        start_date = default_start
        if last_date:
            start_date = (datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

        logger.info(f"Загружаем цены для {isin} с {start_date} по {end_date}")
        prices = fetch_price_history(isin, start_date, end_date, boardid)
        logger.info(f"Загружено {len(prices)} записей цен для {isin}")
        for p in prices:
            add_price(conn, bond_id, p["date"], p["price"], p["nkd"])
        time.sleep(0.2)

# --------------------------------------------------------------------
# Загрузка из CSV и экспорт (без изменений, оставляем старые функции)
# --------------------------------------------------------------------
import csv
import io

def load_bonds_from_csv(conn, csv_file):
    """Загружает облигации из CSV-файла (колонка isin)."""
    content = csv_file.getvalue().decode('utf-8')
    reader = csv.DictReader(io.StringIO(content))
    isin_list = []
    for row in reader:
        isin = None
        for key in row:
            if key.lower() == 'isin':
                isin = row[key].strip()
                break
        if not isin and len(row) > 0:
            isin = list(row.values())[0].strip()
        if isin:
            isin_list.append(isin)

    added = 0
    skipped = 0
    for isin in isin_list:
        info = get_bond_info_from_moex(isin)
        if info:
            add_bond(conn, **info)
            added += 1
        else:
            skipped += 1
    conn.commit()
    return added, skipped

def prices_to_csv(conn, isin):
    """Экспортирует историю цен в CSV."""
    bond = conn.execute("SELECT name FROM bonds WHERE isin = ?", (isin,)).fetchone()
    if not bond:
        return None
    price_rows = conn.execute("""
        SELECT date, price, nkd 
        FROM prices p
        JOIN bonds b ON p.bond_id = b.id
        WHERE b.isin = ?
        ORDER BY date
    """, (isin,)).fetchall()
    if not price_rows:
        return None
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Дата', 'Цена закрытия (% номинала)', 'НКД (руб.)'])
    for row in price_rows:
        writer.writerow([row['date'], row['price'], row['nkd']])
    return output.getvalue()
import random

def generate_test_prices(conn, isin, days=252):
    """Создаёт случайную историю цен для облигации, если её нет."""
    bond_id = get_bond_id_by_isin(conn, isin)
    if not bond_id:
        return
    existing = conn.execute("SELECT COUNT(*) FROM prices WHERE bond_id=?", (bond_id,)).fetchone()[0]
    if existing > 0:
        return  # уже есть данные
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    current_price = 100.0
    d = start_date
    while d <= end_date:
        if d.weekday() < 5:  # только будни
            change = random.uniform(-0.2, 0.2)
            current_price = max(80, min(120, current_price + change))
            nkd = random.uniform(0, 10)
            add_price(conn, bond_id, d.strftime("%Y-%m-%d"), round(current_price, 2), round(nkd, 2))
        d += timedelta(days=1)
    conn.commit()

def load_prices_from_csv(conn, csv_file):
    """Загружает цены из CSV с колонками: isin, date, price, nkd."""
    import csv, io
    content = csv_file.getvalue().decode('utf-8')
    reader = csv.DictReader(io.StringIO(content))
    count = 0
    for row in reader:
        isin = row.get('isin') or row.get('ISIN')
        if not isin:
            continue
        bond_id = get_bond_id_by_isin(conn, isin)
        if not bond_id:
            continue
        date_str = row.get('date') or row.get('DATE')
        price = row.get('price') or row.get('PRICE')
        nkd = row.get('nkd') or row.get('NKD') or row.get('accruedint')
        if date_str and price:
            add_price(conn, bond_id, date_str, float(price), float(nkd) if nkd else 0.0)
            count += 1
    conn.commit()
    return count
# Список тестовых ISIN – гарантированно торгуемые на 01.06.2025
DEFAULT_ISINS = [
    "SU26238RMFS4",   # ОФЗ-ПД 26238
    "SU26240RMFS7",   # ОФЗ-ПД 26240
    "RU000A103R61",   # Газпром нефть 003P-01R
    "RU000A1006X0",   # РЖД 001P-01R
]