import streamlit as st
import pandas as pd
from datetime import date
from src.database import get_last_price_date
from src.data_loader import prices_to_csv

from src.database import (
    init_db, get_connection,
    add_bond, add_price, get_bond_id_by_isin,
    get_portfolio_positions, DATABASE_PATH
)
from src.data_loader import load_bonds_and_prices, DEFAULT_ISINS
from src.metrics import calc_ytm, modified_duration, convexity, calculate_historical_var, get_portfolio_metrics

st.set_page_config(
    page_title="Мой портфель облигаций",
    page_icon="📈",
    layout="wide"
)

init_db()

@st.cache_resource
def get_db_connection():
    return get_connection()

conn = get_db_connection()

# ---------- Кешируемые обёртки ----------
@st.cache_data(ttl=600)
def cached_coupon_flow():
    """Кешируемая версия get_portfolio_coupon_flow."""
    from src.coupons import get_portfolio_coupon_flow
    return get_portfolio_coupon_flow(get_db_connection())

@st.cache_data(ttl=600)
def cached_portfolio_metrics():
    """Кешируемая версия get_portfolio_metrics."""
    return get_portfolio_metrics(get_db_connection())

@st.cache_data(ttl=600)
def cached_var(confidence, horizon):
    """Кешируемая версия calculate_historical_var."""
    return calculate_historical_var(get_db_connection(), confidence, horizon)

# ---------- Интерфейс ----------
st.title("📈 Мой портфель облигаций")
st.markdown("Дашборд для анализа доходности, рисков и структуры портфеля.")

