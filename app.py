import os
import io
import zipfile
import sqlite3
import base64
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from PIL import Image, ImageOps
import numpy as np
import jinja2
import pdfkit
import pandas as pd
import streamlit as st
from streamlit_drawable_canvas import st_canvas

# Enable HEIC / HEIF image format support for iPhone photos if library installed
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

# ==========================================
# 1. SETUP & DIRECTORY INITIALIZATION
# ==========================================
BASE_DIR = Path(__file__).parent.resolve()
DIRS = {
    "photos": BASE_DIR / "photos",
    "signatures": BASE_DIR / "signatures",
    "qr_codes": BASE_DIR / "qr_codes",
    "generated_pdfs": BASE_DIR / "generated_pdfs",
    "assets": BASE_DIR / "assets"
}

# YEDC Official 4 Operational Regions & Common Departments
REGIONS = ["Adamawa", "Borno", "Taraba", "Yobe"]
DEPARTMENTS = ["Technical", "Commercial", "Human Resources", "Finance & Accounts", "ICT & Operations", "Customer Care", "Corporate Communications"]

# Automatically create local storage directories
for d_path in DIRS.values():
    os.makedirs(d_path, exist_ok=True)

DB_PATH = BASE_DIR / "staff_id_data.db"
TEMPLATE_PATH = BASE_DIR / "template.html"
REPORT_TEMPLATE_PATH = BASE_DIR / "staff_report_template.html"
REQUEST_FORM_TEMPLATE_PATH = BASE_DIR / "id_request_form_template.html"
LOGO_PATH = DIRS["assets"] / "logo.png"
NYSC_LOGO_PATH = DIRS["assets"] / "nysc_logo.png"

# ==========================================
# 2. DATABASE & AUTH HELPER FUNCTIONS
# ==========================================
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def verify_password(password: str, hashed: str) -> bool:
    return hash_password(password) == hashed

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize SQLite database, handle schema migrations & seed default users."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Staff records table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS staff_records (
            emp_id TEXT PRIMARY KEY,
            full_name TEXT NOT NULL,
            category TEXT NOT NULL,
            designation TEXT DEFAULT 'Staff Member',
            department TEXT DEFAULT 'Technical',
            region TEXT DEFAULT 'Adamawa',
            photo_path TEXT NOT NULL,
            signature_path TEXT NOT NULL,
            qr_path TEXT NOT NULL
        )
    """)
    
    cursor.execute("PRAGMA table_info(staff_records)")
    columns = [col[1] for col in cursor.fetchall()]
    if "designation" not in columns:
        cursor.execute("ALTER TABLE staff_records ADD COLUMN designation TEXT DEFAULT 'Staff Member'")
    if "department" not in columns:
        cursor.execute("ALTER TABLE staff_records ADD COLUMN department TEXT DEFAULT 'Technical'")
    if "region" not in columns:
        cursor.execute("ALTER TABLE staff_records ADD COLUMN region TEXT DEFAULT 'Adamawa'")

    # Update any legacy non-matching region names in DB to Adamawa
    cursor.execute("UPDATE staff_records SET region = 'Adamawa' WHERE region NOT IN ('Adamawa', 'Borno', 'Taraba', 'Yobe')")

    # 2. Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL,
            region TEXT NOT NULL
        )
    """)
    
    # Seed default accounts if empty
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO users (username, password_hash, full_name, role, region)
            VALUES (?, ?, ?, ?, ?)
        """, ("admin", hash_password("admin123"), "System Administrator", "super_admin", "ALL"))
        
        cursor.execute("""
            INSERT INTO users (username, password_hash, full_name, role, region)
            VALUES (?, ?, ?, ?, ?)
        """, ("adamawa_admin", hash_password("adamawa123"), "Adamawa Regional Admin", "regional_admin", "Adamawa"))
        
        cursor.execute("""
            INSERT INTO users (username, password_hash, full_name, role, region)
            VALUES (?, ?, ?, ?, ?)
        """, ("borno_admin", hash_password("borno123"), "Borno Regional Admin", "regional_admin", "Borno"))

        cursor.execute("""
            INSERT INTO users (username, password_hash, full_name, role, region)
            VALUES (?, ?, ?, ?, ?)
        """, ("taraba_admin", hash_password("taraba123"), "Taraba Regional Admin", "regional_admin", "Taraba"))

        cursor.execute("""
            INSERT INTO users (username, password_hash, full_name, role, region)
            VALUES (?, ?, ?, ?, ?)
        """, ("yobe_admin", hash_password("yobe123"), "Yobe Regional Admin", "regional_admin", "Yobe"))

    # Seed Enrollment Assistant Accounts if not already created
    for reg in REGIONS:
        uname = f"{reg.lower()}_assistant"
        fullname = f"{reg} Enrollment Assistant"
        cursor.execute("""
            INSERT OR IGNORE INTO users (username, password_hash, full_name, role, region)
            VALUES (?, ?, ?, ?, ?)
        """, (uname, hash_password("assist123"), fullname, "enrollment_assistant", reg))

    conn.commit()
    conn.close()

init_db()

# User Auth Functions
def authenticate_user(username, password):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username.strip(),))
    row = cursor.fetchone()
    conn.close()
    if row and verify_password(password, row["password_hash"]):
        return dict(row)
    return None

def fetch_all_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT username, full_name, role, region FROM users ORDER BY role ASC, username ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def insert_user(username, password, full_name, role, region):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO users (username, password_hash, full_name, role, region)
            VALUES (?, ?, ?, ?, ?)
        """, (username.strip(), hash_password(password), full_name.strip(), role, region))
        conn.commit()
        return True, "User account created successfully!"
    except sqlite3.IntegrityError:
        return False, f"Username '{username}' already exists!"
    except Exception as e:
        return False, f"Database Error: {str(e)}"
    finally:
        conn.close()

def delete_user(username):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()

# Staff Database Functions (Region-scoped)
def insert_staff_record(emp_id, full_name, category, designation, department, region, photo_path, signature_path, qr_path="PENDING"):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO staff_records (emp_id, full_name, category, designation, department, region, photo_path, signature_path, qr_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (emp_id, full_name, category, designation, department, region, str(photo_path), str(signature_path), str(qr_path)))
        conn.commit()
        return True, "Record saved successfully!"
    except sqlite3.IntegrityError:
        return False, f"Employee ID '{emp_id}' already exists in database!"
    except Exception as e:
        return False, f"Database Error: {str(e)}"
    finally:
        conn.close()

