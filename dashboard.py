import streamlit as st
from src.database import init_db, get_connection

# Настройка страницы
st.set_page_config(
    page_title="Мой портфель облигаций",
    page_icon="📈",
    layout="wide"
)

# Инициализируем базу данных при первом запуске
init_db()

st.title("📈 Мой портфель облигаций")
st.markdown("Дашборд для анализа доходности, рисков и структуры портфеля.")

# Подключение к базе (кэшируем соединение, чтобы не пересоздавать на каждое взаимодействие)
@st.cache_resource
def get_cached_connection():
    return get_connection()

conn = get_cached_connection()

# Сайдбар с общей информацией
with st.sidebar:
    st.header("📊 О портфеле")
    # Посчитаем количество уникальных облигаций в базе
    bond_count = conn.execute("SELECT COUNT(*) FROM bonds").fetchone()[0]
    st.metric("Загружено облигаций", bond_count)

    # Количество сделок
    tx_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    st.metric("Всего сделок", tx_count)

# Вкладки
tab1, tab2, tab3, tab4 = st.tabs(["📋 Обзор", "🗂 Структура", "💰 Купоны", "⚠️ Риски"])

with tab1:
    st.header("Обзор портфеля")
    if bond_count == 0:
        st.info("👆 В базе пока нет облигаций. Перейди во вкладку 'Структура' и добавь бумаги через журнал сделок (скоро).")
    else:
        st.success("Здесь будет сводная информация: стоимость, средняя доходность, дюрация.")
        # Пока просто выведем список облигаций из базы
        bonds = conn.execute("SELECT isin, name, nominal, currency FROM bonds").fetchall()
        st.dataframe(bonds)

with tab2:
    st.header("Структура портфеля")
    st.info("Здесь будет распределение по секторам, валютам, рейтингам.")

with tab3:
    st.header("Купонные выплаты")
    st.info("Здесь будет календарь и график денежного потока.")

with tab4:
    st.header("Риски")
    st.info("Здесь будет дюрация, VaR, спреды.")