with st.sidebar:
    st.header("⚙️ Управление данными")

    if st.button("🔄 Загрузить тестовые облигации с MOEX"):
        with st.spinner("Загружаю..."):
            load_bonds_and_prices(conn, DEFAULT_ISINS)
        st.success("Данные загружены!")
        st.cache_data.clear()
        st.rerun()
    # Загрузка всех облигаций (только справочник)
    if st.button("📚 Загрузить все облигации с MOEX (без цен)"):
        with st.spinner("Загружаю список всех облигаций... Это может занять 1-2 минуты"):
            from src.data_loader import load_all_bonds_to_db
            added = load_all_bonds_to_db(conn)
        st.success(f"Добавлено {added} облигаций в справочник!")
        st.cache_data.clear()
        st.rerun()
    st.divider()

    st.subheader("📥 Загрузить свои облигации")
    st.caption("Введите ISIN (по одному на строке).")
    user_isins = st.text_area("Список ISIN", height=100, placeholder="SU26226RMFS5\nRU000A103R61")

    if st.button("Загрузить введённые ISIN"):
        if user_isins.strip():
            isin_list = list(set(i.strip() for i in user_isins.splitlines() if i.strip()))
            with st.spinner(f"Загружаю {len(isin_list)} облигаций..."):
                load_bonds_and_prices(conn, isin_list)
            st.success(f"Загружено {len(isin_list)} облигаций!")
            st.cache_data.clear()
            st.rerun()
        else:
            st.warning("Введите хотя бы один ISIN.")

    st.divider()

    st.subheader("📁 Загрузить ISIN из CSV")
    uploaded_file = st.file_uploader("Выберите CSV-файл", type=["csv"])
    if uploaded_file is not None:
        if st.button("Загрузить из CSV"):
            from src.data_loader import load_bonds_from_csv
            added, skipped = load_bonds_from_csv(conn, uploaded_file)
            if added > 0:
                st.success(f"Загружено {added} облигаций, пропущено {skipped}.")
                st.cache_data.clear()
                st.rerun()
            else:
                st.warning("Не удалось загрузить ни одной облигации. Проверьте формат файла.")

    st.divider()

    bond_count = conn.execute("SELECT COUNT(*) FROM bonds").fetchone()[0]
    price_count = conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    st.metric("Облигаций в базе", bond_count)
    st.metric("Записей цен", price_count)

    st.divider()

    st.subheader("➕ Новая сделка")
    search_isin = st.text_input("Поиск облигации по ISIN или названию", key="isin_search")

    matching_bonds = []
    if search_isin:
        search_term = f"%{search_isin}%"
        matching_bonds = conn.execute("""
            SELECT isin, name, nominal, currency
            FROM bonds
            WHERE isin LIKE ? OR name LIKE ?
            ORDER BY isin
            LIMIT 50
        """, (search_term, search_term)).fetchall()

    selected_isin = None
    bond_info = None
    if matching_bonds:
        options = [f"{b['isin']} – {b['name']} ({b['currency']})" for b in matching_bonds]
        choice = st.radio("Найденные облигации:", options)
        selected_isin = choice.split(" – ")[0]
        bond_info = next((b for b in matching_bonds if b['isin'] == selected_isin), None)

    if selected_isin and bond_info:
        with st.form("add_transaction"):
            st.write(f"**{bond_info['name']}** (номинал: {bond_info['nominal']} {bond_info['currency']})")
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
                    st.cache_data.clear()
                    st.success(f"Сделка {tx_type} {qty} шт. {selected_isin} добавлена!")
                    st.rerun()
                else:
                    st.error("Облигация не найдена в базе.")
    elif search_isin and not matching_bonds:
        st.info("Ничего не найдено. Попробуйте другой ISIN или название.")
    else:
        st.info("Введите ISIN или часть названия для поиска.")

    with st.expander("📋 Все доступные облигации"):
        all_bonds = conn.execute("SELECT isin, name, nominal, currency, bond_type FROM bonds ORDER BY isin").fetchall()
        if all_bonds:
            st.dataframe(pd.DataFrame(all_bonds), use_container_width=True)
        else:
            st.info("Справочник пуст.")

    st.divider()
    st.subheader("🗑 Управление данными")

    delete_isin = st.selectbox(
        "Выберите ISIN для удаления",
        options=[""] + [row["isin"] for row in conn.execute("SELECT isin FROM bonds").fetchall()]
    )
    if delete_isin and st.button("❌ Удалить облигацию"):
        if st.session_state.get("confirm_delete") != delete_isin:
            st.warning(f"Подтвердите удаление {delete_isin}, нажав кнопку ещё раз.")
            st.session_state.confirm_delete = delete_isin
        else:
            from src.database import delete_bond
            if delete_bond(conn, delete_isin):
                st.cache_data.clear()
                st.success(f"Облигация {delete_isin} и связанные данные удалены.")
                st.session_state.confirm_delete = None
                st.rerun()
            else:
                st.error("Ошибка удаления.")

    st.divider()
    if st.button("🧹 Очистить все сделки (портфель)"):
        if st.session_state.get("confirm_clear_tx") != True:
            st.warning("Подтвердите очистку всех сделок, нажав кнопку ещё раз.")
            st.session_state.confirm_clear_tx = True
        else:
            from src.database import clear_transactions
            clear_transactions(conn)
            st.cache_data.clear()
            st.success("Все сделки удалены. Портфель пуст.")
            st.session_state.confirm_clear_tx = False
            st.rerun()

    if st.button("💣 Очистить ВСЕ данные (бонды, цены, сделки)"):
        if st.session_state.get("confirm_clear_all") != True:
            st.warning("Подтвердите полную очистку базы данных, нажав кнопку ещё раз.")
            st.session_state.confirm_clear_all = True
        else:
            from src.database import clear_all_data
            clear_all_data(conn)
            st.cache_data.clear()
            st.success("База данных полностью очищена.")
            st.session_state.confirm_clear_all = False
            st.rerun()