def update_staff_record(emp_id, full_name, category, designation, department, region, photo_path=None):
    """Update existing staff details (Employee ID & Signature are read-only)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if photo_path:
            cursor.execute("""
                UPDATE staff_records 
                SET full_name = ?, category = ?, designation = ?, department = ?, region = ?, photo_path = ?
                WHERE emp_id = ?
            """, (full_name, category, designation, department, region, str(photo_path), emp_id))
        else:
            cursor.execute("""
                UPDATE staff_records 
                SET full_name = ?, category = ?, designation = ?, department = ?
                WHERE emp_id = ?
            """, (full_name, category, designation, department, emp_id))
        conn.commit()
        return True, "Staff record updated successfully!"
    except Exception as e:
        return False, f"Update Error: {str(e)}"
    finally:
        conn.close()

def update_qr_code(emp_id, qr_path):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE staff_records SET qr_path = ? WHERE emp_id = ?", (str(qr_path), emp_id))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def fetch_ready_records(region_filter=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if region_filter and region_filter != "ALL":
        cursor.execute("SELECT * FROM staff_records WHERE qr_path != 'PENDING' AND region = ?", (region_filter,))
    else:
        cursor.execute("SELECT * FROM staff_records WHERE qr_path != 'PENDING'")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def fetch_all_records(region_filter=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if region_filter and region_filter != "ALL":
        cursor.execute("SELECT * FROM staff_records WHERE region = ? ORDER BY emp_id ASC", (region_filter,))
    else:
        cursor.execute("SELECT * FROM staff_records ORDER BY emp_id ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def delete_staff_record(emp_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM staff_records WHERE emp_id = ?", (emp_id,))
    conn.commit()
    conn.close()

# ==========================================
# 3. HELPER UTILITIES & IMAGE / DUAL PDF RENDERER (WKHTMLTOPDF + XHTML2PDF FALLBACK)
# ==========================================
def process_and_optimize_photo(photo_file):
    raw_image = Image.open(photo_file)
    try:
        raw_image = ImageOps.exif_transpose(raw_image)
    except Exception:
        pass

    if raw_image.mode != "RGB":
        raw_image = raw_image.convert("RGB")

    optimized_image = ImageOps.fit(
        raw_image, 
        (1200, 1600), 
        centering=(0.5, 0.5), 
        method=Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS
    )
    return optimized_image

def find_wkhtmltopdf_path():
    common_paths = [
        r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe",
        r"C:\Program Files (x86)\wkhtmltopdf\bin\wkhtmltopdf.exe"
    ]
    for path in common_paths:
        if os.path.exists(path):
            return path
    return None

def get_asset_b64(rel_or_abs_path):
    """Convert local asset or photo path to base64 Data URI for 100% reliable PDF image embedding."""
    if not rel_or_abs_path:
        return ""
    p_str = str(rel_or_abs_path)
    if p_str.startswith("http://") or p_str.startswith("https://") or p_str.startswith("data:"):
        return p_str
    
    abs_p = (BASE_DIR / p_str).resolve()
    if not abs_p.exists():
        return ""
    
    ext = abs_p.suffix.lower().replace('.', '')
    mime = "image/jpeg" if ext in ["jpg", "jpeg"] else "image/png"
    try:
        with open(abs_p, "rb") as f:
            return f"data:{mime};base64,{base64.b64encode(f.read()).decode()}"
    except Exception:
        return ""

def convert_html_to_pdf(html_content, output_pdf_path, is_card=False):
    """
    Attempt PDF generation using wkhtmltopdf via pdfkit first.
    If wkhtmltopdf executable is missing or fails, automatically fallback to pure Python xhtml2pdf (pisa).
    """
    wk_path = find_wkhtmltopdf_path()
    if wk_path and os.path.exists(wk_path):
        try:
            config = pdfkit.configuration(wkhtmltopdf=wk_path)
            if is_card:
                pdf_options = {
                    'page-width': '54mm',
                    'page-height': '86mm',
                    'margin-top': '0mm',
                    'margin-bottom': '0mm',
                    'margin-left': '0mm',
                    'margin-right': '0mm',
                    'enable-local-file-access': None,
                    'encoding': 'UTF-8',
                    'no-outline': None
                }
            else:
                pdf_options = {
                    'page-size': 'A4',
                    'margin-top': '12mm',
                    'margin-bottom': '12mm',
                    'margin-left': '15mm',
                    'margin-right': '15mm',
                    'enable-local-file-access': None,
                    'encoding': 'UTF-8',
                    'no-outline': None
                }
            pdfkit.from_string(html_content, str(output_pdf_path), options=pdf_options, configuration=config)
            return True, str(output_pdf_path)
        except Exception:
            pass

    try:
        from xhtml2pdf import pisa
        with open(output_pdf_path, "wb") as pdf_file:
            pisa_status = pisa.CreatePDF(src=html_content, dest=pdf_file)
        if not pisa_status.err:
            return True, str(output_pdf_path)
        else:
            return False, f"xhtml2pdf error code {pisa_status.err}"
    except ImportError:
        return False, "Neither wkhtmltopdf nor xhtml2pdf engine is installed."
    except Exception as e:
        return False, f"PDF generation error: {str(e)}"

def generate_pdf_card(record):
    if not TEMPLATE_PATH.exists():
        return False, f"Template file not found at {TEMPLATE_PATH}"

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template_content = f.read()

    template = jinja2.Template(template_content)

    logo_uri = get_asset_b64(LOGO_PATH)
    nysc_logo_uri = get_asset_b64(NYSC_LOGO_PATH)

    rec_cat = record.get("category", "")
    if rec_cat in ["Intern", "NYSC"]:
        dept_val = "N/A"
        desig_val = rec_cat
    else:
        dept_val = record.get("department", "Technical")
        desig_val = record.get("designation", "Staff Member")

    context = {
        "org_name": "YOLA ELECTRICITY DISTRIBUTION CO.",
        "full_name": record["full_name"],
        "category": record["category"],
        "designation": desig_val,
        "department": dept_val,
        "region": record.get("region", "Adamawa"),
        "emp_id": record["emp_id"],
        "logo_path": logo_uri,
        "nysc_logo_path": nysc_logo_uri,
        "photo_path": get_asset_b64(record["photo_path"]),
        "signature_path": get_asset_b64(record["signature_path"]),
        "qr_path": get_asset_b64(record["qr_path"])
    }

    rendered_html = template.render(context)
    output_pdf_path = DIRS["generated_pdfs"] / f"{record['emp_id']}_ID_Card.pdf"
    return convert_html_to_pdf(rendered_html, output_pdf_path, is_card=True)

def generate_id_request_form_pdf(record):
    """Generate official YEDC Identity Card Request Form PDF matching HR document layout, fitting perfectly on 1 A4 page."""
    if not REQUEST_FORM_TEMPLATE_PATH.exists():
        return False, f"Request Form template not found at {REQUEST_FORM_TEMPLATE_PATH}"

    with open(REQUEST_FORM_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template_content = f.read()

    template = jinja2.Template(template_content)

    logo_uri = get_asset_b64(LOGO_PATH)
    now_dt = datetime.now()
    exp_dt = now_dt + timedelta(days=7)

    rec_cat = record.get("category", "")
    if rec_cat in ["Intern", "NYSC"]:
        dept_val = "N/A"
        desig_val = rec_cat
    else:
        dept_val = record.get("department", "TECHNICAL")
        desig_val = record.get("designation", "Staff Member")

    context = {
        "logo_path": logo_uri,
        "photo_path": get_asset_b64(record["photo_path"]),
        "signature_path": get_asset_b64(record["signature_path"]),
        "full_name": record["full_name"],
        "emp_id": record["emp_id"],
        "department": dept_val,
        "region": record.get("region", "ADAMAWA"),
        "category": record["category"],
        "designation": desig_val,
        "date_of_request": now_dt.strftime("%d / %m / %Y"),
        "expected_date": exp_dt.strftime("%d / %m / %Y")
    }

    rendered_html = template.render(context)
    output_pdf_path = DIRS["generated_pdfs"] / f"{record['emp_id']}_ID_Request_Form.pdf"
    return convert_html_to_pdf(rendered_html, output_pdf_path, is_card=False)

def generate_staff_master_pdf_report(records, region_filter="ALL"):
    """Render full master report of registered staff members to PDF."""
    if not REPORT_TEMPLATE_PATH.exists():
        return False, f"Report template file not found at {REPORT_TEMPLATE_PATH}"

    with open(REPORT_TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template_content = f.read()

    template = jinja2.Template(template_content)

    logo_uri = get_asset_b64(LOGO_PATH)
    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")

    processed_records = []
    for r in records:
        r_copy = dict(r)
        r_copy["photo_uri"] = get_asset_b64(r["photo_path"])
        processed_records.append(r_copy)

    context = {
        "region_filter": region_filter,
        "total_count": len(records),
        "generated_at": now_str,
        "logo_path": logo_uri,
        "records": processed_records
    }

    rendered_html = template.render(context)
    output_pdf_path = DIRS["generated_pdfs"] / f"YEDC_Staff_Master_Report_{region_filter}.pdf"
    return convert_html_to_pdf(rendered_html, output_pdf_path, is_card=False)

def image_to_base64(img_path):
    try:
        if not os.path.exists(img_path):
            return ""
        with open(img_path, "rb") as image_file:
            return f"data:image/png;base64,{base64.b64encode(image_file.read()).decode()}"
    except Exception:
        return ""

def create_pdf_zip_archive():
    zip_buffer = io.BytesIO()
    pdf_files = list(DIRS["generated_pdfs"].glob("*.pdf"))
    if not pdf_files:
        return None
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for pdf_path in pdf_files:
            zip_file.write(pdf_path, arcname=pdf_path.name)
    zip_buffer.seek(0)
    return zip_buffer

# ==========================================
# DELETION CONFIRMATION DIALOG MODALS
# ==========================================
if hasattr(st, "dialog"):
    @st.dialog("⚠️ Confirm Staff Deletion")
    def confirm_delete_staff_modal(emp_id, full_name):
        st.warning(f"Are you sure you want to permanently delete staff record **'{emp_id}'** ({full_name})?")
        st.caption("⚠️ This action cannot be undone.")
        st.markdown("<br>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            if st.button("🗑️ Yes, Delete Permanently", type="primary", use_container_width=True, key=f"modal_del_confirm_{emp_id}"):
                delete_staff_record(emp_id)
                st.session_state.editing_emp_id = None
                st.success(f"Staff record '{emp_id}' deleted successfully.")
                st.rerun()
        with c2:
            if st.button("❌ Cancel", use_container_width=True, key=f"modal_del_cancel_{emp_id}"):
                st.rerun()

    @st.dialog("⚠️ Confirm User Account Deletion")
    def confirm_delete_user_modal(username, full_name):
        st.warning(f"Are you sure you want to delete system user account **'{username}'** ({full_name})?")
        st.caption("⚠️ This user will no longer be able to log in to the portal.")
        st.markdown("<br>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            if st.button("🗑️ Yes, Delete User Account", type="primary", use_container_width=True, key=f"modal_del_u_confirm_{username}"):
                delete_user(username)
                st.success(f"User account '{username}' deleted successfully.")
                st.rerun()
        with c2:
            if st.button("❌ Cancel", use_container_width=True, key=f"modal_del_u_cancel_{username}"):
                st.rerun()

# ==========================================
# 4. STREAMLIT UI PAGE SETUP & ULTRA-PREMIUM ENTERPRISE STYLING
# ==========================================
st.set_page_config(
    page_title="YEDC Enterprise ID Portal",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="auto"
)

logo_b64 = image_to_base64(LOGO_PATH) if LOGO_PATH.exists() else ""
nysc_logo_b64 = image_to_base64(NYSC_LOGO_PATH) if NYSC_LOGO_PATH.exists() else ""

# GOOGLE FONTS & BESPOKE ENTERPRISE CSS DESIGN SYSTEM
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');

    /* HIDE STREAMLIT INPUT INSTRUCTION TOOLTIPS ('Press Enter to submit', etc.) */
    div[data-testid="InputInstructions"],
    .stInputInstructions,
    small[data-testid="stInputInstruction"],
    div[data-testid="stFormInstructions"],
    div[data-baseweb="tooltip"],
    [data-testid="stWidgetLabel"] ~ div small {
        display: none !important;
        visibility: hidden !important;
        opacity: 0 !important;
        height: 0 !important;
    }

    .stApp, .main, [data-testid="stAppViewContainer"] {
        background-color: #f8fafc !important;
        font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif !important;
        color: #0f172a !important;
    }
    
    .main h1, .main h2, .main h3, .main h4, .main h5, .main h6 {
        font-family: 'Outfit', sans-serif !important;
        color: #0f172a !important;
        font-weight: 800 !important;
        letter-spacing: -0.02em !important;
    }

    .main label, .main .stWidgetLabel p {
        color: #1e293b !important;
        font-weight: 700 !important;
        font-size: 13px !important;
        letter-spacing: 0.01em !important;
    }

    div[data-testid="stButton"] button,
    button[kind="secondary"],
    .main button {
        background: #ffffff !important;
        color: #0f172a !important;
        border: 1.5px solid #cbd5e1 !important;
        border-radius: 12px !important;
        font-weight: 700 !important;
        font-size: 13.5px !important;
        padding: 0.55rem 1.1rem !important;
        box-shadow: 0 2px 5px rgba(0,0,0,0.03) !important;
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
    }
    
    div[data-testid="stButton"] button p, 
    div[data-testid="stButton"] button span, 
    .main button p, .main button span {
        color: #0f172a !important;
        font-weight: 700 !important;
    }

    div[data-testid="stButton"] button:hover, .main button:hover {
        background: #0f172a !important;
        color: #ffffff !important;
        border-color: #0f172a !important;
        transform: translateY(-1px) !important;
        box-shadow: 0 4px 12px rgba(15, 23, 42, 0.15) !important;
    }
    div[data-testid="stButton"] button:hover p, 
    div[data-testid="stButton"] button:hover span, 
    .main button:hover p, .main button:hover span {
        color: #ffffff !important;
    }

    button[kind="primary"], .stFormSubmitButton > button {
        background: linear-gradient(135deg, #ff6b00 0%, #e65100 100%) !important;
        color: #ffffff !important;
        border: none !important;
        border-radius: 12px !important;
        font-weight: 800 !important;
        font-size: 14px !important;
        padding: 0.65rem 1.4rem !important;
        box-shadow: 0 4px 16px rgba(255, 107, 0, 0.35) !important;
        transition: all 0.2s ease !important;
    }
    button[kind="primary"]:hover, .stFormSubmitButton > button:hover {
        transform: translateY(-2px) !important;
        box-shadow: 0 6px 20px rgba(255, 107, 0, 0.45) !important;
    }
    button[kind="primary"] p, button[kind="primary"] span,
    .stFormSubmitButton > button p, .stFormSubmitButton > button span {
        color: #ffffff !important;
        font-weight: 800 !important;
    }

    .main input[type="text"], .main input[type="password"], .main select, div[data-baseweb="select"] > div,
    div[data-testid="stTextInput"] input {
        background-color: #ffffff !important;
        color: #0f172a !important;
        border: 1.5px solid #cbd5e1 !important;
        border-radius: 10px !important;
        font-weight: 600 !important;
        padding: 0.5rem 0.8rem !important;
        box-shadow: inset 0 1px 2px rgba(0,0,0,0.02) !important;
    }
    .main input:focus, div[data-testid="stTextInput"] input:focus {
        border-color: #ff6b00 !important;
        box-shadow: 0 0 0 3px rgba(255, 107, 0, 0.15) !important;
    }

    div[data-testid="stFileUploader"] section {
        background: #ffffff !important;
        border: 2px dashed #cbd5e1 !important;
        border-radius: 14px !important;
        padding: 1.2rem !important;
        transition: border-color 0.2s ease !important;
    }
    div[data-testid="stFileUploader"] section:hover {
        border-color: #ff6b00 !important;
    }

    div[data-testid="stExpander"] {
        background: #ffffff !important;
        border: 1px solid #e2e8f0 !important;
        border-radius: 14px !important;
        box-shadow: 0 4px 12px rgba(0,0,0,0.02) !important;
        margin-bottom: 12px !important;
        overflow: hidden !important;
    }
    div[data-testid="stExpander"] summary {
        padding: 12px 16px !important;
    }

    section[data-testid="stSidebar"] {
        background: #080e1a !important;
        border-right: 1px solid #1e293b !important;
    }

    .sidebar-header-box {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 8px 4px 14px 4px;
        border-bottom: 1px solid #1e293b;
        margin-bottom: 14px;
    }
    .sidebar-logo {
        width: 38px;
        height: 38px;
        object-fit: contain;
        background: #ffffff;
        padding: 3px;
        border-radius: 8px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    }
    .sidebar-title-main {
        color: #ffffff !important;
        font-family: 'Outfit', sans-serif !important;
        font-size: 15px;
        font-weight: 800;
        letter-spacing: 0.5px;
        line-height: 1.1;
    }
    .sidebar-title-sub {
        color: #ff6b00 !important;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.5px;
        text-transform: uppercase;
        margin-top: 2px;
    }

    section[data-testid="stSidebar"] button[kind="secondary"],
    section[data-testid="stSidebar"] button[kind="primary"] {
        padding: 0.5rem 0.9rem !important;
        font-size: 13px !important;
        margin-bottom: 3px !important;
    }

    section[data-testid="stSidebar"] button[kind="secondary"] {
        background: #0f172a !important;
        color: #94a3b8 !important;
        border: 1px solid #1e293b !important;
        border-radius: 10px !important;
        font-weight: 600 !important;
        text-align: left !important;
    }
    section[data-testid="stSidebar"] button[kind="secondary"] p,
    section[data-testid="stSidebar"] button[kind="secondary"] span {
        color: #94a3b8 !important;
    }
    section[data-testid="stSidebar"] button[kind="secondary"]:hover {
        background: #1e293b !important;
        color: #ffffff !important;
        border-color: #334155 !important;
    }
    section[data-testid="stSidebar"] button[kind="secondary"]:hover p,
    section[data-testid="stSidebar"] button[kind="secondary"]:hover span {
        color: #ffffff !important;
    }

    section[data-testid="stSidebar"] button[kind="primary"] {
        background: linear-gradient(135deg, #ff6b00 0%, #e65100 100%) !important;
        color: #ffffff !important;
        border: none !important;
        border-radius: 10px !important;
        font-weight: 800 !important;
        text-align: left !important;
        box-shadow: 0 4px 14px rgba(255, 107, 0, 0.4) !important;
    }

    .top-header-bar {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 10px 16px;
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 16px;
        box-shadow: 0 4px 16px rgba(0,0,0,0.02);
        margin-bottom: 24px;
    }
    .page-title {
        font-family: 'Outfit', sans-serif !important;
        font-size: 22px;
        font-weight: 800;
        color: #0f172a !important;
        margin: 0;
        letter-spacing: -0.02em;
    }
    .user-profile-badge {
        display: flex;
        align-items: center;
        gap: 10px;
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        padding: 4px 14px 4px 6px;
        border-radius: 24px;
    }
    .avatar-circle {
        width: 32px;
        height: 32px;
        background: linear-gradient(135deg, #ff6b00 0%, #e65100 100%);
        color: #ffffff;
        border-radius: 50%;
        font-weight: 800;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 13px;
        box-shadow: 0 2px 6px rgba(255, 107, 0, 0.3);
    }

    .region-pill-badge {
        background: #0f172a;
        color: #ffffff !important;
        font-size: 11px;
        font-weight: 800;
        padding: 4px 12px;
        border-radius: 20px;
        border: 1px solid #ff6b00;
        letter-spacing: 0.3px;
    }

    .metric-card-box {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 16px;
        padding: 18px 16px;
        text-align: center;
        box-shadow: 0 4px 16px rgba(0,0,0,0.02);
        transition: all 0.2s ease;
        position: relative;
        overflow: hidden;
    }
    .metric-card-box::after {
        content: '';
        position: absolute;
        bottom: 0;
        left: 0;
        right: 0;
        height: 3px;
        background: linear-gradient(90deg, #ff6b00, #0f172a);
    }
    .metric-card-box:hover {
        transform: translateY(-3px);
        box-shadow: 0 8px 24px rgba(0,0,0,0.06);
        border-color: #cbd5e1;
    }
    .metric-num {
        font-family: 'Outfit', sans-serif !important;
        font-size: 36px;
        font-weight: 800;
        line-height: 1;
        margin-bottom: 4px;
    }
    .metric-lbl {
        font-size: 10.5px;
        color: #64748b !important;
        font-weight: 800;
        text-transform: uppercase;
        letter-spacing: 0.8px;
    }

    .pending-badge {
        background: #d97706 !important;
        color: #ffffff !important;
        font-weight: 800 !important;
        padding: 5px 12px !important;
        border-radius: 6px !important;
        display: inline-block !important;
        font-size: 11px !important;
        text-align: center !important;
    }

    .card-preview-container {
        width: 240px;
        height: 380px;
        background: #ffffff;
        border-radius: 12px;
        border: 2px solid #0f172a;
        box-shadow: 0 12px 30px rgba(0,0,0,0.12);
        overflow: hidden;
        margin: 0 auto;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        font-family: Arial, sans-serif;
    }
    .preview-header {
        background: #0f172a;
        color: white;
        text-align: center;
        padding: 6px 4px;
        border-bottom: 3px solid #ff6b00;
    }
    .preview-photo {
        width: 105px;
        height: 138px;
        object-fit: cover;
        border: 1.5px solid #0f172a;
        border-radius: 4px;
        margin: 6px auto 3px auto;
        display: block;
        background: #f1f5f9;
    }

    .top-edit-form-card {
        background: #ffffff;
        border: 2px solid #ff6b00;
        border-radius: 18px;
        padding: 24px;
        margin-bottom: 24px;
        box-shadow: 0 12px 32px rgba(255, 107, 0, 0.12);
    }

    .online-indicator {
        width: 7px;
        height: 7px;
        background: #10b981;
        border-radius: 50%;
        display: inline-block;
        margin-right: 4px;
    }

    .nysc-icon-img {
        height: 24px;
        width: auto;
        vertical-align: middle;
        margin-right: 6px;
    }
</style>
""", unsafe_allow_html=True)

