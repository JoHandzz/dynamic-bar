import streamlit as st
import pandas as pd
import plotly.express as px
import time
from sqlalchemy import text

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
st.set_page_config(page_title="Beer Exchange", layout="wide", initial_sidebar_state="expanded")

INITIAL_PRICES = {
    "Øl": 10.00,
    "Shot": 8.00,
    "5 shots": 20.00,
    "Rum & Coke": 20.00,
    "Gin & Tonic": 20.00,
    "Tequila Shot": 20.00,
    "Whiskey Sour": 20.00
}

DRINKS = list(INITIAL_PRICES.keys())
FLOOR_PRICE = 2.00
CEIL_PRICE = 30.00
BUMP_BUY = 0.2      
DECAY_PASSIVE = BUMP_BUY / len(DRINKS)  
CRASH_DISCOUNT = 0.7

# ==========================================
# STATE INITIALIZATION (The Shopping Cart)
# ==========================================
for drink in DRINKS:
    if f"cart_{drink}" not in st.session_state:
        st.session_state[f"cart_{drink}"] = 0

# ==========================================
# DATABASE CONNECTION (SUPABASE POSTGRESQL)
# ==========================================
conn = st.connection("supabase", type="sql")

def init_db():
    with conn.session as s:
        # Note: PostgreSQL uses SERIAL instead of AUTOINCREMENT, and TIMESTAMP
        s.execute(text("""
            CREATE TABLE IF NOT EXISTS price_history (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                drink_name VARCHAR(50),
                price NUMERIC(10,2)
            );
        """))
        s.execute(text("""
            CREATE TABLE IF NOT EXISTS sales_log (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                drink_name VARCHAR(50),
                sale_price NUMERIC(10,2)
            );
        """))
        s.commit()
        
        check = s.execute(text("SELECT COUNT(*) FROM price_history")).scalar()
        if check == 0:
            for drink, price in INITIAL_PRICES.items():
                s.execute(text(f"INSERT INTO price_history (drink_name, price) VALUES ('{drink}', {price})"))
            s.commit()

init_db()

# ==========================================
# DATA FETCHING & LOGIC FUNCTIONS
# ==========================================
def get_price_history():
    df = conn.query("SELECT timestamp, drink_name, price FROM price_history ORDER BY timestamp ASC", ttl=0)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df

def get_current_prices():
    df = get_price_history()
    if df.empty:
        return {}
    
    current_prices = {}
    for drink in DRINKS:
        drink_df = df[df['drink_name'] == drink]
        if not drink_df.empty:
            prices = drink_df['price'].tolist()
            current = float(prices[-1])
            initial = INITIAL_PRICES[drink]
            current_prices[drink] = {"current": current, "delta": current - initial}
        else:
            current_prices[drink] = {"current": INITIAL_PRICES[drink], "delta": 0.0}
            
    return current_prices

def process_cart_sale(prices_dict):
    """Processes all items in the cart, updating prices iteratively."""
    temp_prices = {d: prices_dict[d]["current"] for d in DRINKS}
    
    with conn.session as s:
        for drink in DRINKS:
            qty = st.session_state[f"cart_{drink}"]
            
            for _ in range(qty):
                s.execute(text(f"INSERT INTO sales_log (drink_name, sale_price) VALUES ('{drink}', {temp_prices[drink]})"))
                
                for d in DRINKS:
                    if d == drink:
                        temp_prices[d] = min(temp_prices[d] + BUMP_BUY, CEIL_PRICE)
                    else:
                        temp_prices[d] = max(temp_prices[d] - DECAY_PASSIVE, FLOOR_PRICE)
        
        for d in DRINKS:
            s.execute(text(f"INSERT INTO price_history (drink_name, price) VALUES ('{d}', {temp_prices[d]})"))
            
        s.commit()

