"""
SMART SCHOOL DASHBOARD
- Login system with SQLite Database persistent tracking
- Teachers enter attendance (saved to CSV)
- Live ESP canteen data (Adafruit IO via MQTT)
"""

import streamlit as st
import paho.mqtt.client as mqtt
import pandas as pd
import os
import sqlite3  # 🗄️ Python's native SQL database engine
from datetime import datetime

# ================== CONFIG ==================

AIO_USERNAME = st.secrets["AIO_USERNAME"]
AIO_KEY = st.secrets["AIO_KEY"]

FEEDS = ["gas-status", "waste-bin", "kitchen-health", "fan-status", "valve-status", "event-log"]
ATTENDANCE_FILE = "attendance.csv"
DB_FILE = "School.Data"  # The name of your local SQL database file

st.set_page_config(page_title="School Dashboard", page_icon="🏫", layout="wide")

# ================== SQL DATABASE INITIALIZATION ==================

def init_db():
    """Initializes the SQLite database, creates tables, and seeds initial users if empty."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 1. Create the user authentication table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL
        )
    """)
    
    # 2. Check if the table is empty. If it is, seed our default system accounts
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        default_users = [
            ("teacher1", "pass123"),
            ("teacher2", "pass123"),
            ("admin", "admin123"),
            ("Principal", "principal123456789##")
        ]
        # Bulk insert layout logic
        cursor.executemany("INSERT INTO users (username, password) VALUES (?, ?)", default_users)
        conn.commit()
        
    conn.close()

# Spin up the SQL infrastructure on boot execution
init_db()

# ================== DYNAMIC THEME ENGINE ==================

if "theme" not in st.session_state:
    st.session_state.theme = "dark"