# Initialize Session Auth State
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "user" not in st.session_state:
    st.session_state.user = None
if "form_counter" not in st.session_state:
    st.session_state.form_counter = 0

# ==========================================
# 5. LOGIN SCREEN (IF UNAUTHENTICATED)
# ==========================================
if not st.session_state.authenticated:
    st.markdown("<br>", unsafe_allow_html=True)
    l_col1, l_col2, l_col3 = st.columns([1, 1.3, 1])

    with l_col2:
        logo_html = f'<img src="{logo_b64}" style="max-height: 64px; background: white; padding: 6px 12px; border-radius: 12px; border: 1px solid #cbd5e1; box-shadow: 0 4px 12px rgba(0,0,0,0.06);">' if logo_b64 else '⚡'
        
        st.markdown(f"""
        <div style="text-align: center; margin-bottom: 24px;">
            {logo_html}
            <h2 style="color: #0f172a; font-weight: 800; font-size: 26px; margin-top: 14px; margin-bottom: 2px; letter-spacing: -0.02em;">YEDC ID Management System</h2>
            <div style="display: inline-flex; align-items: center; gap: 6px; background: #fff3eb; border: 1px solid #ffccaa; color: #d95200; font-weight: 700; font-size: 11.5px; padding: 3px 12px; border-radius: 20px;">
                <span style="width:6px; height:6px; background:#ff6b00; border-radius:50%; display:inline-block;"></span> Enterprise Staff Identity Portal
            </div>
        </div>
        """, unsafe_allow_html=True)

        with st.form("login_form"):
            st.markdown("##### 🔐 System Login")
            u_name = st.text_input("Username", placeholder="e.g. admin").strip()
            u_pass = st.text_input("Password", type="password", placeholder="Enter password").strip()
            
            login_submit = st.form_submit_button("🔑 Login to Portal", type="primary", use_container_width=True)

            if login_submit:
                if not u_name or not u_pass:
                    st.error("Please enter both username and password.")
                else:
                    user_data = authenticate_user(u_name, u_pass)
                    if user_data:
                        st.session_state.authenticated = True
                        st.session_state.user = user_data
                        st.session_state.current_page = "📊 Dashboard"
                        st.session_state.editing_emp_id = None
                        st.success(f"Welcome, {user_data['full_name']}!")
                        st.rerun()
                    else:
                        st.error("Invalid username or password.")

        st.markdown("<br>", unsafe_allow_html=True)
        st.info("""
        💡 **Quick Test Credentials:**
        - **Super Admin:** Username `admin` | Password `admin123`
        - **Regional Admin:** Username `adamawa_admin` | Password `adamawa123`
        - **Enrollment Assistant:** Username `adamawa_assistant` | Password `assist123`
        """)
    st.stop()

