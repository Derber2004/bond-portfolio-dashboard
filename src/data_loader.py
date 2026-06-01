import requests
import time
from datetime import datetime, timedelta
import logging

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

def get_bond_info_from_moex(isin):
    """
    Получает информацию об облигации по ISIN.
    Возвращает словарь с ключами: isin, ticker, name, nominal, coupon_rate,
    coupon_frequency, maturity_date, currency, bond_type.
    """
    url = f"{MOEX_BASE}/securities/{isin}.json"
    data = fetch_json(url)
    if not data:
        return None

    sec_data = data.get("description", {}).get("data", [])
    if not sec_data:
        return None
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
    coupon_period = int(get_val("COUPONPERIOD")) if get_val("COUPONPERIOD") else None
    maturity_date_str = get_val("MATDATE")
    currency = get_val("FACEUNIT") or get_val("CURRENCYID")
    group = get_val("GROUPNAME")

    # Безопасное определение типа облигации
    if group and isinstance(group, str):
        if "федерального займа" in group.lower():
            bond_type = "ОФЗ"
        elif "корпоративные" in group.lower():
            bond_type = "корпоративная"
        else:
            bond_type = "прочая"
    else:
        bond_type = "прочая"

    if coupon_period:
        freq = round(365 / coupon_period)
    else:
        freq = 2

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
        "history.columns": "TRADEDATE,CLOSE,ACCRUEDINT",
        "limit": 500
    }
    data = fetch_json(url, params)
    if not data:
        return []

    # Обработка ответа, который может быть списком
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "history" in item:
                data = item
                break
        else:
            logger.warning(f"Не удалось найти блок 'history' для {isin}")
            return []

    history_block = data.get("history")
    if not history_block:
        return []

    history_data = history_block.get("data", [])
    cols = history_block["columns"]
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
                "price": float(price),
                "nkd": float(nkd) if nkd is not None else 0.0
            })
    return result

def load_bonds_and_prices(conn, isin_list):
    """
    Для каждого ISIN загружает параметры облигации и историю цен с MOEX.
    Если в базе уже есть цены, докачивает только пропущенные дни.
    """
    end_date = datetime.now().strftime("%Y-%m-%d")
    default_start = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

    for isin in isin_list:
        logger.info(f"Обработка {isin}")

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
            logger.info(f"Данные по {isin} актуальны (последняя дата {last_date}). Пропускаем.")
            continue

        if last_date:
            start_date = (datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            if start_date > end_date:
                logger.info(f"Данные по {isin} актуальны. Пропускаем.")
                continue
        else:
            start_date = default_start

        logger.info(f"Загружаем цены для {isin} с {start_date} по {end_date}")
        prices = fetch_price_history(isin, start_date, end_date)
        logger.info(f"Загружено {len(prices)} записей цен для {isin}")
        for p in prices:
            add_price(conn, bond_id, p["date"], p["price"], p["nkd"])
        time.sleep(0.2)


# Список ISIN для теста
DEFAULT_ISINS = [
    "SU26226RMFS5",   # ОФЗ-ПД 26226
    "SU26238RMFS4",   # ОФЗ-ПД 26238
    "RU000A103R61",   # Газпром нефть 003P-01R (корп)
    "RU000A105A92",   # Замещающая облигация Газпрома (пример)
]

def get_all_bonds_securities():
    base_url = f"{MOEX_BASE}/engines/stock/markets/bonds/securities.json"
    all_rows = []
    columns = None
    start = 0
    limit = 100

    while True:
        params = {
            "start": start,
            "limit": limit,
            "iss.json": "extended",
            "iss.meta": "off"
        }
        data = fetch_json(base_url, params)
        if not data:
            break

        # Отладка
        logger.info(f"Ответ: тип {type(data)}")
        if isinstance(data, dict):
            logger.info(f"Ключи: {list(data.keys())}")
        elif isinstance(data, list):
            logger.info(f"Список из {len(data)} элементов")
            if data:
                logger.info(f"Первый элемент типа {type(data[0])}: {data[0] if not isinstance(data[0], dict) else list(data[0].keys())}")

        # Нормализация
        if isinstance(data, list):
            found = False
            for item in data:
                if isinstance(item, dict) and "securities" in item:
                    data = item
                    found = True
                    break
            if not found:
                logger.warning("Не найден блок securities в списке")
                break

        if not isinstance(data, dict):
            logger.warning(f"data не словарь: {type(data)}")
            break

        securities_block = data.get("securities")
        if not securities_block:
            logger.info("Нет блока securities")
            break

        if isinstance(securities_block, dict):
            rows = securities_block.get("data", [])
            if columns is None:
                columns = securities_block.get("columns", [])
        elif isinstance(securities_block, list):
            rows = []
            for block in securities_block:
                if isinstance(block, dict):
                    rows.extend(block.get("data", []))
                    if columns is None:
                        columns = block.get("columns", [])
        else:
            logger.warning(f"Неизвестный тип securities_block: {type(securities_block)}")
            break

        if not rows:
            break

        all_rows.extend(rows)
        if len(rows) < limit:
            break
        start += limit
        time.sleep(0.1)

    if not all_rows or not columns:
        logger.warning("Не удалось получить данные облигаций через новый эндпоинт")
        return []

    # --- Обработка строк (скопируйте из предыдущей версии) ---
    # ... вставьте сюда весь код обработки, начиная с isin_idx = columns.index("ISIN") ...

def load_all_bonds_to_db(conn):
    """
    Загружает все облигации (только справочник) из MOEX в таблицу bonds.
    Возвращает количество добавленных записей.
    """
    bonds = get_all_bonds_securities()
    if not bonds:
        return 0
    count = 0
    for bond in bonds:
        add_bond(conn, **bond)
        count += 1
    conn.commit()
    return count
import csv
import io

def load_bonds_from_csv(conn, csv_file):
    """
    Загружает облигации из CSV-файла. Ожидается колонка 'isin' (или первая колонка).
    Возвращает (added_count, skipped_count).
    """
    content = csv_file.getvalue().decode('utf-8')
    reader = csv.DictReader(io.StringIO(content))
    isin_list = []
    # Ищем колонку isin (регистронезависимо)
    for row in reader:
        isin = None
        for key in row:
            if key.lower() == 'isin':
                isin = row[key].strip()
                break
        if not isin and len(row) > 0:
            # Если нет isin, берём первую колонку
            isin = list(row.values())[0].strip()
        if isin:
            isin_list.append(isin)

    if not isin_list:
        return 0, 0

    added = 0
    skipped = 0
    for isin in isin_list:
        info = get_bond_info_from_moex(isin)
        if info:
            add_bond(conn, **info)
            added += 1
        else:
            logger.warning(f"Не удалось получить данные для {isin}")
            skipped += 1
    conn.commit()
    return added, skipped
def prices_to_csv(conn, isin):
    """
    Получает все цены для облигации по ISIN и возвращает строку в формате CSV.
    Колонки: Дата, Цена закрытия, НКД.
    """
    import csv
    import io

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