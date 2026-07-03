"""
SMART SCHOOL DASHBOARD
- Login system with persistent Turso (SQLite-compatible) database
- Teachers enter attendance (saved to the same Turso database)
- Live ESP canteen data (Adafruit IO via MQTT)
- Admin/Principal Attendance Deletion
- Twilio WhatsApp Gas Leak Alerts
"""

import streamlit as st
import base64
import paho.mqtt.client as mqtt
import streamlit.components.v1 as components
import pandas as pd
import re
import libsql_client
from datetime import datetime

# ================== CONFIG ==================

AIO_USERNAME = st.secrets["AIO_USERNAME"]
AIO_KEY = st.secrets["AIO_KEY"]

TURSO_DATABASE_URL = st.secrets["TURSO_DATABASE_URL"]
TURSO_AUTH_TOKEN = st.secrets["TURSO_AUTH_TOKEN"]

FEEDS = ["gas-status", "waste-bin", "kitchen-health", "fan-status", "valve-status", "event-log"]

st.set_page_config(page_title="School Dashboard", page_icon="🏫", layout="wide")

# ================== SQL DATABASE (Turso) ==================

class _CursorShim:
    def __init__(self, client):
        self._client = client
        self._rows = []

    def execute(self, sql, params=None):
        result = self._client.execute(sql, list(params) if params else [])
        self._rows = list(result.rows)
        return self

    def executemany(self, sql, seq_of_params):
        for params in seq_of_params:
            self._client.execute(sql, list(params))

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

class _ConnectionShim:
    def __init__(self, url, auth_token):
        self._client = libsql_client.create_client_sync(url=url, auth_token=auth_token)

    def cursor(self):
        return _CursorShim(self._client)

    def commit(self):
        pass

    def close(self):
        self._client.close()

def get_db_connection():
    return _ConnectionShim(TURSO_DATABASE_URL, TURSO_AUTH_TOKEN)

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            teacher TEXT NOT NULL,
            class TEXT NOT NULL,
            present INTEGER NOT NULL,
            total INTEGER NOT NULL
        )
    """)
    conn.commit()
    
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        default_users = [
            ("Admin", "admin123"),
            ("Principal", "principal123456789##")
        ]
        cursor.executemany("INSERT INTO users (username, password) VALUES (?, ?)", default_users)
        conn.commit()
    conn.close()

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
        .metric-card h3 { color: #8e9bb0; font-size: 14px; font-weight: 500; margin-bottom: 8px; }
        .metric-card h1 { color: #ffffff; font-size: 32px; font-weight: 700; margin: 0; }
        div.stButton > button { background: linear-gradient(90deg, #4f46e5, #3b82f6) !important; color: white !important; border-radius: 10px !important; border: none !important; font-weight: 600 !important; }
        div[data-testid="stTextInput"] input, div[data-testid="stNumberInput"] input { background-color: #161925 !important; color: #ffffff !important; border: 1px solid #282f4a !important; border-radius: 10px !important; }
        .login-box { max-width: 400px; margin: 80px auto; padding: 40px; border-radius: 20px; background: #12141c; border: 1px solid #282f4a; }
        @media print { div[data-testid="stSidebar"], button, .stDownloadButton { display: none !important; } div[data-testid="stMainBlockContainer"] { padding: 0 !important; width: 100% !important; } }
        </style>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        * { font-family: 'Inter', sans-serif; }
        .stApp { background-color: #f3f4f6 !important; }
        [data-testid="stSidebar"] { background-color: #ffffff !important; border-right: 1px solid #e5e7eb; }
        .metric-card { background: #ffffff; border-radius: 16px; padding: 22px; text-align: center; border: 1px solid #e5e7eb; }
        .login-box { max-width: 400px; margin: 80px auto; padding: 40px; border-radius: 20px; background: #ffffff; border: 1px solid #e5e7eb; }
        .stMarkdown, p, label, h1, h2, h3, h4, h5, h6, span { color: #111827 !important; }
        @media print { div[data-testid="stSidebar"], button, .stDownloadButton { display: none !important; } div[data-testid="stMainBlockContainer"] { padding: 0 !important; width: 100% !important; } }
        </style>
        """, unsafe_allow_html=True)