# ==========================================
# 6. LOGGED-IN USER CONTEXT & NAVIGATION
# ==========================================
current_user = st.session_state.user
user_role = current_user["role"]
user_region = current_user["region"]
is_super_admin = (user_role == "super_admin")
is_enrollment_assistant = (user_role == "enrollment_assistant")

# Automatically collapse mobile sidebar overlay after tab selection
if st.session_state.get("close_sidebar_mobile"):
    st.session_state.close_sidebar_mobile = False
    st.markdown("""
    <script>
        setTimeout(function() {
            var closeBtn = window.parent.document.querySelector('button[aria-label="Close sidebar"]') || 
                           window.parent.document.querySelector('[data-testid="stSidebarCollapseButton"]') ||
                           window.parent.document.querySelector('button[data-testid="baseButton-headerNoPadding"]');
            if (closeBtn && window.innerWidth < 768) {
                closeBtn.click();
            }
        }, 150);
    </script>
    """, unsafe_allow_html=True)

# Smooth scroll window to top
if st.session_state.get("scroll_to_top"):
    st.session_state.scroll_to_top = False
    st.markdown("""
    <script>
        setTimeout(function() {
            var mainSec = window.parent.document.querySelector('section.main');
            if (mainSec) {
                mainSec.scrollTo({top: 0, behavior: 'smooth'});
            }
            window.parent.scrollTo({top: 0, behavior: 'smooth'});
        }, 120);
    </script>
    """, unsafe_allow_html=True)

if "current_page" not in st.session_state:
    st.session_state.current_page = "📊 Dashboard"

# Security Enforcement for Enrollment Assistants (Strictly Dashboard & Staff Register)
if is_enrollment_assistant and st.session_state.current_page not in ["📊 Dashboard", "📝 Staff Register"]:
    st.session_state.current_page = "📊 Dashboard"

with st.sidebar:
    logo_img_html = f'<img src="{logo_b64}" class="sidebar-logo">' if logo_b64 else '<div style="font-size:20px;">⚡</div>'
    
    st.markdown(f"""
    <div class="sidebar-header-box">
        {logo_img_html}
        <div>
            <div class="sidebar-title-main">YEDC ID PORTAL</div>
            <div class="sidebar-title-sub">MANAGEMENT SYSTEM</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    def nav_item(label, key):
        is_active = (st.session_state.current_page == label)
        btn_kind = "primary" if is_active else "secondary"
        if st.button(label, key=key, use_container_width=True, type=btn_kind):
            st.session_state.current_page = label
            st.session_state.close_sidebar_mobile = True
            st.rerun()

    # Allowed for all roles (Super Admin, Regional Admin, Enrollment Assistant)
    nav_item("📊 Dashboard", "btn_nav_dash")
    nav_item("📝 Staff Register", "btn_nav_reg")
    
    # Restricted from Enrollment Assistants (Admins only)
    if user_role in ["super_admin", "regional_admin"]:
        nav_item("⚙️ Batch Processing", "btn_nav_batch")
        nav_item("🔍 Staff Directory", "btn_nav_dir")
        nav_item("📈 Reports & Analytics", "btn_nav_rep")
    
    if is_super_admin:
        nav_item("👥 User Management", "btn_nav_users")

    # COMPACT USER PROFILE & LOGOUT SECTION (ZERO SCROLL FIT)
    st.markdown("""
    <div style="border-top: 1px solid #1e293b; margin-top: 10px; padding-top: 10px;">
    """, unsafe_allow_html=True)
    
    st.markdown(f"""
    <div style="color: white; font-size: 12.5px; font-weight: 700; margin-bottom: 2px;">
        <span class="online-indicator"></span>{current_user['full_name']}
    </div>
    <div style="color: #ff6b00; font-size: 10.5px; font-weight: 800; text-transform: uppercase;">Role: {user_role.replace('_', ' ').title()}</div>
    <div style="color: #94a3b8; font-size: 10.5px; margin-bottom: 8px;">Region Scope: <b>{user_region}</b></div>
    </div>
    """, unsafe_allow_html=True)

    if st.button("🚪 Logout", key="btn_logout", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.user = None
        st.session_state.current_page = "📊 Dashboard"
        st.session_state.editing_emp_id = None
        st.rerun()

selected_page = st.session_state.current_page

# Super Admin Region Filter Selection
if is_super_admin:
    if "admin_selected_region" not in st.session_state:
        st.session_state.admin_selected_region = "ALL"

# Gather Statistics based on user region scope
records_all = fetch_all_records(region_filter=user_region if not is_super_admin else st.session_state.admin_selected_region)
records_ready = fetch_ready_records(region_filter=user_region if not is_super_admin else st.session_state.admin_selected_region)
pending_qr_count = len(records_all) - len(records_ready)
generated_pdfs_count = len(list(DIRS["generated_pdfs"].glob("*.pdf")))

# Main Top Glass Header Bar
region_badge_text = f"Region: {user_region}" if not is_super_admin else f"Region Scope: {st.session_state.admin_selected_region}"
st.markdown(f"""
<div class="top-header-bar">
    <div>
        <div class="page-title">{selected_page.replace('📊 ', '').replace('📝 ', '').replace('⚙️ ', '').replace('🔍 ', '').replace('📈 ', '').replace('👥 ', '')}</div>
    </div>
    <div style="display: flex; align-items: center; gap: 12px;">
        <span class="region-pill-badge">{region_badge_text}</span>
        <div class="user-profile-badge">
            <div class="avatar-circle">{current_user['full_name'][0].upper()}</div>
            <div>
                <div style="font-size: 12px; font-weight: 700; color: #0f172a; line-height: 1;">{current_user['username']}</div>
                <div style="font-size: 10px; color: #64748b;">{user_role.replace('_', ' ').title()}</div>
            </div>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)


