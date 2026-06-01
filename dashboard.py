import streamlit as st
import pandas as pd
from datetime import date

from src.database import (
    init_db, get_connection,
    add_bond, add_price, get_bond_id_by_isin,
    get_portfolio_positions
)
from src.data_loader import load_bonds_and_prices, DEFAULT_ISINS

st.set_page_config(
    page_title="Мой портфель облигаций",
    page_icon="📈",
    layout="wide"
)

# Инициализация БД
init_db()

# Подключение к БД (кэшируем, но с возможностью обновления)
@st.cache_resource
def get_db_connection():
    return get_connection()

conn = get_db_connection()

st.title("📈 Мой портфель облигаций")
st.markdown("Дашборд для анализа доходности, рисков и структуры портфеля.")

# ===================== САЙДБАР =====================
with st.sidebar:
    st.header("⚙️ Управление данными")

    # Загрузка тестовых облигаций
    if st.button("🔄 Загрузить тестовые облигации с MOEX"):
        with st.spinner("Загружаю данные... Это может занять ~30 секунд"):
            load_bonds_and_prices(conn, DEFAULT_ISINS)
        st.success("Данные загружены!")
        st.rerun()

    st.divider()

    # Статистика базы
    bond_count = conn.execute("SELECT COUNT(*) FROM bonds").fetchone()[0]
    price_count = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    st.metric("Облигаций в базе", bond_count)
    st.metric("Записей цен", price_count)

    st.divider()

    # Форма добавления сделки
    st.subheader("➕ Новая сделка")
    with st.form("add_transaction"):
        # Получить список ISIN из базы
        isin_list = [row["isin"] for row in conn.execute("SELECT isin FROM bonds").fetchall()]
        if isin_list:
            selected_isin = st.selectbox("ISIN облигации", isin_list)
            tx_type = st.radio("Тип", ["BUY", "SELL"], horizontal=True)
            tx_date = st.date_input("Дата сделки", value=date.today())
            qty = st.number_input("Количество (бумаг)", min_value=1, step=1)
            tx_price = st.number_input("Цена (% от номинала)", min_value=0.0, value=100.0, step=0.01)
            tx_nkd = st.number_input("НКД на дату сделки (₽)", min_value=0.0, value=0.0, step=0.01)
            tx_commission = st.number_input("Комиссия (₽)", min_value=0.0, value=0.0, step=0.01)

            submitted = st.form_submit_button("Добавить сделку")
            if submitted:
                bond_id = get_bond_id_by_isin(conn, selected_isin)
                if bond_id:
                    conn.execute("""
                        INSERT INTO transactions (bond_id, date, type, quantity, price, nkd, commission)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (bond_id, tx_date.strftime("%Y-%m-%d"), tx_type, qty, tx_price, tx_nkd, tx_commission))
                    conn.commit()
                    st.success(f"Сделка {tx_type} {qty} шт. {selected_isin} добавлена!")
                    st.rerun()
                else:
                    st.error("Облигация не найдена в базе.")
        else:
            st.warning("Сначала загрузите облигации через кнопку выше.")

# ===================== ВКЛАДКИ =====================
tab1, tab2, tab3, tab4 = st.tabs(["📋 Обзор", "🗂 Структура", "💰 Купоны", "⚠️ Риски"])

# --- Вкладка "Обзор" ---
with tab1:
    st.header("Текущий портфель")
    positions = get_portfolio_positions(conn)

    if not positions:
        st.info("Портфель пуст. Добавьте сделки через боковую панель.")
    else:
        # Превращаем в DataFrame для удобства
        df = pd.DataFrame(positions, columns=["ISIN", "Название", "Номинал", "Валюта", "Тип",
                                              "Количество", "Средняя цена покупки, %", "Затраты, ₽"])

        # Добавим текущую цену (пока заглушка, потом будем брать последнюю из prices)
        # Для наглядности используем последнюю доступную цену из таблицы prices
        def get_last_price(isin):
            row = conn.execute("""
                SELECT p.price, p.nkd
                FROM prices p
                JOIN bonds b ON p.bond_id = b.id
                WHERE b.isin = ?
                ORDER BY p.date DESC
                LIMIT 1
            """, (isin,)).fetchone()
            return row if row else (None, None)

        last_prices = [get_last_price(isin) for isin in df["ISIN"]]
        df["Текущая цена, %"] = [lp[0] if lp[0] else None for lp in last_prices]
        df["Текущий НКД, ₽"] = [lp[1] if lp[1] else None for lp in last_prices]

        # Рассчитаем текущую стоимость позиции (количество * (цена/100 * номинал) + НКД)
        def calc_current_value(row, price_col, nkd_col):
            if row[price_col] and row["Номинал"]:
                return row["Количество"] * (row[price_col] / 100.0 * row["Номинал"]) + row[nkd_col]
            return None

        df["Текущая стоимость, ₽"] = df.apply(
            lambda r: calc_current_value(r, "Текущая цена, %", "Текущий НКД, ₽"), axis=1
        )

        # Общие суммы
        total_cost = df["Затраты, ₽"].sum()
        total_value = df["Текущая стоимость, ₽"].sum()

        col1, col2, col3 = st.columns(3)
        col1.metric("Общая стоимость портфеля", f"{total_value:,.2f} ₽")
        col2.metric("Затраты на покупку", f"{total_cost:,.2f} ₽")
        col3.metric("Облигаций в портфеле", len(df))

        st.dataframe(df, use_container_width=True)

# --- Вкладка "Структура" ---
with tab2:
    st.header("Структура портфеля")
    if positions:
        df = pd.DataFrame(positions)
        # Круговые диаграммы по типам и валютам
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("По типу облигаций")
            type_dist = df.groupby("type")["total_cost"].sum()
            st.bar_chart(type_dist)
        with col2:
            st.subheader("По валютам")
            curr_dist = df.groupby("currency")["total_cost"].sum()
            st.bar_chart(curr_dist)
    else:
        st.info("Нет данных для отображения.")

# --- Вкладка "Купоны" ---
with tab3:
    st.header("Купонные выплаты")
    st.info("Здесь будет календарь и график денежного потока (на будущем этапе).")

# --- Вкладка "Риски" ---
with tab4:
    st.header("Риски")
    st.info("Дюрация, VaR, спреды — после добавления расчётов YTM.")