apply_theme()

# ================== SESSION STATE ==================

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = ""

# ================== WHATSAPP ALERT LOGIC ==================

def send_whatsapp_alert(gas_status):
    """Sends hardware alert via Twilio API when ESP board detects gas."""
    try:
        from twilio.rest import Client
        account_sid = st.secrets["TWILIO_ACCOUNT_SID"]
        auth_token = st.secrets["TWILIO_AUTH_TOKEN"]
        my_number = st.secrets["WHATSAPP_PHONE"]
        
        client = Client(account_sid, auth_token)
        msg_body = f"🚨 *SMART SCHOOL EMERGENCY*\n\n⚠️ Gas Leak Detected!\n• Status: {gas_status}\n• Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        client.messages.create(
            from_='whatsapp:+14155238886', 
            body=msg_body,
            to=f'whatsapp:{my_number}'
        )
    except Exception as e:
        print(f"Twilio WhatsApp alert failed: {e}")

# ================== MQTT LOGIC ==================

GAS_LEAK_VALUES = {"leak", "gas leak", "gas detected", "danger", "1", "unsafe"}

@st.cache_resource
def get_mqtt_data():
    alert_state = {"gas_leak_active": False}
    data = {feed: "—" for feed in FEEDS}

    def on_connect(client, userdata, flags, rc):
        for feed in FEEDS:
            client.subscribe(f"{AIO_USERNAME}/feeds/{feed}")

    def on_message(client, userdata, msg):
        feed_name = msg.topic.split("/")[-1]
        val = msg.payload.decode()
        data[feed_name] = val

        # Hardware systems logic trigger for Gas Leak
        if feed_name == "gas-status":
            is_leak = str(val).strip().lower() in GAS_LEAK_VALUES
            if is_leak and not alert_state["gas_leak_active"]:
                send_whatsapp_alert(val)
                alert_state["gas_leak_active"] = True
            elif not is_leak:
                alert_state["gas_leak_active"] = False

    # Bug fix: Compatibility for newer paho-mqtt versions
    try:
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION1)
    except AttributeError:
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
    st.markdown("<h1 style='text-align:center; padding-top: 40px;'>🏫 Smart School Dashboard</h1>", unsafe_allow_html=True)
    st.markdown("<div class='login-box'>", unsafe_allow_html=True)
    
    tab1, tab2 = st.tabs(["🔒 Secure Login", "📝 Teacher Registration"])
    
    with tab1:
        st.markdown("<div style='padding-top:15px;'></div>", unsafe_allow_html=True)
        uid = st.text_input("Teacher / Admin ID", key="login_uid")
        pwd = st.text_input("Password", type="password", key="login_pwd")
        
        if st.button("Sign In", use_container_width=True, key="login_btn"):
            conn = get_db_connection()
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
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT username FROM users WHERE username = ?", (new_uid,))
                if cursor.fetchone():
                    st.error("This username is already registered")
                    conn.close()
                else:
                    cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (new_uid, new_pwd))
                    conn.commit()
                    conn.close()
                    st.success(f"Account '{new_uid}' saved! Slide back to login tab.")
                
    st.markdown("</div>", unsafe_allow_html=True)

# ================== DYNAMIC PRINT FRAMEWORK ==================