# ==========================================
# CALLBACK FUNCTIONS (Run before UI redraws)
# ==========================================
def checkout_cart():
    latest_prices = get_current_prices()
    total_items = sum(st.session_state[f"cart_{d}"] for d in DRINKS)
    
    if total_items > 0:
        process_cart_sale(latest_prices)
        for d in DRINKS:
            st.session_state[f"cart_{d}"] = 0

def trigger_market_crash():
    with conn.session as s:
        for drink in DRINKS:
            crash_price = max(INITIAL_PRICES[drink] * (1 - CRASH_DISCOUNT), FLOOR_PRICE)
            s.execute(text(f"INSERT INTO price_history (drink_name, price) VALUES ('{drink}', {crash_price})"))
        s.commit()

def trigger_market_reset():
    with conn.session as s:
        for drink in DRINKS:
            reset_price = INITIAL_PRICES[drink]
            s.execute(text(f"INSERT INTO price_history (drink_name, price) VALUES ('{drink}', {reset_price})"))
        s.commit()

def get_sales_log():
    return conn.query("SELECT * FROM sales_log ORDER BY timestamp DESC", ttl=0)

# ==========================================
# APP ROUTING (Single App Logic)
# ==========================================
# Checks if the URL is ?admin=bartender2026 (or whatever is in secrets.toml)
query_params = st.query_params
is_admin = query_params.get("admin") == st.secrets.get("ADMIN_PASS", "")

current_prices = get_current_prices()

# ==========================================
# USER INTERFACE: PUBLIC DASHBOARD
# ==========================================
st.title("STAMBAR")
st.markdown("### Follow the live market")

cols_row1 = st.columns(4)
cols_row2 = st.columns(4)
all_cols = cols_row1 + cols_row2

for i, drink in enumerate(DRINKS):
    if drink in current_prices:
        data = current_prices[drink]
        all_cols[i].metric(
            label=drink, 
            value=f"{data['current']:.2f} kr.", 
            delta=f"{data['delta']:.2f} kr." 
        )

st.markdown("---")
history_df = get_price_history()
if not history_df.empty:
    fig = px.line(
        history_df, 
        x='timestamp', 
        y='price', 
        color='drink_name',
        title="Market Trends",
        template="plotly_dark"
    )
    fig.update_layout(yaxis_title="Price (kr.)", xaxis_title="Time", hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)

# ==========================================
# USER INTERFACE: BARTENDER PANEL (PROTECTED)
# ==========================================
if is_admin:
    st.sidebar.title("Kasseapparat")
    st.sidebar.markdown("Tilføj varer til kurven:")

    total_items_in_cart = 0
    total_cart_cost = 0.0

    for drink in DRINKS:
        qty = st.sidebar.number_input(
            f"{drink} ({current_prices[drink]['current']:.2f} kr.)",
            min_value=0,
            step=1,
            key=f"cart_{drink}" 
        )
        total_items_in_cart += qty
        total_cart_cost += qty * current_prices[drink]['current']

    st.sidebar.markdown("---")

    if total_items_in_cart > 0:
        st.sidebar.success(f"Kurv Total: {total_items_in_cart} varer ({total_cart_cost:.2f} kr.)")

    st.sidebar.button("KØB (Lock in Sale)", type="primary", use_container_width=True, on_click=checkout_cart)

    st.sidebar.markdown("---")
    st.sidebar.error("Event controls")

    st.sidebar.button("Trigger Market Crash", use_container_width=True, on_click=trigger_market_crash)
    st.sidebar.button("Reset Market (original prices)", use_container_width=True, on_click=trigger_market_reset)

    st.sidebar.markdown("---")
    st.sidebar.success("Data Export")
    sales_df = get_sales_log()
    csv = sales_df.to_csv(index=False).encode('utf-8')
    st.sidebar.download_button(
        label="Download Sales CSV",
        data=csv,
        file_name='party_sales_log.csv',
        mime='text/csv',
        use_container_width=True
    )

# ==========================================
# REFRESH LOGIC (For Public View Only)
# ==========================================
# If it's a student viewing the public page, auto-refresh every 3 seconds
if not is_admin:
    time.sleep(3)
    st.rerun()