# ---------- Вкладки ----------
tab1, tab2, tab3, tab4 = st.tabs(["📋 Обзор", "🗂 Структура", "💰 Купоны", "⚠️ Риски"])

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
                ORDER BY p.date DESC LIMIT 1
            """, (isin,)).fetchone()
            return row if row else (None, None)

        last_prices = [get_last_price(isin) for isin in df["ISIN"]]
        df["Текущая цена, %"] = [lp[0] if lp[0] else None for lp in last_prices]
        df["Текущий НКД, ₽"] = [lp[1] if lp[1] else None for lp in last_prices]

        bonds_info = {}
        for row in conn.execute("SELECT isin, nominal, coupon_rate, coupon_frequency, maturity_date FROM bonds").fetchall():
            bonds_info[row['isin']] = row

        ytm_values = []
        md_values = []
        for idx, isin in enumerate(df["ISIN"]):
            price = df.loc[idx, "Текущая цена, %"]
            nkd = df.loc[idx, "Текущий НКД, ₽"]
            info = bonds_info.get(isin)
            if price is not None and nkd is not None and info:
                ytm = calc_ytm(price, info['nominal'], info['coupon_rate'], 
                               info['coupon_frequency'], info['maturity_date'], nkd=nkd)
                md = modified_duration(price, info['nominal'], info['coupon_rate'], 
                                      info['coupon_frequency'], info['maturity_date'], ytm=ytm, nkd=nkd)
            else:
                ytm = None
                md = None
            ytm_values.append(ytm)
            md_values.append(md)

        df["YTM, %"] = [f"{y:.2f}" if y is not None else "N/A" for y in ytm_values]
        df["Мод. дюрация, %"] = [f"{d:.2f}" if d is not None else "N/A" for d in md_values]

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

        # --- Экспорт CSV ---
        st.divider()
        st.subheader("📥 Экспорт цен")
        export_isin = st.selectbox(
            "Выберите облигацию для экспорта котировок",
            options=df["ISIN"].tolist()
        )
        if export_isin:
            csv_data = prices_to_csv(conn, export_isin)
            if csv_data:
                bond_name = conn.execute("SELECT name FROM bonds WHERE isin=?", (export_isin,)).fetchone()['name']
                st.download_button(
                    label=f"Скачать CSV для {export_isin}",
                    data=csv_data,
                    file_name=f"{export_isin}_prices.csv",
                    mime="text/csv"
                )
            else:
                st.info("Нет данных о ценах для выбранной облигации.")

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

with tab3:
    st.header("💰 Купонные выплаты")
    coupons_df = cached_coupon_flow()
    if coupons_df.empty:
        st.info("Портфель пуст или по всем облигациям уже прошло погашение.")
    else:
        coupons_df["Месяц"] = coupons_df["Дата"].apply(lambda d: d.strftime("%Y-%m"))
        monthly = coupons_df.groupby("Месяц")["Общая сумма"].sum().reset_index().sort_values("Месяц")

        st.subheader("📅 Денежный поток по месяцам")
        st.bar_chart(monthly.set_index("Месяц"))

        st.subheader("📋 Ближайшие выплаты (первые 20)")
        st.dataframe(coupons_df.head(20), use_container_width=True)

with tab4:
    st.header("⚠️ Риски")
    metrics = cached_portfolio_metrics()
    if metrics:
        col1, col2, col3 = st.columns(3)
        col1.metric("Средневзвешенная YTM", f"{metrics['weighted_ytm']:.2f}%" if metrics['weighted_ytm'] else "N/A")
        col2.metric("Средневзвешенная дюрация", f"{metrics['weighted_duration']:.2f}%" if metrics['weighted_duration'] else "N/A")
        col3.metric("Средневзвешенная выпуклость", f"{metrics['weighted_convexity']:.4f}" if metrics['weighted_convexity'] else "N/A")

        st.divider()

        var_95 = cached_var(0.95, 10)
        var_99 = cached_var(0.99, 10)
        if var_95 is not None:
            col1, col2 = st.columns(2)
            col1.metric("VaR 95% (10 дней)", f"{var_95:,.2f} ₽")
            col2.metric("VaR 99% (10 дней)", f"{var_99:,.2f} ₽" if var_99 else "N/A")
        else:
            st.info("Недостаточно данных для расчёта VaR.")
    else:
        st.info("Портфель пуст или отсутствуют цены.")