def print_dataframe_button(df, class_name="Report"):
    html_table = df.to_html(index=False).replace('\n', '')
    safe_id = re.sub(r'[^a-zA-Z0-9_]', '_', str(class_name))

    components.html(f"""
        <div id="print-area-{safe_id}" style="display:none;">
            <h2>🏫 Class {class_name} Attendance Report</h2>
            {html_table}
        </div>
        <button onclick="printClass_{safe_id}()" style="
            background: linear-gradient(90deg, #10b981, #059669);
            color: white; border-radius: 12px; border: none; font-weight: 600; 
            font-size: 16px; padding: 14px 28px; width: 100%; cursor: pointer;">
            🖨️ Print Class {class_name} Data Table
        </button>
        <script>
        function printClass_{safe_id}() {{
            document.getElementById("print-area-{safe_id}").style.display = "block";
            window.print();
            document.getElementById("print-area-{safe_id}").style.display = "none";
        }}
        </script>
    """, height=70)

# ================== ATTENDANCE PAGE ==================

def get_all_attendance():
    conn = get_db_connection()
    cursor = conn.cursor()
    # Added 'id' to the query so we can target records for deletion
    cursor.execute("SELECT id, date, teacher, class, present, total FROM attendance")
    rows = cursor.fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=["id", "date", "teacher", "class", "present", "total"])

def attendance_page():
    if st.session_state.username not in ["Admin", "Principal"]:
        st.header("📋 Attendance Entry")
        col1, col2, col3 = st.columns(3)
        with col1: class_name = st.text_input("Class (e.g. 10-A)")
        with col2: present = st.number_input("Present", min_value=0, step=1)
        with col3: total = st.number_input("Total students", min_value=0, step=1)

        if st.button("Submit Attendance"):
            if class_name.strip() == "": st.error("Enter a class name")
            else:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO attendance (date, teacher, class, present, total) VALUES (?, ?, ?, ?, ?)",
                    (datetime.now().strftime("%Y-%m-%d %H:%M"), st.session_state.username, class_name, present, total)
                )
                conn.commit()
                conn.close()
                st.success(f"Attendance saved for {class_name}")
        st.divider()

    # Admin/Principal View
    if st.session_state.username in ["Admin", "Principal"]:
        st.title("👑 Institutional Attendance Center")

        df = get_all_attendance()
        if not df.empty:
            unique_classes = sorted(df["class"].dropna().unique())
            for cls in unique_classes:
                st.markdown(f"## 🏫 Class {cls} Logs")
                class_filtered_df = df[df["class"] == cls].sort_values("date", ascending=False)
                
                # Hide the structural database ID from the UI table for cleaner viewing
                display_df = class_filtered_df.drop(columns=["id"])
                st.dataframe(display_df, use_container_width=True)
                
                csv_data = display_df.to_csv(index=False).encode('utf-8')
                st.download_button(label=f"📥 Export Class {cls}", data=csv_data, file_name=f"Class_{cls}.csv", key=f"dl_{cls}")
                print_dataframe_button(display_df, cls)
                st.markdown("<div style='margin-bottom: 40px; border-bottom: 2px dashed #333a52;'></div>", unsafe_allow_html=True)
            
            # --- NEW FEATURE: ATTENDANCE RECORD DELETION ---
            st.subheader("🗑️ Database Management: Delete Records")
            st.markdown("Select a specific attendance log to permanently remove it from the system.")
            
            # Format a string array to let the Admin easily identify which log to delete
            record_list = df.apply(lambda r: f"ID: {r['id']} | Date: {r['date']} | Class: {r['class']} | Teacher: {r['teacher']}", axis=1).tolist()
            
            selected_record = st.selectbox("Target Log to Purge:", record_list)
            if st.button("Permanently Delete Selected Log", type="primary"):
                # Extract just the integer ID back out of the string
                record_id = int(selected_record.split("|")[0].replace("ID:", "").strip())
                
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM attendance WHERE id = ?", (record_id,))
                conn.commit()
                conn.close()
                
                st.success(f"Record #{record_id} successfully purged.")
                st.rerun()

        else:
            st.info("No attendance records have been submitted yet.")
            
    # Normal Teacher View
    else:
        st.markdown(f"### 📖 Your Class Submissions ({st.session_state.username})")
        df = get_all_attendance()
        teacher_df = df[df["teacher"] == st.session_state.username].sort_values("date", ascending=False) if not df.empty else df
        if not teacher_df.empty: 
            st.dataframe(teacher_df.drop(columns=["id"]), use_container_width=True)
        else: 
            st.info("No records submitted yet.")

