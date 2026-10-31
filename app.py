import streamlit as st
import pandas as pd
from datetime import datetime
import io
import requests
import base64
import json
import re
import time
from pathlib import Path
from typing import Optional, Dict, Callable
from urllib.parse import quote


def export_dataframe_to_excel(df: pd.DataFrame, output_path: str, sheet_name: str = "Sheet1") -> str:
    """Export a dataframe to Excel when possible, otherwise fall back to CSV."""
    for engine in ("xlsxwriter", "openpyxl"):
        try:
            with pd.ExcelWriter(output_path, engine=engine) as writer:
                df.to_excel(writer, sheet_name=sheet_name, index=False)
            return output_path
        except ModuleNotFoundError as exc:
            if exc.name not in {"xlsxwriter", "openpyxl"}:
                raise
            last_error = exc

    csv_path = output_path.rsplit(".", 1)[0] + ".csv"
    df.to_csv(csv_path, index=False)
    st.warning("Excel export is unavailable in this runtime; the data was saved as CSV instead.")
    return csv_path

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
    @media (max-width: 700px) {
        .block-container {
            padding-left: 1rem;
            padding-right: 1rem;
        }
        div[data-testid="stHorizontalBlock"] {
            flex-wrap: wrap;
            gap: 0.75rem;
        }
        div[data-testid="column"] {
            min-width: 100% !important;
        }
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

def save_audio_to_github(
    audio_bytes: bytes,
    filename: str,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Optional[str]:
    """Upload audio to GitHub and return its URL, reporting request-body progress."""
    try:
        headers = get_github_headers()
        url = (
            f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/"
            f"{AUDIO_FOLDER}/{quote(filename, safe='')}"
        )
        prefix = (
            '{"message":'
            + json.dumps(f"Upload audio record: {filename}")
            + ',"branch":"main","content":"'
        ).encode("utf-8")
        suffix = b'"}'
        encoded_size = 4 * ((len(audio_bytes) + 2) // 3)
        body_size = len(prefix) + encoded_size + len(suffix)
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(body_size)

        class UploadBody:
            def __len__(self) -> int:
                return body_size

            def __iter__(self):
                sent = 0
                yield prefix
                sent += len(prefix)
                if progress_callback:
                    progress_callback(sent, body_size)

                chunk_size = 192 * 1024
                for offset in range(0, len(audio_bytes), chunk_size):
                    chunk = base64.b64encode(audio_bytes[offset:offset + chunk_size])
                    yield chunk
                    sent += len(chunk)
                    if progress_callback:
                        progress_callback(sent, body_size)

                yield suffix
                sent += len(suffix)
                if progress_callback:
                    progress_callback(sent, body_size)

        response = requests.put(
            url,
            headers=headers,
            data=UploadBody(),
            timeout=(10, 600),
        )

        try:
            response_data = response.json()
        except ValueError:
            response_data = {}

        if response.status_code in [200, 201]:
            content_url = response_data.get("content", {}).get("html_url")
            return content_url

        message = response_data.get("message") or response.text or f"HTTP {response.status_code}"
        st.error(f"Could not upload {filename}: {message}")
        return None
            
    except Exception as e:
        st.error(f"Could not upload {filename}: {str(e)}")
        return None


def build_audio_filenames(
    enumerator: str,
    farmer_id: str,
    farmer_name: str,
    file_extensions: list[str],
    file_labels: Optional[list[str]] = None,
) -> list[str]:
    """Create organized, unique filenames with optional user-provided labels."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_enumerator = re.sub(r"[^A-Za-z0-9-]+", "_", enumerator).strip("_").lower()
    safe_farmer_id = re.sub(r"[^A-Za-z0-9-]+", "_", farmer_id).strip("_")
    safe_name = re.sub(r"[^A-Za-z0-9-]+", "_", farmer_name).strip("_").lower()
    filenames = []
    for index, extension in enumerate(file_extensions):
        suffix = f"_{index + 1}" if len(file_extensions) > 1 else ""
        label = ""
        if file_labels and index < len(file_labels):
            safe_label = re.sub(r"[^A-Za-z0-9-]+", "_", file_labels[index]).strip("_").lower()
            label = f"_{safe_label}" if safe_label else ""
        safe_extension = re.sub(r"[^A-Za-z0-9]+", "", extension.lower()) or "audio"
        filenames.append(
            f"{safe_enumerator}_{safe_farmer_id}_{safe_name}_{timestamp}{suffix}{label}.{safe_extension}"
        )
    return filenames

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
    my_farmers = my_farmers.copy()
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
        st.subheader("2️⃣ Choose audio file(s)")
        st.info(
            f"Choose recordings for **{farmer_name}** from your phone or computer. "
            "All audio formats are accepted (up to 100 MB per file); playback preview depends on your browser."
        )
        
        uploaded_audios = st.file_uploader(
            "Choose audio files",
            accept_multiple_files=True,
            key=f"audio_upload_{farmer_id}",
            help="Select one or more audio recordings. Files from your phone are supported in their original format.",
        )
        
        if uploaded_audios:
            st.subheader("3️⃣ Preview and rename")
            file_labels = []
            total_bytes = sum(uploaded_audio.size for uploaded_audio in uploaded_audios)
            for index, uploaded_audio in enumerate(uploaded_audios):
                with st.container(border=True):
                    st.caption(
                        f"File {index + 1} of {len(uploaded_audios)} · "
                        f"{uploaded_audio.size / (1024 * 1024):.1f} MB"
                    )
                    file_labels.append(
                        st.text_input(
                            "File name",
                            value=Path(uploaded_audio.name).stem,
                            key=f"audio_label_{farmer_id}_{index}",
                            help="This label is added to the organized filename saved with your recording.",
                        )
                    )
                    if uploaded_audio.type.startswith("audio/"):
                        st.audio(uploaded_audio.getvalue(), format=uploaded_audio.type)
                    else:
                        st.caption(
                            "Your browser did not identify this format for preview. "
                            "The file can still be uploaded unchanged."
                        )
            
            st.markdown("---")
            st.subheader("4️⃣ Upload")
            if any(uploaded_audio.size > 100_000_000 for uploaded_audio in uploaded_audios):
                st.error("GitHub accepts files up to 100 MB. Remove or compress any larger files before uploading.")
            elif any(not label.strip() for label in file_labels):
                st.error("Enter a file name for each recording before uploading.")
            elif any(uploaded_audio.size == 0 for uploaded_audio in uploaded_audios):
                st.error("One or more selected files are empty. Remove them and choose valid audio recordings.")
            elif st.button(
                f"Upload {len(uploaded_audios)} audio file(s)",
                type="primary",
                help="Keep this page open until all selected files finish uploading.",
            ):
                file_extensions = [
                    Path(audio.name).suffix.lstrip(".") or "audio"
                    for audio in uploaded_audios
                ]
                filenames = build_audio_filenames(
                    enumerator, farmer_id, farmer_name, file_extensions, file_labels
                )
                progress_bar = st.progress(0)
                progress_message = st.empty()
                completed_bytes = 0
                successful_uploads = []
                upload_started = time.monotonic()
                total_bytes = max(1, total_bytes)

                for index, (uploaded_audio, filename) in enumerate(zip(uploaded_audios, filenames)):
                    audio_bytes = uploaded_audio.getvalue()
                    progress_message.caption(f"Uploading file {index + 1} of {len(uploaded_audios)}: {filename}")

                    def update_progress(sent: int, body_size: int, file_size: int = uploaded_audio.size) -> None:
                        overall = min(
                            1.0,
                            (completed_bytes + file_size * sent / body_size) / total_bytes,
                        )
                        elapsed = max(time.monotonic() - upload_started, 0.1)
                        progress_bar.progress(overall)
                        status = (
                            f"Uploading file {index + 1} of {len(uploaded_audios)} · "
                            f"{overall:.0%} transferred"
                        )
                        if elapsed >= 2 and 0 < overall < 1:
                            sent_bytes = overall * total_bytes
                            remaining_seconds = (total_bytes - sent_bytes) / (sent_bytes / elapsed)
                            status += f" · about {int(remaining_seconds)} seconds remaining"
                        progress_message.caption(status)

                    file_url = save_audio_to_github(audio_bytes, filename, update_progress)
                    if file_url:
                        successful_uploads.append((filename, file_url))
                    completed_bytes += uploaded_audio.size
                    progress_bar.progress(min(1.0, completed_bytes / total_bytes))

                if len(successful_uploads) == len(uploaded_audios):
                    st.success(f"Successfully uploaded all {len(successful_uploads)} file(s) for {farmer_name}.")
                    st.balloons()
                elif successful_uploads:
                    st.warning(
                        f"Uploaded {len(successful_uploads)} of {len(uploaded_audios)} files. "
                        "Review the errors above and retry any files that failed."
                    )
                else:
                    st.error("No files were uploaded. Review the error above and try again.")

                if successful_uploads:
                    st.markdown("**Uploaded files and share links**")
                    for filename, file_url in successful_uploads:
                        if file_url:
                            st.link_button(f"Open {filename}", file_url)
                        else:
                            st.caption(f"{filename} uploaded, but GitHub did not return a file link.")

    st.markdown('</div>', unsafe_allow_html=True)

# ============================================================================
# ADMIN INTERFACE HELPERS
# ============================================================================

@st.cache_data(ttl=600)
def fetch_uploaded_audio_files() -> list[dict]:
    """Fetch all audio files from the GitHub repository"""
    try:
        headers = get_github_headers()
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{AUDIO_FOLDER}"
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            files = response.json()
            audio_files = []
            for file in files:
                if file['type'] == 'file':
                    audio_files.append({
                        'name': file['name'],
                        'size': file['size'],
                        'url': file['html_url']
                    })
            return audio_files
        else:
            st.warning("No audio files found in repository yet.")
            return []
    except Exception as e:
        st.error(f"Error fetching audio files: {str(e)}")
        return []

def parse_audio_filename(filename: str) -> dict:
    """Parse the audio filename to extract enumerator, farmer_id, and farmer_name"""
    # Format: {enumerator}_{farmer_id}_{farmer_name}_{timestamp}[_{index}][_{label}].{extension}
    try:
        # Remove extension
        name_without_ext = filename.rsplit('.', 1)[0]
        parts = name_without_ext.split('_')
        
        if len(parts) >= 4:
            enumerator = parts[0]
            farmer_id = parts[1]
            # Farmer name is everything between farmer_id and timestamp
            # Timestamp is the part with numbers and underscore like "20240115_120530"
            timestamp_start = None
            for i in range(2, len(parts)):
                if len(parts[i]) == 8 and parts[i].isdigit():  # YYYYMMDD format
                    timestamp_start = i
                    break
            
            if timestamp_start:
                farmer_name = '_'.join(parts[2:timestamp_start]).replace('_', ' ').title()
                timestamp = '_'.join(parts[timestamp_start:timestamp_start+2])
                return {
                    'enumerator': enumerator,
                    'farmer_id': farmer_id,
                    'farmer_name': farmer_name,
                    'timestamp': timestamp
                }
    except Exception:
        pass
    
    return {
        'enumerator': 'Unknown',
        'farmer_id': 'Unknown',
        'farmer_name': 'Unknown',
        'timestamp': 'Unknown'
    }

def build_admin_statistics(df_assignments: pd.DataFrame, audio_files: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Build statistics dataframes and summary info for admin dashboard"""
    
    # Parse all audio files
    parsed_files = [parse_audio_filename(f['name']) for f in audio_files]
    df_audio = pd.DataFrame(parsed_files)
    
    # 1. Enumerator Summary
    enumerators_list = sorted(df_assignments['username'].unique().tolist())
    enumerator_stats = []
    
    for enum_name in enumerators_list:
        enum_farmers = df_assignments[df_assignments['username'] == enum_name]
        total_expected = len(enum_farmers)
        
        files_uploaded = len(df_audio[df_audio['enumerator'] == enum_name]) if not df_audio.empty else 0
        remaining = max(0, total_expected - files_uploaded)
        completion_pct = (files_uploaded / total_expected * 100) if total_expected > 0 else 0
        
        # Get list of farmers for this enumerator
        farmers_str = ", ".join(enum_farmers['farmer_name'].tolist())
        farmer_ids_str = ", ".join(enum_farmers['unique_id'].astype(str).tolist())
        
        enumerator_stats.append({
            'Enumerator': enum_name,
            'Farmers Assigned': total_expected,
            'Files Uploaded': files_uploaded,
            'Remaining': remaining,
            'Completion %': f"{completion_pct:.0f}%",
            'Farmer Names': farmers_str,
            'Farmer IDs': farmer_ids_str
        })
    
    df_enumerators = pd.DataFrame(enumerator_stats)
    
    # 2. Detailed Farmer Status
    farmer_status = []
    for _, farmer in df_assignments.iterrows():
        enum_name = farmer['username']
        farmer_id = farmer['unique_id']
        farmer_name = farmer['farmer_name']
        
        # Count files for this specific farmer
        files_count = len(df_audio[(df_audio['enumerator'] == enum_name) & (df_audio['farmer_id'] == str(farmer_id))]) if not df_audio.empty else 0
        
        farmer_status.append({
            'Enumerator': enum_name,
            'Farmer Name': farmer_name,
            'Farmer ID': farmer_id,
            'Files Uploaded': files_count,
            'Status': '✅ Completed' if files_count > 0 else '⏳ Pending'
        })
    
    df_farmers = pd.DataFrame(farmer_status)
    
    # 3. Summary Statistics
    summary = {
        'total_enumerators': len(enumerators_list),
        'total_farmers': len(df_assignments),
        'total_files_uploaded': len(audio_files),
        'overall_completion': f"{len(audio_files) / len(df_assignments) * 100:.1f}%" if len(df_assignments) > 0 else "0%"
    }
    
    return df_enumerators, df_farmers, summary

def render_admin_interface(df_assignments: pd.DataFrame):
    col1, col2 = st.columns([6, 1])
    with col1:
        st.title("👑 Admin Dashboard - Upload Progress")
    with col2:
        if st.button("🚪 Logout", type="secondary"):
            st.session_state.is_authenticated = False
            st.rerun()
            
    st.markdown("---")
    
    # Fetch audio files
    with st.spinner("📊 Loading upload statistics..."):
        audio_files = fetch_uploaded_audio_files()
        df_enumerators, df_farmers, summary = build_admin_statistics(df_assignments, audio_files)
    
    # Summary Cards
    st.subheader("📈 Overall Statistics")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Enumerators", summary['total_enumerators'])
    with col2:
        st.metric("Total Farmers", summary['total_farmers'])
    with col3:
        st.metric("Audio Files Uploaded", summary['total_files_uploaded'])
    with col4:
        st.metric("Overall Completion", summary['overall_completion'])
    
    st.markdown("---")
    
    # Enumerator Summary Table
    st.subheader("👥 Enumerator Status & Farmers")
    st.dataframe(
        df_enumerators,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Enumerator": st.column_config.TextColumn("Enumerator", width="medium"),
            "Farmers Assigned": st.column_config.NumberColumn("Farmers Assigned", width="small"),
            "Files Uploaded": st.column_config.NumberColumn("Files Uploaded", width="small"),
            "Remaining": st.column_config.NumberColumn("Remaining", width="small"),
            "Completion %": st.column_config.TextColumn("Completion %", width="small"),
            "Farmer Names": st.column_config.TextColumn("Farmer Names", width="large"),
            "Farmer IDs": st.column_config.TextColumn("Farmer IDs", width="medium")
        }
    )
    
    st.markdown("---")
    
    # Detailed Farmer Status Table
    st.subheader("🚜 Detailed Farmer Status")
    st.dataframe(
        df_farmers,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Enumerator": st.column_config.TextColumn("Enumerator", width="medium"),
            "Farmer Name": st.column_config.TextColumn("Farmer Name", width="large"),
            "Farmer ID": st.column_config.TextColumn("Farmer ID", width="small"),
            "Files Uploaded": st.column_config.NumberColumn("Files Uploaded", width="small"),
            "Status": st.column_config.TextColumn("Status", width="small")
        }
    )
    
    st.markdown("---")
    
    # Direct GitHub Access
    st.subheader("📂 Access Audio Files")
    url = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/tree/main/{AUDIO_FOLDER}"
    st.info(f"[🔗 View all audio files on GitHub]({url})")
    
    # Download section
    if len(df_farmers) > 0:
        st.subheader("📥 Export Reports")
        col1, col2, col3 = st.columns(3)
        with col1:
            # Export enumerator summary
            csv_enum = df_enumerators.to_csv(index=False)
            st.download_button(
                label="📥 Download Enumerator Report",
                data=csv_enum,
                file_name=f"enumerator_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv"
            )
        with col2:
            # Export farmer status
            csv_farmers = df_farmers.to_csv(index=False)
            st.download_button(
                label="📥 Download Farmer Status",
                data=csv_farmers,
                file_name=f"farmer_status_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv"
            )
        with col3:
            # Refresh button to clear cache
            if st.button("🔄 Refresh Statistics", type="secondary"):
                st.cache_data.clear()
                st.rerun()

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
            render_admin_interface(df_assignments)
        else:
            render_upload_interface(df_assignments)

if __name__ == "__main__":
    main()