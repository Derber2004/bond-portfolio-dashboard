import sqlite3
import os

DATABASE_PATH = os.path.join("data", "portfolio.db")

def get_connection() -> sqlite3.Connection:
    """Возвращает соединение с БД (создаёт файл, если его нет)."""
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row  # чтобы можно было обращаться по именам колонок
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    """Инициализирует базу данных, создавая таблицы, если их нет."""
    conn = get_connection()
    cursor = conn.cursor()

    # Таблица облигаций
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bonds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            isin TEXT UNIQUE NOT NULL,
            ticker TEXT,
            name TEXT,
            nominal REAL,
            coupon_rate REAL,
            coupon_frequency INTEGER,  -- раз в год
            maturity_date TEXT,        -- YYYY-MM-DD
            currency TEXT,
            type TEXT,                 -- ОФЗ, корпоративная, замещающая
            credit_rating TEXT
        );
    """)

    # Таблица исторических цен
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bond_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            price REAL,               -- % от номинала
            nkd REAL,                 -- накопленный купонный доход (в валюте номинала или в %?)
            FOREIGN KEY (bond_id) REFERENCES bonds(id) ON DELETE CASCADE,
            UNIQUE(bond_id, date)
        );
    """)

    # Таблица сделок
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bond_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('BUY', 'SELL')),
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,       -- цена покупки/продажи в % от номинала (без НКД)
            nkd REAL,                  -- уплаченный/полученный НКД на дату сделки
            commission REAL DEFAULT 0.0,
            FOREIGN KEY (bond_id) REFERENCES bonds(id)
        );
    """)

    conn.commit()
    conn.close()