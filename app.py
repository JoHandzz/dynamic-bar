import streamlit as st
import pandas as pd
import plotly.express as px
import time
from sqlalchemy import text

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
st.set_page_config(page_title="Beer Exchange", layout="wide", initial_sidebar_state="expanded")

# Define initial prices individually
INITIAL_PRICES = {
    "Øl": 10,
    "Shot": 8,
    "Rum & Coke": 20,
    "Gin & Tonic": 20,
    "Tequila Shot": 20,
    "Whiskey Sour": 20
}

DRINKS = list(INITIAL_PRICES.keys())
FLOOR_PRICE = 2.00
CEIL_PRICE = 25
BUMP_BUY = 0.75      # Price increase when bought
DECAY_PASSIVE = 0.15 # Price decrease for all other drinks

# ==========================================
# DATABASE CONNECTION & INITIALIZATION
# ==========================================
conn = st.connection("supabase", type="sql")

def init_db():
    """Creates necessary tables if they don't exist and seeds initial prices."""
    with conn.session as s:
        s.execute(text("""
            CREATE TABLE IF NOT EXISTS price_history (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                drink_name VARCHAR(50),
                price NUMERIC(5,2)
            );
        """))
        s.execute(text("""
            CREATE TABLE IF NOT EXISTS sales_log (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                drink_name VARCHAR(50),
                sale_price NUMERIC(5,2)
            );
        """))
        s.commit()
        
        # Check if we need to seed initial prices
        check = s.execute(text("SELECT COUNT(*) FROM price_history")).scalar()
        if check == 0:
            # Seed prices using the dictionary values
            for drink, price in INITIAL_PRICES.items():
                s.execute(text(f"INSERT INTO price_history (drink_name, price) VALUES ('{drink}', {price})"))
            s.commit()

init_db()

# ==========================================
# DATA FETCHING & LOGIC FUNCTIONS
# ==========================================
def get_price_history():
    """Fetches the full price history for the Plotly chart."""
    df = conn.query("SELECT timestamp, drink_name, price FROM price_history ORDER BY timestamp ASC", ttl=0)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df

def get_current_prices():
    """Gets the latest price and the previous price (for deltas) for each drink."""
    df = get_price_history()
    if df.empty:
        return {}
    
    current_prices = {}
    for drink in DRINKS:
        drink_df = df[df['drink_name'] == drink]
        if not drink_df.empty:
            prices = drink_df['price'].tolist()
            current = float(prices[-1])
            prev = float(prices[-2]) if len(prices) > 1 else current
            current_prices[drink] = {"current": current, "delta": current - prev}
        else:
            # Fallback to the individual initial price if no history exists for this specific drink
            current_prices[drink] = {"current": INITIAL_PRICES[drink], "delta": 0.0}
    return current_prices

def register_sale(sold_drink, current_prices):
    """The Pricing Engine: Increases sold drink, decreases others, logs the sale."""
    with conn.session as s:
        # Log the sale
        sold_price = current_prices[sold_drink]["current"]
        s.execute(text(f"INSERT INTO sales_log (drink_name, sale_price) VALUES ('{sold_drink}', {sold_price})"))
        
        # Calculate and log new prices for ALL drinks
        for drink in DRINKS:
            old_price = current_prices[drink]["current"]
            
            if drink == sold_drink:
                new_price = min(old_price + BUMP_BUY, CEIL_PRICE)
            else:
                new_price = max(old_price - DECAY_PASSIVE, FLOOR_PRICE)
                
            s.execute(text(f"INSERT INTO price_history (drink_name, price) VALUES ('{drink}', {new_price})"))
        s.commit()

def trigger_market_crash():
    """Drops all prices to the floor immediately."""
    with conn.session as s:
        for drink in DRINKS:
            s.execute(text(f"INSERT INTO price_history (drink_name, price) VALUES ('{drink}', {FLOOR_PRICE})"))
        s.commit()

def get_sales_log():
    return conn.query("SELECT * FROM sales_log ORDER BY timestamp DESC", ttl=0)

# ==========================================
# APP ROUTING (Single App Logic)
# ==========================================
query_params = st.query_params
is_admin = query_params.get("admin") == st.secrets["ADMIN_PASS"]

current_prices = get_current_prices()

# ==========================================
# USER INTERFACE: PUBLIC DASHBOARD
# ==========================================
st.title("📈 The Beer Exchange")
st.markdown("### Live Market Ticker")

# Metrics Display (Corporate Ticker Style)
cols = st.columns(len(DRINKS))
for i, drink in enumerate(DRINKS):
    if drink in current_prices:
        data = current_prices[drink]
        cols[i].metric(
            label=drink, 
            value=f"${data['current']:.2f}", 
            delta=f"${data['delta']:.2f}"
        )

# Plotly Price History Chart
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
    fig.update_layout(yaxis_title="Price ($)", xaxis_title="Time", hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)

# ==========================================
# USER INTERFACE: BARTENDER PANEL (HIDDEN)
# ==========================================
if is_admin:
    st.sidebar.title("Bartender Panel")
    st.sidebar.markdown("Register sales to manipulate the market.")
    
    for drink in DRINKS:
        if st.sidebar.button(f"Sell: {drink} (${current_prices[drink]['current']:.2f})", use_container_width=True):
            register_sale(drink, current_prices)
            st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.error("EMERGENCY CONTROLS")
    if st.sidebar.button("TRIGGER MARKET CRASH", use_container_width=True):
        trigger_market_crash()
        st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.success("DATA EXPORT")
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
# REFRESH LOGIC
# ==========================================
if not is_admin:
    time.sleep(3)
    st.rerun()