# ==========================================
# 7. PAGE 1: DASHBOARD OVERVIEW
# ==========================================
if selected_page == "📊 Dashboard":
    if is_super_admin:
        r_col1, r_col2 = st.columns([1, 3])
        with r_col1:
            sel_r = st.selectbox(
                "Filter View by Region:", 
                ["ALL"] + REGIONS,
                index=(["ALL"] + REGIONS).index(st.session_state.admin_selected_region) if st.session_state.admin_selected_region in (["ALL"] + REGIONS) else 0
            )
            if sel_r != st.session_state.admin_selected_region:
                st.session_state.admin_selected_region = sel_r
                st.rerun()

    st.markdown("### System Metric Overview")
    
    m1, m2, m3, m4 = st.columns(4)
    
    with m1:
        st.markdown(f"""
        <div class="metric-card-box">
            <div class="metric-num" style="color: #ff6b00;">{len(records_all)}</div>
            <div class="metric-lbl">REGISTERED STAFF</div>
        </div>
        """, unsafe_allow_html=True)

    with m2:
        st.markdown(f"""
        <div class="metric-card-box">
            <div class="metric-num" style="color: #2563eb;">{len(records_ready)}</div>
            <div class="metric-lbl">CARDS READY FOR PRINT</div>
        </div>
        """, unsafe_allow_html=True)

    with m3:
        st.markdown(f"""
        <div class="metric-card-box">
            <div class="metric-num" style="color: #7c3aed;">{pending_qr_count}</div>
            <div class="metric-lbl">PENDING QR MATCHING</div>
        </div>
        """, unsafe_allow_html=True)

    with m4:
        st.markdown(f"""
        <div class="metric-card-box">
            <div class="metric-num" style="color: #059669;">{generated_pdfs_count}</div>
            <div class="metric-lbl">PDF CARDS GENERATED</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("### Recent Registered Staff")
    
    if records_all:
        st.dataframe(
            [
                {
                    "Employee ID": r["emp_id"],
                    "Full Name": r["full_name"],
                    "Department": "N/A" if r.get("category") in ["Intern", "NYSC"] else r.get("department", "Technical"),
                    "Role / Designation": r["category"] if r.get("category") in ["Intern", "NYSC"] else r.get("designation", "Staff Member"),
                    "Category": r["category"],
                    "Region": r.get("region", "Adamawa"),
                    "QR Status": "Ready" if r["qr_path"] != "PENDING" else "Pending QR"
                }
                for r in records_all[:10]
            ],
            use_container_width=True
        )
    else:
        st.info("No staff members registered yet for this region.")


# ==========================================
# 8. PAGE 2: STAFF REGISTER & DRAFT PREVIEW
# ==========================================
elif selected_page == "📝 Staff Register":
    st.markdown("### New Staff Registration")
    
    col_form, col_prev = st.columns([1.2, 0.8], gap="large")

    f_cnt = st.session_state.form_counter

    with col_form:
        # Category Selection outside/top of form for 100% reactive Department & Role behavior
        reg_cat_key = f"inp_cat_{f_cnt}"
        category = st.selectbox("Staff Category *", ["Permanent", "Contract", "Intern", "NYSC"], key=reg_cat_key)

        with st.form(f"staff_capture_form_{f_cnt}", clear_on_submit=True):
            f_col1, f_col2 = st.columns(2)
            with f_col1:
                emp_id = st.text_input("Employee ID *", placeholder="e.g. YEDC-1045", key=f"inp_emp_{f_cnt}").strip()
                full_name = st.text_input("Full Name *", placeholder="e.g. John Doe", key=f"inp_name_{f_cnt}").strip()
                
            with f_col2:
                if category == "Intern":
                    st.text_input("Role / Designation", value="Intern", disabled=True, key=f"inp_desig_dis_{f_cnt}")
                    st.text_input("Department", value="N/A", disabled=True, key=f"inp_dept_dis_{f_cnt}")
                    designation = "Intern"
                    dept_input = "N/A"
                elif category == "NYSC":
                    st.text_input("Role / Designation", value="NYSC", disabled=True, key=f"inp_desig_dis_{f_cnt}")
                    st.text_input("Department", value="N/A", disabled=True, key=f"inp_dept_dis_{f_cnt}")
                    designation = "NYSC"
                    dept_input = "N/A"
                else:
                    desig_input = st.text_input("Role / Designation *", value="", placeholder="e.g. Linesman", key=f"inp_desig_{f_cnt}").strip()
                    designation = desig_input if desig_input else "Staff Member"
                    dept_input = st.selectbox("Department *", DEPARTMENTS, key=f"inp_dept_{f_cnt}")

            # Region Selection (Locked for Regional Admin / Enrollment Assistant, Selectable for Super Admin)
            if is_super_admin:
                staff_region = st.selectbox("Assign Region *", REGIONS, key=f"inp_reg_{f_cnt}")
            else:
                staff_region = user_region
                st.text_input("Assigned Region", value=user_region, disabled=True, key=f"inp_reg_dis_{f_cnt}")

            st.markdown("##### 📷 Staff Photo")
            st.caption("Upload passport photo file OR take photo directly using camera:")

            p_col1, p_col2 = st.columns(2)
            with p_col1:
                uploaded_photo_file = st.file_uploader("Upload Passport Photo (JPG / PNG / HEIC) *", type=["jpg", "jpeg", "png", "heic", "heif"], key=f"uploader_photo_{f_cnt}")
            with p_col2:
                captured_photo_file = st.camera_input("Take Photo using Camera *", key=f"cam_photo_{f_cnt}")

            photo_file = uploaded_photo_file if uploaded_photo_file is not None else captured_photo_file

            if photo_file is not None:
                st.caption(f"✓ Original file size: {photo_file.size / (1024*1024):.2f} MB (Optimizing to ~600 KB HD Output)")

            st.markdown("##### ✍️ Digital Signature Pad")
            st.caption("Draw staff signature inside the canvas:")

            canvas_result = st_canvas(
                fill_color="rgba(255, 255, 255, 0)",
                stroke_width=2.5,
                stroke_color="#0F172A",
                background_color="#FFFFFF",
                height=145,
                width=330,
                drawing_mode="freedraw",
                key=f"signature_canvas_{f_cnt}",
            )

            submit_btn = st.form_submit_button("💾 Save Staff Record", use_container_width=True)

    with col_prev:
        st.markdown("##### 🪪 Live Draft Preview")
        st.caption("⚠️ Temporary Preview Template (Subject to final approved design)")

        saved_rec = st.session_state.get("last_saved_record")

        if saved_rec:
            disp_name = saved_rec["full_name"]
            disp_id = saved_rec["emp_id"]
            disp_desig = saved_rec["designation"]
            disp_dept = saved_rec["department"]
            disp_cat = saved_rec["category"]
            disp_reg = saved_rec["region"]
            preview_photo_b64 = saved_rec["photo_b64"]
            preview_sig_b64 = saved_rec["sig_b64"]
        else:
            preview_photo_b64 = ""
            preview_sig_b64 = ""
            if photo_file:
                try:
                    processed_preview = process_and_optimize_photo(photo_file)
                    buf = io.BytesIO()
                    processed_preview.save(buf, format="JPEG", quality=95)
                    preview_photo_b64 = f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}"
                except Exception as e:
                    st.caption(f"Preview load notice: {str(e)}")

            disp_name = full_name if full_name else "FULL NAME"
            disp_id = emp_id if emp_id else "YEDC-0000"
            disp_cat = category if category else "Permanent"
            
            if disp_cat in ["Intern", "NYSC"]:
                disp_desig = disp_cat
                disp_dept = "N/A"
            else:
                disp_desig = designation if designation else "Role / Designation"
                disp_dept = dept_input if dept_input else "Technical"

            disp_reg = staff_region

        photo_render = f'<img src="{preview_photo_b64}" class="preview-photo">' if preview_photo_b64 else '<div class="preview-photo" style="display:flex;align-items:center;justify-content:center;color:#94a3b8;font-size:11px;">[Staff Photo]</div>'
        sig_render = f'<img src="{preview_sig_b64}" style="max-height:26px;max-width:60px;">' if preview_sig_b64 else '<div style="font-size:6.5px;color:#64748b;font-weight:700;text-transform:uppercase;">Authorized Signature</div>'

        cat_badge_html = f'<img src="{nysc_logo_b64}" style="height:12px;vertical-align:middle;margin-right:2px;"> NYSC' if (disp_cat == "NYSC" and nysc_logo_b64) else disp_cat

        st.markdown(f"""
        <div class="card-preview-container">
            <div class="preview-header">
                {f'<img src="{logo_b64}" style="max-height:18px;background:white;padding:1px 3px;border-radius:2px;">' if logo_b64 else ''}
                <div style="font-size:7.5px;font-weight:800;letter-spacing:0.5px;margin-top:2px;color:white;">YOLA ELECTRICITY DIST. CO.</div>
                <div style="font-size:5.5px;color:#ff6b00;font-weight:700;">DRAFT IDENTITY CARD ({disp_reg.upper()})</div>
            </div>
            <div style="text-align:center;padding:3px;">
                {photo_render}
                <div style="font-size:11px;font-weight:800;color:#0f172a;text-transform:uppercase;margin-top:3px;">{disp_name}</div>
                <div style="font-size:8.5px;color:#475569;font-weight:600;">{disp_desig} ({disp_dept})</div>
                <div style="background:#ff6b00;color:white;font-size:9px;font-weight:800;padding:2px 7px;border-radius:3px;display:inline-block;margin:3px 0;">ID: {disp_id}</div>
                <div><span style="background:#e2e8f0;color:#0f172a;font-size:7.5px;font-weight:700;padding:1px 5px;border-radius:2px;text-transform:uppercase;display:inline-flex;align-items:center;">{cat_badge_html}</span></div>
            </div>
            <div style="background:#f8fafc;border-top:1px solid #e2e8f0;padding:5px;display:flex;align-items:center;justify-content:space-between;height:42px;">
                {sig_render}
                <div style="width:26px;height:26px;border:1px solid #0f172a;background:#e2e8f0;font-size:5.5px;display:flex;align-items:center;justify-content:center;color:#475569;">QR</div>
            </div>
            <div style="height:3.5px;background:linear-gradient(90deg, #ff6b00, #0f172a);"></div>
        </div>
        """, unsafe_allow_html=True)

    if submit_btn:
        if category in ["Intern", "NYSC"]:
            final_dept = "N/A"
            final_desig = category
        else:
            final_dept = dept_input
            final_desig = designation

        if not emp_id:
            st.error("Please enter Employee ID.")
        elif not full_name:
            st.error("Please enter Full Name.")
        elif photo_file is None:
            st.error("Please upload or capture photo.")
        elif canvas_result is None or canvas_result.image_data is None:
            st.error("Please sign on signature pad.")
        else:
            try:
                processed_photo = process_and_optimize_photo(photo_file)

                photo_filename = f"{emp_id}_photo.jpg"
                photo_rel_path = f"photos/{photo_filename}"
                photo_abs_path = DIRS["photos"] / photo_filename
                processed_photo.save(photo_abs_path, format="JPEG", quality=95, optimize=True)

                sig_data = canvas_result.image_data.astype(np.uint8)
                sig_image = Image.fromarray(sig_data)

                # Composite signature RGBA onto solid white background to prevent transparent line cutouts
                if sig_image.mode == "RGBA":
                    bg = Image.new("RGB", sig_image.size, (255, 255, 255))
                    bg.paste(sig_image, mask=sig_image.split()[3])
                    sig_image = bg
                elif sig_image.mode != "RGB":
                    sig_image = sig_image.convert("RGB")

                sig_filename = f"{emp_id}_sig.png"
                sig_rel_path = f"signatures/{sig_filename}"
                sig_abs_path = DIRS["signatures"] / sig_filename
                sig_image.save(sig_abs_path, format="PNG")

                buf_photo = io.BytesIO()
                processed_photo.save(buf_photo, format="JPEG", quality=95)
                photo_b64_str = f"data:image/jpeg;base64,{base64.b64encode(buf_photo.getvalue()).decode()}"

                buf_sig = io.BytesIO()
                sig_image.save(buf_sig, format="PNG")
                sig_b64_str = f"data:image/png;base64,{base64.b64encode(buf_sig.getvalue()).decode()}"

                qr_rel_path = "PENDING"

                success, msg = insert_staff_record(
                    emp_id=emp_id,
                    full_name=full_name,
                    category=category,
                    designation=final_desig,
                    department=final_dept,
                    region=staff_region,
                    photo_path=photo_rel_path,
                    signature_path=sig_rel_path,
                    qr_path=qr_rel_path
                )

                if success:
                    st.session_state.form_saved_success = True
                    st.session_state.saved_emp_id = emp_id
                    st.session_state.last_saved_record = {
                        "emp_id": emp_id,
                        "full_name": full_name,
                        "category": category,
                        "designation": final_desig,
                        "department": final_dept,
                        "region": staff_region,
                        "photo_b64": photo_b64_str,
                        "sig_b64": sig_b64_str
                    }
                    st.rerun()
                else:
                    st.error(f"❌ {msg}")

            except Exception as e:
                st.error(f"Error processing photo: {str(e)}")

    if st.session_state.get("form_saved_success"):
        st.markdown("<br>", unsafe_allow_html=True)
        st.success(f"✅ Staff record for Employee ID **'{st.session_state.get('saved_emp_id')}'** ({st.session_state.last_saved_record['region']} Region) saved successfully!")
        
        act_col1, act_col2 = st.columns(2)
        with act_col1:
            if st.button("➕ Register Another Staff Member", type="primary", use_container_width=True, key="btn_reg_another_bottom"):
                st.session_state.form_saved_success = False
                st.session_state.saved_emp_id = ""
                st.session_state.last_saved_record = None
                st.session_state.form_counter += 1
                st.session_state.scroll_to_top = True
                st.rerun()
        with act_col2:
            if not is_enrollment_assistant:
                if st.button("🔍 View in Staff Directory", use_container_width=True, key="btn_view_dir_bottom"):
                    st.session_state.form_saved_success = False
                    st.session_state.saved_emp_id = ""
                    st.session_state.last_saved_record = None
                    st.session_state.current_page = "🔍 Staff Directory"
                    st.rerun()


# ==========================================
# 9. PAGE 3: ADMIN BATCH PROCESSING (Admins Only)
# ==========================================
elif selected_page == "⚙️ Batch Processing" and user_role in ["super_admin", "regional_admin"]:
    st.markdown(f"### Admin Batch QR Processing & PDF Generation ({user_region if not is_super_admin else st.session_state.admin_selected_region} Region)")

    st.markdown("##### 📦 Batch Upload QR Codes (From External Department)")
    st.caption("Upload QR Code image files named by Employee ID (e.g., `YEDC-1045.png`).")

    qr_files = st.file_uploader(
        "Upload Batch QR Code Image Files",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key="batch_qr_uploader"
    )

    if qr_files:
        if st.button("🔄 Match Batch QR Codes", type="primary"):
            updated_count = 0
            not_found = []

            for qr_file in qr_files:
                file_stem = Path(qr_file.name).stem
                save_filename = f"{file_stem}_qr{Path(qr_file.name).suffix}"
                qr_rel_path = f"qr_codes/{save_filename}"
                qr_abs_path = DIRS["qr_codes"] / save_filename

                with open(qr_abs_path, "wb") as f:
                    f.write(qr_file.getbuffer())

                if update_qr_code(file_stem, qr_rel_path):
                    updated_count += 1
                else:
                    not_found.append(file_stem)

            st.success(f"Matched {updated_count} QR Code(s)!")
            if not_found:
                st.warning(f"No DB match for IDs: {', '.join(not_found)}")
            st.rerun()

    st.divider()

    st.markdown("##### 🖨️ PDF ID Card Batch Generation")
    ready_records = fetch_ready_records(region_filter=user_region if not is_super_admin else st.session_state.admin_selected_region)

    if ready_records:
        st.dataframe(
            [
                {
                    "Employee ID": r["emp_id"],
                    "Full Name": r["full_name"],
                    "Department": "N/A" if r.get("category") in ["Intern", "NYSC"] else r.get("department", "Technical"),
                    "Role / Designation": r["category"] if r.get("category") in ["Intern", "NYSC"] else r.get("designation", "Staff"),
                    "Category": r["category"],
                    "Region": r.get("region", "Adamawa"),
                    "Status": "Ready for PDF"
                }
                for r in ready_records
            ],
            use_container_width=True
        )

        p_col1, p_col2 = st.columns([1, 1])
        with p_col1:
            if st.button("🖨️ Generate All PDF ID Cards (CR80 Standard)", type="primary", use_container_width=True):
                pdf_success_count = 0
                pdf_errors = []

                with st.spinner("Rendering templates & generating PDFs..."):
                    for record in ready_records:
                        ok, res = generate_pdf_card(record)
                        if ok:
                            pdf_success_count += 1
                        else:
                            pdf_errors.append((record["emp_id"], res))

                if pdf_success_count > 0:
                    st.success(f"Generated {pdf_success_count} PDF ID Card(s) in `generated_pdfs/`!")

                if pdf_errors:
                    st.error("Errors during PDF generation:")
                    for eid, err in pdf_errors:
                        st.write(f"- **{eid}**: {err}")

        with p_col2:
            zip_buf = create_pdf_zip_archive()
            if zip_buf:
                st.download_button(
                    label="📦 Download All PDFs as ZIP Archive",
                    data=zip_buf,
                    file_name="YEDC_Staff_ID_Cards.zip",
                    mime="application/zip",
                    use_container_width=True
                )
    else:
        st.info("No records are ready for PDF generation in this region.")


# ==========================================
# 10. PAGE 4: STAFF DIRECTORY & SEARCH (Admins Only)
# ==========================================
elif selected_page == "🔍 Staff Directory" and user_role in ["super_admin", "regional_admin"]:
    st.markdown("### Staff Directory & Records Management")

    sd_top1, sd_top2 = st.columns([2, 1])
    with sd_top1:
        if is_super_admin:
            dir_region_filter = st.selectbox(
                "Filter Directory by Region:",
                ["ALL"] + REGIONS,
                key="dir_region_filter_select"
            )
        else:
            dir_region_filter = user_region
    with sd_top2:
        st.markdown("<div style='height:28px;'></div>", unsafe_allow_html=True)
        # Export Master Staff Directory PDF Button
        all_staff_for_pdf = fetch_all_records(region_filter=dir_region_filter)
        if all_staff_for_pdf:
            ok_r, r_path_or_err = generate_staff_master_pdf_report(all_staff_for_pdf, region_filter=dir_region_filter)
            if ok_r and os.path.exists(r_path_or_err):
                with open(r_path_or_err, "rb") as pdf_report_file:
                    st.download_button(
                        label="📄 Download Master Directory (PDF)",
                        data=pdf_report_file,
                        file_name=f"YEDC_Staff_Master_Report_{dir_region_filter}.pdf",
                        mime="application/pdf",
                        type="primary",
                        use_container_width=True,
                        key="btn_dl_master_report_pdf_dir"
                    )
            else:
                st.error(f"Error generating PDF report: {r_path_or_err}")

    all_staff = fetch_all_records(region_filter=dir_region_filter)

    # PROMINENT AUTO-OPENING EDIT FORM AT TOP OF DIRECTORY
    editing_id = st.session_state.get("editing_emp_id")
    if editing_id:
        target_staff = next((s for s in all_staff if s["emp_id"] == editing_id), None)
        if not target_staff:
            target_staff = next((s for s in fetch_all_records("ALL") if s["emp_id"] == editing_id), None)

        if target_staff:
            st.markdown('<div class="top-edit-form-card">', unsafe_allow_html=True)
            t_hdr1, t_hdr2 = st.columns([3, 1])
            with t_hdr1:
                st.markdown(f"#### ✏️ Editing Staff Member: **{target_staff['emp_id']}**")
                st.caption("Update staff details below (Employee ID & Digital Signature are locked). Click 'Save Changes' to update.")
            with t_hdr2:
                if st.button("❌ Close Edit Form", key="btn_close_top_edit", use_container_width=True):
                    st.session_state.editing_emp_id = None
                    st.rerun()

            with st.form(key=f"top_edit_form_{target_staff['emp_id']}"):
                e_c1, e_c2 = st.columns(2)
                with e_c1:
                    # Employee ID strictly read-only
                    st.text_input("Employee ID (Read-Only)", value=target_staff["emp_id"], disabled=True)
                    e_name = st.text_input("Full Name *", value=target_staff["full_name"]).strip()
                    
                    cat_list = ["Permanent", "Contract", "Intern", "NYSC"]
                    cat_idx = cat_list.index(target_staff["category"]) if target_staff["category"] in cat_list else 0
                    e_cat = st.selectbox("Staff Category *", cat_list, index=cat_idx)
                with e_c2:
                    if e_cat == "Intern":
                        st.text_input("Role / Designation", value="Intern", disabled=True, key=f"edit_desig_dis_{target_staff['emp_id']}")
                        st.text_input("Department", value="N/A", disabled=True, key=f"edit_dept_dis_{target_staff['emp_id']}")
                        e_desig = "Intern"
                        e_dept = "N/A"
                    elif e_cat == "NYSC":
                        st.text_input("Role / Designation", value="NYSC", disabled=True, key=f"edit_desig_dis_{target_staff['emp_id']}")
                        st.text_input("Department", value="N/A", disabled=True, key=f"edit_dept_dis_{target_staff['emp_id']}")
                        e_desig = "NYSC"
                        e_dept = "N/A"
                    else:
                        e_desig = st.text_input("Role / Designation *", value=target_staff.get("designation", "Staff Member"), placeholder="e.g. Linesman").strip()
                        dept_idx = DEPARTMENTS.index(target_staff.get("department", "Technical")) if target_staff.get("department", "Technical") in DEPARTMENTS else 0
                        e_dept = st.selectbox("Department *", DEPARTMENTS, index=dept_idx)

                if is_super_admin:
                    reg_idx = REGIONS.index(target_staff.get("region", "Adamawa")) if target_staff.get("region", "Adamawa") in REGIONS else 0
                    e_region = st.selectbox("Assigned Region *", REGIONS, index=reg_idx)
                else:
                    e_region = target_staff.get("region", user_region)
                    st.text_input("Assigned Region", value=e_region, disabled=True)

                st.markdown("###### 📷 Update Staff Photo (Optional)")
                st.caption("Leave empty to keep existing staff photo.")
                new_photo = st.file_uploader("Upload New Photo (JPG/PNG/HEIC)", type=["jpg", "jpeg", "png", "heic", "heif"])

                eb_col1, eb_col2 = st.columns(2)
                with eb_col1:
                    e_save = st.form_submit_button("💾 Save Changes", type="primary", use_container_width=True)
                with eb_col2:
                    st.markdown("<br>", unsafe_allow_html=True)

                if e_save:
                    if not e_name:
                        st.error("Full Name cannot be empty.")
                    else:
                        try:
                            updated_photo_rel = None
                            if new_photo is not None:
                                opt_p = process_and_optimize_photo(new_photo)
                                p_fname = f"{target_staff['emp_id']}_photo.jpg"
                                p_abs = DIRS["photos"] / p_fname
                                opt_p.save(p_abs, format="JPEG", quality=95, optimize=True)
                                updated_photo_rel = f"photos/{p_fname}"

                            if e_cat in ["Intern", "NYSC"]:
                                final_edit_dept = "N/A"
                                final_edit_desig = e_cat
                            else:
                                final_edit_dept = e_dept
                                final_edit_desig = e_desig if e_desig else "Staff Member"

                            ok, u_msg = update_staff_record(
                                emp_id=target_staff["emp_id"],
                                full_name=e_name,
                                category=e_cat,
                                designation=final_edit_desig,
                                department=final_edit_dept,
                                region=e_region,
                                photo_path=updated_photo_rel
                            )

                            if ok:
                                st.success(f"✅ {u_msg}")
                                st.session_state.editing_emp_id = None
                                st.rerun()
                            else:
                                st.error(f"❌ {u_msg}")

                        except Exception as err:
                            st.error(f"Error updating record: {str(err)}")

            st.markdown('</div>', unsafe_allow_html=True)
            st.divider()

    if not all_staff:
        st.info(f"No staff records registered yet for '{dir_region_filter}' region scope.")
    else:
        search_query = st.text_input("🔍 Search Staff by Name, Employee ID, or Department:", placeholder="e.g. YEDC-1045 or Technical").strip().lower()

        filtered_staff = [
            s for s in all_staff
            if search_query in s["emp_id"].lower() or search_query in s["full_name"].lower() or search_query in s["category"].lower() or search_query in s.get("department", "").lower() or search_query in s.get("region", "").lower()
        ]

        st.caption(f"Showing {len(filtered_staff)} of {len(all_staff)} record(s)")

        for staff in filtered_staff:
            display_dept_label = "N/A" if staff.get('category') in ['Intern', 'NYSC'] else staff.get('department', 'Technical')
            display_desig_label = staff.get('category') if staff.get('category') in ['Intern', 'NYSC'] else staff.get('designation', 'Staff Member')
            with st.expander(f"🪪 **{staff['emp_id']}** - {staff['full_name']} (Role: {display_desig_label} | Dept: {display_dept_label} - {staff['category']} - {staff.get('region', 'Adamawa')})"):
                c1, c2, c3, c4 = st.columns([1, 1, 1, 1.2])

                photo_abs = BASE_DIR / staff["photo_path"]
                sig_abs = BASE_DIR / staff["signature_path"]
                qr_abs = BASE_DIR / staff["qr_path"] if staff["qr_path"] != "PENDING" else None
                pdf_abs = DIRS["generated_pdfs"] / f"{staff['emp_id']}_ID_Card.pdf"

                with c1:
                    st.markdown("**Staff Photo**")
                    if photo_abs.exists():
                        st.image(str(photo_abs), width=95)
                    else:
                        st.caption("Missing")

                with c2:
                    st.markdown("**Signature**")
                    if sig_abs.exists():
                        st.image(str(sig_abs), width=110)
                    else:
                        st.caption("Missing")

                with c3:
                    st.markdown("**QR Code**")
                    if qr_abs and qr_abs.exists():
                        st.image(str(qr_abs), width=95)
                    else:
                        st.markdown('<div class="pending-badge">PENDING</div>', unsafe_allow_html=True)

                with c4:
                    st.markdown("**Actions**")
                    
                    # 1. Download Official ID Card Request Form PDF (Fits 100% on 1 A4 Page)
                    ok_req, req_pdf_path = generate_id_request_form_pdf(staff)
                    if ok_req and os.path.exists(req_pdf_path):
                        with open(req_pdf_path, "rb") as req_f:
                            st.download_button(
                                label="📋 Download Request Form (1-Page A4 PDF)",
                                data=req_f,
                                file_name=f"{staff['emp_id']}_ID_Card_Request_Form.pdf",
                                mime="application/pdf",
                                type="primary",
                                key=f"dl_req_form_{staff['emp_id']}"
                            )

                    # 2. CR80 Printable Badge Render / Download
                    if st.button(f"🖨️ Render CR80 Badge PDF", key=f"btn_pdf_{staff['emp_id']}"):
                        if staff["qr_path"] == "PENDING":
                            st.warning("Upload QR code first for CR80 Badge.")
                        else:
                            ok, res = generate_pdf_card(staff)
                            if ok:
                                st.success("CR80 Badge Created!")
                                st.rerun()
                            else:
                                st.error(f"Error: {res}")

                    if pdf_abs.exists():
                        with open(pdf_abs, "rb") as pdf_file:
                            st.download_button(
                                label="⬇️ Download CR80 Badge PDF",
                                data=pdf_file,
                                file_name=f"{staff['emp_id']}_CR80_Badge.pdf",
                                mime="application/pdf",
                                key=f"dl_{staff['emp_id']}"
                            )

                    # 3. Edit Record Action
                    if st.button("✏️ Edit Record", key=f"btn_edit_{staff['emp_id']}"):
                        st.session_state.editing_emp_id = staff["emp_id"]
                        st.session_state.scroll_to_top = True
                        st.rerun()

                    if st.button(f"🗑️ Delete Record", key=f"del_{staff['emp_id']}"):
                        if hasattr(st, "dialog"):
                            confirm_delete_staff_modal(staff['emp_id'], staff['full_name'])
                        else:
                            st.session_state.confirm_delete_emp_id = staff["emp_id"]
                            st.session_state.confirm_delete_staff_name = staff["full_name"]
                            st.rerun()


# ==========================================
# 11. PAGE 5: REPORTS & ANALYTICS (Admins Only)
# ==========================================
elif selected_page == "📈 Reports & Analytics" and user_role in ["super_admin", "regional_admin"]:
    st.markdown("### Staff Category & Regional Reports")

    if is_super_admin:
        r_col1, r_col2 = st.columns([1, 2])
        with r_col1:
            rep_region_filter = st.selectbox(
                "Filter Report by Region:",
                ["ALL"] + REGIONS,
                key="rep_region_select"
            )
    else:
        rep_region_filter = user_region
        st.info(f"Showing regional report for **{user_region}** Region.")

    rep_records = fetch_all_records(region_filter=rep_region_filter)
    total_staff = len(rep_records)

    perm_count = sum(1 for r in rep_records if r["category"] == "Permanent")
    contract_count = sum(1 for r in rep_records if r["category"] == "Contract")
    intern_count = sum(1 for r in rep_records if r["category"] == "Intern")
    nysc_count = sum(1 for r in rep_records if r["category"] == "NYSC")
    ready_count = sum(1 for r in rep_records if r["qr_path"] != "PENDING")

    nysc_card_html = f'<img src="{nysc_logo_b64}" class="nysc-icon-img"> NYSC MEMBERS' if nysc_logo_b64 else '🎖️ NYSC MEMBERS'

    st.markdown("##### 📊 Staff Category Metric Cards")
    m1, m2, m3, m4, m5 = st.columns(5)

    with m1:
        st.markdown(f"""
        <div class="metric-card-box">
            <div class="metric-num" style="color: #0f172a;">{total_staff}</div>
            <div class="metric-lbl">TOTAL STAFF</div>
        </div>
        """, unsafe_allow_html=True)

    with m2:
        st.markdown(f"""
        <div class="metric-card-box">
            <div class="metric-num" style="color: #ff6b00;">{perm_count}</div>
            <div class="metric-lbl">PERMANENT STAFF</div>
        </div>
        """, unsafe_allow_html=True)

    with m3:
        st.markdown(f"""
        <div class="metric-card-box">
            <div class="metric-num" style="color: #2563eb;">{contract_count}</div>
            <div class="metric-lbl">CONTRACT STAFF</div>
        </div>
        """, unsafe_allow_html=True)

    with m4:
        st.markdown(f"""
        <div class="metric-card-box">
            <div class="metric-num" style="color: #7c3aed;">{intern_count}</div>
            <div class="metric-lbl">INTERN STAFF</div>
        </div>
        """, unsafe_allow_html=True)

    with m5:
        st.markdown(f"""
        <div class="metric-card-box">
            <div class="metric-num" style="color: #059669;">{nysc_count}</div>
            <div class="metric-lbl">{nysc_card_html}</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    st.markdown("##### 📈 Staff Category Breakdown")

    if total_staff > 0:
        c1, c2 = st.columns([1.2, 1])

        with c1:
            st.markdown("###### Staff Percentage Composition")
            st.write(f"👔 **Permanent:** {perm_count} ({perm_count/total_staff*100:.1f}%)")
            st.progress(perm_count / total_staff)

            st.write(f"📄 **Contract:** {contract_count} ({contract_count/total_staff*100:.1f}%)")
            st.progress(contract_count / total_staff)

            st.write(f"🎓 **Intern:** {intern_count} ({intern_count/total_staff*100:.1f}%)")
            st.progress(intern_count / total_staff)

            nysc_lbl_html = f'<img src="{nysc_logo_b64}" style="height:18px;vertical-align:middle;"> <b>NYSC:</b>' if nysc_logo_b64 else '🎖️ <b>NYSC:</b>'
            st.markdown(f"{nysc_lbl_html} {nysc_count} ({nysc_count/total_staff*100:.1f}%)", unsafe_allow_html=True)
            st.progress(nysc_count / total_staff)

        with c2:
            st.markdown("###### Regional Summary & Export")
            
            df_rep = pd.DataFrame([
                {"Category": "Permanent", "Count": perm_count, "Percentage": f"{perm_count/total_staff*100:.1f}%"},
                {"Category": "Contract", "Count": contract_count, "Percentage": f"{contract_count/total_staff*100:.1f}%"},
                {"Category": "Intern", "Count": intern_count, "Percentage": f"{intern_count/total_staff*100:.1f}%"},
                {"Category": "NYSC", "Count": nysc_count, "Percentage": f"{nysc_count/total_staff*100:.1f}%"},
                {"Category": "TOTAL", "Count": total_staff, "Percentage": "100.0%"}
            ])
            st.dataframe(df_rep, use_container_width=True)

            ex_col1, ex_col2 = st.columns(2)
            with ex_col1:
                csv_data = df_rep.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Export CSV",
                    data=csv_data,
                    file_name=f"YEDC_Staff_Report_{rep_region_filter}.csv",
                    mime="text/csv",
                    type="primary",
                    use_container_width=True
                )
            with ex_col2:
                # Dual-Engine PDF Master Report Export Button
                ok_rep, pdf_rep_path = generate_staff_master_pdf_report(rep_records, region_filter=rep_region_filter)
                if ok_rep and os.path.exists(pdf_rep_path):
                    with open(pdf_rep_path, "rb") as f_pdf_rep:
                        st.download_button(
                            label="📄 Export PDF Report",
                            data=f_pdf_rep,
                            file_name=f"YEDC_Staff_Master_Report_{rep_region_filter}.pdf",
                            mime="application/pdf",
                            type="primary",
                            use_container_width=True,
                            key="btn_dl_pdf_rep_tab_direct"
                        )
                else:
                    st.error(f"PDF Report Error: {pdf_rep_path}")
    else:
        st.info("No records available to generate report for this region scope.")


