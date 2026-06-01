import sqlite3
import os

DATABASE_PATH = os.path.join("data", "portfolio.db")

def get_connection() -> sqlite3.Connection:
    """Возвращает соединение с БД (создаёт файл, если его нет). Разрешено многопоточное использование."""
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    """Инициализирует базу данных, создавая таблицы, если их нет."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bonds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            isin TEXT UNIQUE NOT NULL,
            ticker TEXT,
            name TEXT,
            nominal REAL,
            coupon_rate REAL,
            coupon_frequency INTEGER,
            maturity_date TEXT,
            currency TEXT,
            bond_type TEXT,
            credit_rating TEXT
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bond_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            price REAL,
            nkd REAL,
            FOREIGN KEY (bond_id) REFERENCES bonds(id) ON DELETE CASCADE,
            UNIQUE(bond_id, date)
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bond_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('BUY', 'SELL')),
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            nkd REAL,
            commission REAL DEFAULT 0.0,
            FOREIGN KEY (bond_id) REFERENCES bonds(id)
        );
    """)

    conn.commit()
    conn.close()

def add_bond(conn, isin, ticker, name, nominal, coupon_rate, coupon_frequency, maturity_date, currency, bond_type, credit_rating=None):
    """Добавляет новую облигацию или обновляет существующую по ISIN."""
    sql = """
        INSERT INTO bonds (isin, ticker, name, nominal, coupon_rate, coupon_frequency, maturity_date, currency, bond_type, credit_rating)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(isin) DO UPDATE SET
            ticker = excluded.ticker,
            name = excluded.name,
            nominal = excluded.nominal,
            coupon_rate = excluded.coupon_rate,
            coupon_frequency = excluded.coupon_frequency,
            maturity_date = excluded.maturity_date,
            currency = excluded.currency,
            bond_type = excluded.bond_type,
            credit_rating = excluded.credit_rating;
    """
    conn.execute(sql, (isin, ticker, name, nominal, coupon_rate, coupon_frequency, maturity_date, currency, bond_type, credit_rating))
    conn.commit()

def add_price(conn, bond_id, date_str, price, nkd):
    """Добавляет или обновляет запись о цене на конкретную дату."""
    sql = """
        INSERT INTO prices (bond_id, date, price, nkd)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(bond_id, date) DO UPDATE SET
            price = excluded.price,
            nkd = excluded.nkd;
    """
    conn.execute(sql, (bond_id, date_str, price, nkd))
    conn.commit()

def get_bond_id_by_isin(conn, isin):
    """Возвращает id облигации по ISIN."""
    row = conn.execute("SELECT id FROM bonds WHERE isin = ?", (isin,)).fetchone()
    return row['id'] if row else None

def get_portfolio_positions(conn):
    """
    Возвращает текущий портфель: ISIN, количество, средняя цена покупки, общая стоимость покупки.
    """
    query = """
        SELECT 
            b.isin,
            b.name,
            b.nominal,
            b.currency,
            b.bond_type as type,
            SUM(CASE WHEN t.type = 'BUY' THEN t.quantity ELSE -t.quantity END) as total_qty,
            CASE 
                WHEN SUM(CASE WHEN t.type = 'BUY' THEN t.quantity ELSE 0 END) > 0 
                THEN SUM(CASE WHEN t.type = 'BUY' THEN t.quantity * t.price ELSE 0 END) 
                     / SUM(CASE WHEN t.type = 'BUY' THEN t.quantity ELSE 0 END)
                ELSE 0
            END as avg_buy_price,
            SUM(CASE WHEN t.type = 'BUY' THEN t.quantity * (t.price / 100.0 * b.nominal + COALESCE(t.nkd, 0) + COALESCE(t.commission, 0)) ELSE 0 END) as total_cost
        FROM bonds b
        LEFT JOIN transactions t ON b.id = t.bond_id
        GROUP BY b.id
        HAVING total_qty > 0
    """
    return conn.execute(query).fetchall()

def get_last_price_date(conn, isin):
    """Возвращает последнюю дату, на которую есть цена для облигации по ISIN, или None."""
    row = conn.execute("""
        SELECT MAX(p.date) 
        FROM prices p
        JOIN bonds b ON p.bond_id = b.id
        WHERE b.isin = ?
    """, (isin,)).fetchone()
    return row[0] if row and row[0] else None