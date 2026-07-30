import streamlit as st
import pandas as pd
from datetime import datetime
import io
import requests
import base64
from typing import Optional, Dict

# ============================================================================
# CONFIGURATION
# ============================================================================

st.set_page_config(
    page_title="ET Audio Upload",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# GitHub Constants
GITHUB_OWNER = "mohammed-seid"
GITHUB_REPO = "hfc-data-private"
AUDIO_FOLDER = "audio_records"  # The folder in your repo where audio will be saved
ASSIGNMENTS_FILE = "farmer_assignments.csv" # CSV containing: username, unique_id, farmer_name

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"
ENUMERATOR_PASSWORD = "1234"

# ============================================================================
# STYLING
# ============================================================================

st.markdown("""
    <style>
    .stButton>button {
        width: 100%;
        border-radius: 8px;
        height: 54px;
        font-size: 16px;
        font-weight: 600;
        margin-bottom: 10px;
    }
    .login-box, .upload-box {
        background: var(--secondary-background-color);
        padding: 25px;
        border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
        margin: 20px 0;
        border: 1px solid #e0e0e0;
    }
    .stSelectbox {
        margin-bottom: 20px;
    }
    </style>
""", unsafe_allow_html=True)

# ============================================================================
# SESSION STATE
# ============================================================================

def initialize_session_state():
    defaults = {
        'is_authenticated': False,
        'selected_enumerator': None,
        'is_admin': False
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

initialize_session_state()

# ============================================================================
# GITHUB API FUNCTIONS
# ============================================================================

def get_github_headers() -> Dict[str, str]:
    token = st.secrets.get("github", {}).get("token")
    if not token:
        st.error("⚠️ GitHub token not configured in secrets.")
        st.stop()
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

@st.cache_data(ttl=3600)
def fetch_farmer_assignments() -> pd.DataFrame:
    """Fetch the list of farmers assigned to enumerators"""
    try:
        headers = get_github_headers()
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{ASSIGNMENTS_FILE}"
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            content = base64.b64decode(response.json()['content']).decode('utf-8')
            return pd.read_csv(io.StringIO(content))
        else:
            st.warning(f"Assignment file '{ASSIGNMENTS_FILE}' not found. Using fallback demo data.")
            # Fallback data for testing if file doesn't exist yet
            return pd.DataFrame({
                'username': ['aberash.y', 'aberash.y', 'asfaw.f'],
                'unique_id': ['1001', '1002', '1003'],
                'farmer_name': ['Abebe Kebede', 'Tigist Bekele', 'Dawit Lemma']
            })
    except Exception as e:
        st.error(f"Error loading assignments: {str(e)}")
        return pd.DataFrame()

def save_audio_to_github(audio_bytes: bytes, filename: str) -> bool:
    """Uploads the binary audio data directly to the GitHub repository"""
    try:
        headers = get_github_headers()
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{AUDIO_FOLDER}/{filename}"
        
        # Convert binary audio to base64 string for the GitHub API
        encoded_audio = base64.b64encode(audio_bytes).decode("utf-8")
        
        payload = {
            "message": f"Upload audio record: {filename}",
            "content": encoded_audio,
            "branch": "main"
        }
        
        response = requests.put(url, headers=headers, json=payload, timeout=15)
        
        # 201 means Created successfully, 200 means Updated
        if response.status_code in [200, 201]:
            return True
        else:
            st.error(f"GitHub API Error: {response.json().get('message')}")
            return False
            
    except Exception as e:
        st.error(f"Upload failed: {str(e)}")
        return False

# ============================================================================
# AUTHENTICATION
# ============================================================================

def render_login_page(df_assignments: pd.DataFrame):
    st.title("🎙️ Audio Upload Portal")
    st.markdown("---")
    
    # Extract unique enumerators from the assignment dataset
    valid_enumerators = sorted(df_assignments['username'].dropna().unique().tolist())
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown('<div class="login-box">', unsafe_allow_html=True)
        st.subheader("👤 Enumerator Login")
        with st.form("enumerator_login"):
            username = st.selectbox("Select Username", options=[""] + valid_enumerators, index=0)
            password = st.text_input("Password", type="password")
            submit = st.form_submit_button("🚀 Login", type="primary")
            
            if submit:
                if username and password == ENUMERATOR_PASSWORD:
                    st.session_state.is_authenticated = True
                    st.session_state.selected_enumerator = username
                    st.session_state.is_admin = False
                    st.rerun()
                else:
                    st.error("❌ Invalid credentials")
        st.markdown('</div>', unsafe_allow_html=True)
        
    with col2:
        st.markdown('<div class="login-box">', unsafe_allow_html=True)
        st.subheader("👑 Admin Login")
        with st.form("admin_login"):
            admin_user = st.text_input("Username")
            admin_pass = st.text_input("Password", type="password")
            admin_submit = st.form_submit_button("🔑 Login", type="secondary")
            
            if admin_submit:
                if admin_user == ADMIN_USERNAME and admin_pass == ADMIN_PASSWORD:
                    st.session_state.is_authenticated = True
                    st.session_state.selected_enumerator = "admin"
                    st.session_state.is_admin = True
                    st.rerun()
                else:
                    st.error("❌ Invalid admin credentials")
        st.markdown('</div>', unsafe_allow_html=True)

# ============================================================================
# ENUMERATOR UPLOAD INTERFACE
# ============================================================================

def render_upload_interface(df_assignments: pd.DataFrame):
    enumerator = st.session_state.selected_enumerator
    
    col1, col2 = st.columns([6, 1])
    with col1:
        st.title("🎙️ Farmer Audio Upload")
        st.markdown(f"**Logged in as:** {enumerator}")
    with col2:
        if st.button("🚪 Logout", type="secondary"):
            st.session_state.is_authenticated = False
            st.rerun()
            
    st.markdown("---")
    
    # 1. Filter farmers for this enumerator
    my_farmers = df_assignments[df_assignments['username'] == enumerator]
    
    if my_farmers.empty:
        st.warning("You currently have no farmers assigned to you.")
        return
        
    # Create a clean display string for the dropdown
    my_farmers['display_name'] = my_farmers['farmer_name'] + " (ID: " + my_farmers['unique_id'].astype(str) + ")"
    farmer_options = ["-- Select a Farmer --"] + my_farmers['display_name'].tolist()
    
    st.markdown('<div class="upload-box">', unsafe_allow_html=True)
    
    # 2. Select Farmer
    st.subheader("1️⃣ Select Farmer")
    selected_display = st.selectbox("Whose interview file are you uploading?", options=farmer_options)
    
    if selected_display != "-- Select a Farmer --":
        # Extract the specific row for the selected farmer
        farmer_row = my_farmers[my_farmers['display_name'] == selected_display].iloc[0]
        farmer_id = str(farmer_row['unique_id'])
        farmer_name = farmer_row['farmer_name']
        
        st.markdown("---")
        st.subheader("2️⃣ Upload Audio File")
        st.info(f"Please select the pre-recorded audio file for **{farmer_name}** from your device.")
        
        # File Uploader tailored for mobile audio files
        uploaded_audio = st.file_uploader(
            "Choose an audio file", 
            type=['wav', 'mp3', 'm4a', 'ogg', 'amr', 'aac', 'flac']
        )
        
        if uploaded_audio is not None:
            final_audio_bytes = uploaded_audio.getvalue()
            # Extract extension from uploaded file
            file_extension = uploaded_audio.name.split('.')[-1].lower()
            
            # Allow them to listen and confirm the file is correct before uploading
            st.markdown("**Preview your selected file:**")
            try:
                st.audio(final_audio_bytes, format=f'audio/{file_extension}')
            except Exception:
                st.caption("*(Preview may not be available for all file types in browser, but upload will still work)*")
            
            # 3. Save Button
            st.markdown("---")
            st.subheader("3️⃣ Save & Upload")
            
            # Generate a clean filename: username_uniqueid_timestamp.extension
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = farmer_name.replace(' ', '_').lower()
            filename = f"{enumerator}_{farmer_id}_{safe_name}_{timestamp}.{file_extension}"
            
            if st.button("📤 Upload Audio to Database", type="primary"):
                with st.spinner("Uploading audio file to secure repository... please wait."):
                    success = save_audio_to_github(final_audio_bytes, filename)
                    
                    if success:
                        st.success(f"✅ Successfully uploaded: `{filename}`")
                        st.balloons()
                    else:
                        st.error("❌ Failed to upload. Please try again.")

    st.markdown('</div>', unsafe_allow_html=True)

# ============================================================================
# ADMIN INTERFACE
# ============================================================================

def render_admin_interface():
    col1, col2 = st.columns([6, 1])
    with col1:
        st.title("👑 Admin - Audio Records")
    with col2:
        if st.button("🚪 Logout", type="secondary"):
            st.session_state.is_authenticated = False
            st.rerun()
            
    st.markdown("---")
    
    st.info("As an admin, you can view the repository directly to download and review the uploaded audio files.")
    
    url = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/tree/main/{AUDIO_FOLDER}"
    st.markdown(f"""
        ### 📂 Access Audio Files
        All audio files uploaded by enumerators are saved securely in the private repository.
        
        👉 **[Click here to view the audio folder on GitHub]({url})**
    """)

# ============================================================================
# MAIN
# ============================================================================

def main():
    # 1. Fetch Assignments
    with st.spinner("Loading enumerator assignments..."):
        df_assignments = fetch_farmer_assignments()
        
    # 2. Routing
    if not st.session_state.is_authenticated:
        render_login_page(df_assignments)
    else:
        if st.session_state.is_admin:
            render_admin_interface()
        else:
            render_upload_interface(df_assignments)

if __name__ == "__main__":
    main()