# ==========================================
# 12. PAGE 6: USER MANAGEMENT (SUPER ADMIN ONLY)
# ==========================================
elif selected_page == "👥 User Management" and is_super_admin:
    st.markdown("### System User Account Management")

    u_col1, u_col2 = st.columns([1.1, 0.9], gap="large")

    with u_col1:
        st.markdown("##### ➕ Create New User Account")
        with st.form("create_user_form", clear_on_submit=True):
            nu_username = st.text_input("Username *", placeholder="e.g. borno_assistant").strip()
            nu_fullname = st.text_input("Full Name *", placeholder="e.g. Sarah Connor").strip()
            nu_password = st.text_input("Password *", type="password", placeholder="••••••••").strip()
            
            nu_role = st.selectbox("System Role *", ["enrollment_assistant", "regional_admin", "super_admin"])
            nu_region = st.selectbox("Assigned Region *", ["ALL"] + REGIONS if nu_role == "super_admin" else REGIONS)

            user_submit = st.form_submit_button("👤 Create User Account", type="primary", use_container_width=True)

            if user_submit:
                if not nu_username or not nu_fullname or not nu_password:
                    st.error("Please fill in username, full name, and password.")
                else:
                    ok, u_msg = insert_user(
                        username=nu_username,
                        password=nu_password,
                        full_name=nu_fullname,
                        role=nu_role,
                        region=nu_region
                    )
                    if ok:
                        st.success(f"✅ {u_msg}")
                        st.rerun()
                    else:
                        st.error(f"❌ {u_msg}")

    with u_col2:
        st.markdown("##### 👥 Existing System Accounts")
        existing_users = fetch_all_users()
        
        for u in existing_users:
            role_title = u["role"].replace('_', ' ').title()
            with st.expander(f"👤 **{u['username']}** - {u['full_name']} ({role_title})"):
                st.write(f"**Username:** `{u['username']}`")
                st.write(f"**Role:** {role_title}")
                st.write(f"**Assigned Region:** {u['region']}")
                
                if u["username"] != "admin" and u["username"] != current_user["username"]:
                    if st.button(f"🗑️ Delete User Account", key=f"del_u_{u['username']}"):
                        if hasattr(st, "dialog"):
                            confirm_delete_user_modal(u['username'], u['full_name'])
                        else:
                            st.session_state.confirm_delete_username = u["username"]
                            st.rerun()
