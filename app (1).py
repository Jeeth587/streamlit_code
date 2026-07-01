"""
SMART SCHOOL DASHBOARD
- Login system
- Teachers enter attendance (saved to CSV)
- Live ESP canteen data (Adafruit IO via MQTT)
"""

import streamlit as st
import paho.mqtt.client as mqtt
import pandas as pd
import os
from datetime import datetime

# ================== CONFIG ==================

AIO_USERNAME = st.secrets["AIO_USERNAME"]
AIO_KEY = st.secrets["AIO_KEY"]

FEEDS = ["gas-status", "waste-bin", "kitchen-health", "fan-status", "valve-status", "event-log"]

# id : password (swap for a real DB later)
USERS = {
    "teacher1": "pass123",
    "teacher2": "pass123",
    "admin": "admin123",
}

ATTENDANCE_FILE = "attendance.csv"

st.set_page_config(page_title="Smart School Dashboard", page_icon="🏫", layout="wide")

# ================== STYLE ==================

st.markdown("""
<style>
.main { background-color: #0f1116; }
.metric-card {
    background: linear-gradient(135deg, #1e2130, #262b3d);
    border-radius: 14px;
    padding: 18px;
    text-align: center;
    border: 1px solid #333a52;
}
.metric-card h3 { color: #9aa4c7; font-size: 14px; margin-bottom: 6px; }
.metric-card h1 { color: #ffffff; font-size: 28px; margin: 0; }
.status-ok { color: #3ddc97; }
.status-bad { color: #ff5c5c; }
.login-box {
    max-width: 380px;
    margin: 60px auto;
    padding: 30px;
    border-radius: 16px;
    background: #1a1d29;
    border: 1px solid #333a52;
}
</style>
""", unsafe_allow_html=True)

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

# ================== LOGIN PAGE ==================

def login_page():
    st.markdown("<h1 style='text-align:center;'>🏫 Smart School Dashboard</h1>", unsafe_allow_html=True)
    st.markdown("<div class='login-box'>", unsafe_allow_html=True)
    st.subheader("Login")
    uid = st.text_input("ID")
    pwd = st.text_input("Password", type="password")
    if st.button("Login", use_container_width=True):
        if uid in USERS and USERS[uid] == pwd:
            st.session_state.logged_in = True
            st.session_state.username = uid
            st.rerun()
        else:
            st.error("Invalid ID or password")
    st.markdown("</div>", unsafe_allow_html=True)

# ================== ATTENDANCE PAGE ==================

def attendance_page():
    st.header("📋 Attendance Entry")

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

    st.subheader("Today's Records")
    if os.path.exists(ATTENDANCE_FILE):
        df = pd.read_csv(ATTENDANCE_FILE)
        st.dataframe(df.sort_values("date", ascending=False), use_container_width=True)
    else:
        st.info("No attendance records yet.")

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