def apply_theme():
    if st.session_state.theme == "dark":
        st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        * { font-family: 'Inter', sans-serif; }
        .stApp { background-color: #0b0c10 !important; }
        [data-testid="stSidebar"] { background-color: #12141c !important; border-right: 1px solid #1f2438; }
        .metric-card {
            background: linear-gradient(145deg, #161925, #1d2133);
            border-radius: 16px;
            padding: 22px;
            text-align: center;
            border: 1px solid #282f4a;
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.3);
            transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
        }
        .metric-card:hover {
            transform: translateY(-4px);
            border-color: #3ddc97;
            box-shadow: 0 8px 25px rgba(61, 220, 151, 0.15);
        }
        .metric-card h3 { color: #8e9bb0; font-size: 14px; font-weight: 500; letter-spacing: 0.5px; margin-bottom: 8px; }
        .metric-card h1 { color: #ffffff; font-size: 32px; font-weight: 700; margin: 0; }
        div.stButton > button {
            background: linear-gradient(90deg, #4f46e5, #3b82f6) !important;
            color: white !important;
            border-radius: 10px !important;
            border: none !important;
            font-weight: 600 !important;
            padding: 10px 24px !important;
            transition: all 0.2s ease-in-out !important;
            box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3) !important;
        }
        div.stButton > button:hover { transform: scale(1.02) !important; box-shadow: 0 6px 20px rgba(59, 130, 246, 0.5) !important; }
        div[data-testid="stTextInput"] input, div[data-testid="stNumberInput"] input {
            background-color: #161925 !important;
            color: #ffffff !important;
            border: 1px solid #282f4a !important;
            border-radius: 10px !important;
        }
        div[data-testid="stTextInput"] input:focus, div[data-testid="stNumberInput"] input:focus {
            border-color: #3b82f6 !important;
            box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.2) !important;
        }
        .login-box {
            max-width: 400px;
            margin: 80px auto;
            padding: 40px;
            border-radius: 20px;
            background: #12141c;
            border: 1px solid #282f4a;
            box-shadow: 0 15px 35px rgba(0,0,0,0.5);
        }
        button[data-baseweb="tab"] { font-size: 14px !important; font-weight: 600 !important; padding: 10px 16px !important; }
        div[data-testid="stNotification"] { border-radius: 10px !important; margin-top: 15px; }

        /* 🖨️ PDF PRINT LAYOUT MODIFICATION (DARK MODE) */
        @media print {
            div[data-testid="stSidebar"] { display: none !important; }
            div[data-testid="stMainBlockContainer"] { padding: 0 !important; width: 100% !important; }
            button, .stDownloadButton { display: none !important; }
        }
        </style>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        * { font-family: 'Inter', sans-serif; }
        .stApp { background-color: #f3f4f6 !important; }
        [data-testid="stSidebar"] { background-color: #ffffff !important; border-right: 1px solid #e5e7eb; }
        .metric-card {
            background: #ffffff;
            border-radius: 16px;
            padding: 22px;
            text-align: center;
            border: 1px solid #e5e7eb;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03);
            transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
        }
        .metric-card:hover {
            transform: translateY(-4px);
            border-color: #2563eb;
            box-shadow: 0 10px 20px -5px rgba(0, 0, 0, 0.1);
        }
        .metric-card h3 { color: #4b5563 !important; font-size: 14px; font-weight: 500; letter-spacing: 0.5px; margin-bottom: 8px; }
        .metric-card h1 { color: #111827 !important; font-size: 32px; font-weight: 700; margin: 0; }
        div.stButton > button {
            background: linear-gradient(90deg, #2563eb, #1d4ed8) !important;
            color: white !important;
            border-radius: 10px !important;
            border: none !important;
            font-weight: 600 !important;
            padding: 10px 24px !important;
            transition: all 0.2s ease-in-out !important;
            box-shadow: 0 4px 12px rgba(37, 99, 235, 0.2) !important;
        }
        div.stButton > button:hover { transform: scale(1.02) !important; box-shadow: 0 6px 20px rgba(37, 99, 235, 0.4) !important; }
        div[data-testid="stTextInput"] input, div[data-testid="stNumberInput"] input {
            background-color: #ffffff !important;
            color: #111827 !important;
            border: 1px solid #d1d5db !important;
            border-radius: 10px !important;
        }
        .login-box {
            max-width: 400px;
            margin: 80px auto;
            padding: 40px;
            border-radius: 20px;
            background: #ffffff;
            border: 1px solid #e5e7eb;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
        }
        .stMarkdown, p, label, h1, h2, h3, h4, h5, h6, span { color: #111827 !important; }
        div[data-testid="stRadio"] label { color: #374151 !important; }
        button[data-baseweb="tab"] { font-size: 14px !important; font-weight: 600 !important; padding: 10px 16px !important; }
        div[data-testid="stNotification"] { border-radius: 10px !important; margin-top: 15px; }

        /* 🖨️ PDF PRINT LAYOUT MODIFICATION (LIGHT MODE) */
        @media print {
            div[data-testid="stSidebar"] { display: none !important; }
            div[data-testid="stMainBlockContainer"] { padding: 0 !important; width: 100% !important; }
            button, .stDownloadButton { display: none !important; }
        }
        </style>
        """, unsafe_allow_html=True)
apply_theme()
# ================== SESSION STATE ==================

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = ""

# ================== MQTT (cached, one connection) ==================

@st.cache_resource
def get_mqtt_data():
    data = {feed: "—" for feed in FEEDS}

    def on_connect(client, userdata, flags, rc):
        for feed in FEEDS:
            client.subscribe(f"{AIO_USERNAME}/feeds/{feed}")

    def on_message(client, userdata, msg):
        feed_name = msg.topic.split("/")[-1]
        data[feed_name] = msg.payload.decode()

    client = mqtt.Client()
    client.username_pw_set(AIO_USERNAME, AIO_KEY)
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect("io.adafruit.com", 1883, 60)
        client.loop_start()
    except Exception as e:
        st.warning(f"Could not connect to Adafruit IO: {e}")
    return data

# ================== LOGIN PAGE (WITH SQL INTERACTION) ==================

def login_page():
    st.markdown("<h1 style='text-align:center; padding-top: 40px;'>🏫 Smart School Dashboard</h1>", unsafe_allow_html=True)
    st.markdown("<div class='login-box'>", unsafe_allow_html=True)
    
    tab1, tab2 = st.tabs(["🔒 Secure Login", "📝 Teacher Registration"])
    
    with tab1:
        st.markdown("<div style='padding-top:15px;'></div>", unsafe_allow_html=True)
        uid = st.text_input("Teacher / Admin ID", key="login_uid")
        pwd = st.text_input("Password", type="password", key="login_pwd")
        
        if st.button("Sign In", use_container_width=True, key="login_btn"):
            # 🔍 SQL QUERY: Fetch password matching username
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute("SELECT password FROM users WHERE username = ?", (uid,))
            record = cursor.fetchone()
            conn.close()
            
            if record and record[0] == pwd:
                st.session_state.logged_in = True
                st.session_state.username = uid
                st.rerun()
            else:
                st.error("Invalid ID or password")
                
    with tab2:
        st.markdown("<div style='padding-top:15px;'></div>", unsafe_allow_html=True)
        new_uid = st.text_input("Choose New Username", key="reg_uid")
        new_pwd = st.text_input("Create Secure Password", type="password", key="reg_pwd")
        confirm_pwd = st.text_input("Confirm Password", type="password", key="reg_confirm_pwd")
        
        if st.button("Register Account", use_container_width=True, key="reg_btn"):
            if not new_uid.strip() or not new_pwd.strip():
                st.error("Fields cannot be left blank")
            elif new_pwd != confirm_pwd:
                st.error("Passwords do not match")
            else:
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                
                # 🔍 SQL QUERY: Check if username already exists
                cursor.execute("SELECT username FROM users WHERE username = ?", (new_uid,))
                if cursor.fetchone():
                    st.error("This username is already registered")
                    conn.close()
                else:
                    # 📥 SQL QUERY: Insert new teacher record securely
                    cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (new_uid, new_pwd))
                    conn.commit()
                    conn.close()
                    st.success(f"Account '{new_uid}' saved to Database! Slide back to login tab.")
                
    st.markdown("</div>", unsafe_allow_html=True)

# ================== ATTENDANCE PAGE ==================

def attendance_page():
    st.header("📋 Attendance Entry")

    # 📥 standard input entry grid
    col1, col2, col3 = st.columns(3)
    with col1:
        class_name = st.text_input("Class (e.g. 10-A)")
    with col2:
        present = st.number_input("Present", min_value=0, step=1)
    with col3:
        total = st.number_input("Total students", min_value=0, step=1)

    if st.button("Submit Attendance"):
        if class_name.strip() == "":
            st.error("Enter a class name")
        else:
            new_row = pd.DataFrame([{
                "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "teacher": st.session_state.username,
                "class": class_name,
                "present": present,
                "total": total,
            }])
            if os.path.exists(ATTENDANCE_FILE):
                new_row.to_csv(ATTENDANCE_FILE, mode="a", header=False, index=False)
            else:
                new_row.to_csv(ATTENDANCE_FILE, index=False)
            st.success(f"Attendance saved for {class_name}")

    st.divider()

    # --- 🔒 SECURITY LAYER: ROLE-BASED ACCESS FILTERING ---
    if os.path.exists(ATTENDANCE_FILE):
        df = pd.read_csv(ATTENDANCE_FILE)
        
        # Condition A: Higher Privilege clearance check
        if st.session_state.username in ["admin", "Principal"]:
            st.subheader("👑 Complete School Records Overview (Management Frame)")
            filtered_df = df.sort_values("date", ascending=False)
        
        # Condition B: Restricted Teacher clearance block
        else:
            st.subheader(f"📖 Your Class Submissions ({st.session_state.username})")
            # Filters rows matching the teacher's unique login ID
            filtered_df = df[df["teacher"] == st.session_state.username].sort_values("date", ascending=False)

        # Renders the authorized dataframe
        if not filtered_df.empty:
            st.dataframe(filtered_df, use_container_width=True)
            
            # --- 📄 EXPORT PROTOCOLS ---
            col_dl, _ = st.columns([1, 2])
            with col_dl:
                csv_data = filtered_df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Download Data Table (.CSV)",
                    data=csv_data,
                    file_name=f"Attendance_Report_{st.session_state.username}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
            
            st.info("💡 **How to generate a perfect PDF:** Press **Ctrl + P** (or **Cmd + P** on Mac) to open your device's native system print view. Choose **'Save as PDF'** to instantly download a clean, structured print report of the data table displayed above.")
        else:
            st.info("No historical records matching your credentials found.")
    else:
        st.info("No attendance records have been initialized within the database yet.")

# ================== CANTEEN DASHBOARD PAGE ==================

def canteen_page():
    st.header("🍽️ Canteen Live Status")
    data = get_mqtt_data()

    cols = st.columns(3)
    labels = {
        "gas-status": ("⚠️ Gas Status", cols[0]),
        "waste-bin": ("🗑️ Waste Bin (%)", cols[1]),
        "kitchen-health": ("💚 Kitchen Health", cols[2]),
        "fan-status": ("🌀 Fan Status", cols[0]),
        "valve-status": ("🔧 Valve Status", cols[1]),
        "event-log": ("📝 Last Event", cols[2]),
    }

    for feed, (label, col) in labels.items():
        with col:
            st.markdown(f"""
            <div class="metric-card">
                <h3>{label}</h3>
                <h1>{data.get(feed, "—")}</h1>
            </div>
            """, unsafe_allow_html=True)

    st.caption("Live data pushed via MQTT from Adafruit IO — updates automatically when the ESP publishes.")
    if st.button("🔄 Refresh"):
        st.rerun()

# ================== MAIN APP ==================

def main_app():
    with st.sidebar:
        st.markdown(f"### 👋 {st.session_state.username}")
        page = st.radio("Navigate", ["Attendance", "Canteen Dashboard"])
        st.divider()
        
        is_dark = st.toggle("🌙 Dark Mode", value=(st.session_state.theme == "dark"))
        if is_dark != (st.session_state.theme == "dark"):
            st.session_state.theme = "dark" if is_dark else "light"
            st.rerun()
            
        st.divider()
        if st.button("Logout"):
            st.session_state.logged_in = False
            st.session_state.username = ""
            st.rerun()

    if page == "Attendance":
        attendance_page()
    else:
        canteen_page()

# ================== ROUTER ==================

if st.session_state.logged_in:
    main_app()
else:
    login_page()