# ================== CANTEEN PAGE ==================

def canteen_page():
    st.header("🍽️ Canteen Live Status")
    st.markdown("Monitoring live metrics from Adafruit IO MQTT feeds.")
    
    data = get_mqtt_data()
    st.markdown("<div style='padding-top:20px;'></div>", unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("<div class='metric-card'>", unsafe_allow_html=True)
        st.metric("💨 Gas Status", data.get("gas-status", "—"))
        st.markdown("</div>", unsafe_allow_html=True)
        st.markdown("<div style='padding-top:20px;'></div>", unsafe_allow_html=True)
        st.markdown("<div class='metric-card'>", unsafe_allow_html=True)
        st.metric("🔄 Fan Status", data.get("fan-status", "—"))
        st.markdown("</div>", unsafe_allow_html=True)

    with col2:
        st.markdown("<div class='metric-card'>", unsafe_allow_html=True)
        st.metric("🗑️ Waste Bin", data.get("waste-bin", "—"))
        st.markdown("</div>", unsafe_allow_html=True)
        st.markdown("<div style='padding-top:20px;'></div>", unsafe_allow_html=True)
        st.markdown("<div class='metric-card'>", unsafe_allow_html=True)
        st.metric("🚰 Valve Status", data.get("valve-status", "—"))
        st.markdown("</div>", unsafe_allow_html=True)

    with col3:
        st.markdown("<div class='metric-card'>", unsafe_allow_html=True)
        st.metric("❤️ Kitchen Health", data.get("kitchen-health", "—"))
        st.markdown("</div>", unsafe_allow_html=True)
        st.markdown("<div style='padding-top:20px;'></div>", unsafe_allow_html=True)
        st.markdown("<div class='metric-card'>", unsafe_allow_html=True)
        st.metric("📋 Event Log", data.get("event-log", "—"))
        st.markdown("</div>", unsafe_allow_html=True)
        
    st.divider()
    if st.button("🔄 Force Refresh Sensor Data"):
        st.rerun()

# ================== USER MANAGEMENT PAGE ==================

def users_page():
    st.header("🗄️ System Identity Management")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM users")
    df_users = pd.DataFrame(cursor.fetchall(), columns=["username"])
    conn.close()

    st.dataframe(df_users, use_container_width=True)
    st.divider()
    st.subheader("❌ Remove User Access Profile")
    
    current_user_lower = st.session_state.username.lower()
    active_profile_list = []
    
    for user in df_users["username"].tolist():
        u_lower = user.lower()
        if u_lower == current_user_lower: continue
        if current_user_lower == "principal" and u_lower == "admin": continue
        active_profile_list.append(user)
    
    if active_profile_list:
        target_user = st.selectbox("Select target account to purge:", active_profile_list)
        if st.button("Confirm Deletion", type="primary"):
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM users WHERE username = ?", (target_user,))
            conn.commit()
            conn.close()
            st.rerun()

# ================== ROUTER ==================

def main_app():
    with st.sidebar:
        st.markdown(f"### 👋 {st.session_state.username}")
        
        # Bug Fix applied here: Changed "admin" to "Admin" to match initialization case
        if st.session_state.username in ["Admin", "Principal"]:
            page = st.radio("Navigate", ["Attendance", "Canteen Dashboard", "User Management"])
        else:
            page = "Attendance"
            st.info("🔒 Advanced panels are restricted to Management.")
            
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
    elif page == "Canteen Dashboard":
        canteen_page()
    elif page == "User Management":
        users_page()

if st.session_state.logged_in:
    main_app()
else:
    login_page()