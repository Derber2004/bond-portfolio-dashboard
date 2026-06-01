import streamlit as st
import pandas as pd
from datetime import date

from src.database import (
    init_db, get_connection,
    add_bond, add_price, get_bond_id_by_isin,
    get_portfolio_positions
)
from src.data_loader import load_bonds_and_prices, DEFAULT_ISINS
from src.metrics import calc_ytm, modified_duration

st.set_page_config(
    page_title="Мой портфель облигаций",
    page_icon="📈",
    layout="wide"
)

# Инициализация БД
init_db()

# Подключение к БД (потокобезопасное, кэшируем один раз за сессию)
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
        # Загрузка тестовых облигаций
    if st.button("🔄 Загрузить тестовые облигации с MOEX"):
        with st.spinner("Загружаю данные... Это может занять ~30 секунд"):
            load_bonds_and_prices(conn, DEFAULT_ISINS)
        st.success("Данные загружены!")
        st.rerun()

    st.divider()

    # Загрузка произвольных ISIN
    st.subheader("📥 Загрузить свои облигации")
    st.caption("Введите ISIN (по одному на строке).\nПример: SU26238RMFS4")
    user_isins = st.text_area("Список ISIN", height=100, placeholder="SU26226RMFS5\nRU000A103R61")

    if st.button("Загрузить введённые ISIN"):
        if user_isins.strip():
            isin_list = [i.strip() for i in user_isins.splitlines() if i.strip()]
            with st.spinner(f"Загружаю {len(isin_list)} облигаций..."):
                load_bonds_and_prices(conn, isin_list)
            st.success(f"Загружено {len(isin_list)} облигаций!")
            st.rerun()
        else:
            st.warning("Введите хотя бы один ISIN.")

    # Статистика базы
    bond_count = conn.execute("SELECT COUNT(*) FROM bonds").fetchone()[0]
    price_count = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    st.metric("Облигаций в базе", bond_count)
    st.metric("Записей цен", price_count)

    st.divider()

    # Форма добавления сделки
    st.subheader("➕ Новая сделка")
    with st.form("add_transaction"):
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
        df = pd.DataFrame(positions, columns=["ISIN", "Название", "Номинал", "Валюта", "Тип",
                                              "Количество", "Средняя цена покупки, %", "Затраты, ₽"])

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

        # Расчёт YTM и дюрации
        bonds_info = {}
        for row in conn.execute("SELECT isin, nominal, coupon_rate, coupon_frequency, maturity_date FROM bonds").fetchall():
            bonds_info[row['isin']] = row

        ytm_values = []
        md_values = []
        for idx, isin in enumerate(df["ISIN"]):
            price = df.loc[idx, "Текущая цена, %"]
            info = bonds_info.get(isin)
            if price and info:
                ytm = calc_ytm(price, info['nominal'], info['coupon_rate'], info['coupon_frequency'], info['maturity_date'])
                md = modified_duration(price, info['nominal'], info['coupon_rate'], info['coupon_frequency'], info['maturity_date'], ytm)
            else:
                ytm = None
                md = None
            ytm_values.append(ytm)
            md_values.append(md)

        df["YTM, %"] = [f"{y:.2f}" if y is not None else "N/A" for y in ytm_values]
        df["Мод. дюрация, %"] = [f"{d:.2f}" if d is not None else "N/A" for d in md_values]

        # Текущая стоимость позиции
        def calc_current_value(row, price_col, nkd_col):
            if row[price_col] and row["Номинал"]:
                return row["Количество"] * (row[price_col] / 100.0 * row["Номинал"]) + row[nkd_col]
            return None

        df["Текущая стоимость, ₽"] = df.apply(
            lambda r: calc_current_value(r, "Текущая цена, %", "Текущий НКД, ₽"), axis=1
        )

        total_cost = df["Затраты, ₽"].sum()
        total_value = df["Текущая стоимость, ₽"].sum()

        col1, col2, col3 = st.columns(3)
        col1.metric("Общая стоимость портфеля", f"{total_value:,.2f} ₽" if total_value else "N/A")
        col2.metric("Затраты на покупку", f"{total_cost:,.2f} ₽")
        col3.metric("Облигаций в портфеле", len(df))

        st.dataframe(df, use_container_width=True)

        if total_value and any(ytm_values):
            weighted_ytm = 0.0
            weighted_md = 0.0
            for i, ytm in enumerate(ytm_values):
                if ytm is not None and df.loc[i, "Текущая стоимость, ₽"]:
                    weight = df.loc[i, "Текущая стоимость, ₽"] / total_value
                    weighted_ytm += ytm * weight
                    if md_values[i] is not None:
                        weighted_md += md_values[i] * weight
            st.metric("Средневзвешенная YTM портфеля", f"{weighted_ytm:.2f}%")
            st.metric("Средневзвешенная модифицированная дюрация портфеля", f"{weighted_md:.2f}%")

# --- Вкладка "Структура" ---
with tab2:
    st.header("Структура портфеля")
    positions = get_portfolio_positions(conn)
    if positions:
        df = pd.DataFrame(positions, columns=["ISIN", "Название", "Номинал", "Валюта", "Тип",
                                              "Количество", "Средняя цена покупки, %", "Затраты, ₽"])
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("По типу облигаций")
            type_dist = df.groupby("Тип")["Затраты, ₽"].sum()
            st.bar_chart(type_dist)
        with col2:
            st.subheader("По валютам")
            curr_dist = df.groupby("Валюта")["Затраты, ₽"].sum()
            st.bar_chart(curr_dist)
    else:
        st.info("Нет данных для отображения.")

# --- Вкладка "Купоны" ---
with tab3:
    st.header("Купонные выплаты")
    st.info("Здесь будет календарь и график денежного потока (на будущем этапе).")

# --- Вкладка "Риски" ---
with tab4:
    st.header("⚠️ Риски")
    from src.metrics import calculate_historical_var, get_portfolio_metrics

    metrics = get_portfolio_metrics(conn)
    if metrics:
        col1, col2, col3 = st.columns(3)
        col1.metric("Средневзвешенная YTM", f"{metrics['weighted_ytm']:.2f}%" if metrics['weighted_ytm'] else "N/A")
        col2.metric("Средневзвешенная дюрация", f"{metrics['weighted_duration']:.2f}%" if metrics['weighted_duration'] else "N/A")
        col3.metric("Средневзвешенная выпуклость", f"{metrics['weighted_convexity']:.4f}" if metrics['weighted_convexity'] else "N/A")

        st.divider()

        var_95 = calculate_historical_var(conn, confidence=0.95, horizon_days=10)
        var_99 = calculate_historical_var(conn, confidence=0.99, horizon_days=10)
        if var_95 is not None:
            col1, col2 = st.columns(2)
            col1.metric("VaR 95% (10 дней)", f"{var_95:,.2f} ₽")
            col2.metric("VaR 99% (10 дней)", f"{var_99:,.2f} ₽" if var_99 else "N/A")
        else:
            st.info("Недостаточно данных для расчёта VaR (нужна история цен).")
    else:
        st.info("Портфель пуст или отсутствуют цены.")