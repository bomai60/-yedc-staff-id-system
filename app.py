import os
import io
import re
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
    if "media_status" not in columns:
        cursor.execute("ALTER TABLE staff_records ADD COLUMN media_status TEXT DEFAULT 'COMPLETE'")

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
    
    # 3. Audit Logs table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            username TEXT NOT NULL,
            role TEXT NOT NULL,
            action TEXT NOT NULL,
            target_id TEXT,
            details TEXT NOT NULL,
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

# ==========================================
# AUDIT LOGGING HELPER FUNCTIONS
# ==========================================
def log_audit_event(action: str, target_id: str = None, details: str = "", region: str = None, username: str = None, role: str = None):
    """Log user or system activity to audit_logs database table."""
    try:
        if not username or not role or not region:
            user_session = getattr(st, 'session_state', {}).get('user')
            if user_session:
                username = username or user_session.get('username', 'SYSTEM')
                role = role or user_session.get('role', 'system')
                region = region or user_session.get('region', 'ALL')
            else:
                username = username or 'SYSTEM'
                role = role or 'system'
                region = region or 'ALL'
                
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO audit_logs (timestamp, username, role, action, target_id, details, region)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (timestamp, str(username), str(role), str(action), str(target_id) if target_id else "", str(details), str(region)))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Audit log error: {e}")

def fetch_audit_logs(region_filter=None, action_filter=None, limit=500):
    conn = get_db_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM audit_logs WHERE 1=1"
    params = []
    
    if region_filter and region_filter != "ALL":
        query += " AND (region = ? OR region = 'ALL')"
        params.append(region_filter)
        
    if action_filter and action_filter != "ALL":
        query += " AND action = ?"
        params.append(action_filter)
        
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

# User Auth Functions
def authenticate_user(username, password):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?", (username.strip(),))
    row = cursor.fetchone()
    conn.close()
    if row and verify_password(password, row["password_hash"]):
        user_dict = dict(row)
        log_audit_event(
            action="USER_LOGIN",
            target_id=user_dict["username"],
            details=f"User '{user_dict['username']}' ({user_dict['role']}) logged in successfully",
            region=user_dict["region"],
            username=user_dict["username"],
            role=user_dict["role"]
        )
        return user_dict
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
        log_audit_event("CREATE_USER", username.strip(), f"Created user account '{username.strip()}' with role '{role}' in region '{region}'")
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
    log_audit_event("DELETE_USER", username, f"Deleted user account '{username}'")

# Staff Database Functions (Region-scoped)
def insert_staff_record(emp_id, full_name, category, designation, department, region, photo_path, signature_path, qr_path="PENDING", media_status="COMPLETE"):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO staff_records (emp_id, full_name, category, designation, department, region, photo_path, signature_path, qr_path, media_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (emp_id, full_name, category, designation, department, region, str(photo_path), str(signature_path), str(qr_path), str(media_status)))
        conn.commit()
        log_audit_event("CREATE_STAFF", emp_id, f"Registered staff '{full_name}' ({emp_id}) - Category: {category}, Dept: {department}, Region: {region} (Media: {media_status})", region=region)
        return True, "Record saved successfully!"
    except sqlite3.IntegrityError:
        return False, f"Employee ID '{emp_id}' already exists in database!"
    except Exception as e:
        return False, f"Database Error: {str(e)}"
    finally:
        conn.close()

def update_staff_record(emp_id, full_name, category, designation, department, region, photo_path=None, signature_path=None, media_status=None):
    """Update existing staff details, photo, and digital signature."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        query = "UPDATE staff_records SET full_name = ?, category = ?, designation = ?, department = ?, region = ?"
        params = [full_name, category, designation, department, region]
        
        if photo_path:
            query += ", photo_path = ?"
            params.append(str(photo_path))
        if signature_path:
            query += ", signature_path = ?"
            params.append(str(signature_path))
        if media_status:
            query += ", media_status = ?"
            params.append(str(media_status))
        elif photo_path or signature_path:
            cursor.execute("SELECT photo_path, signature_path FROM staff_records WHERE emp_id = ?", (emp_id,))
            r = cursor.fetchone()
            curr_p = photo_path or (r[0] if r else "")
            curr_s = signature_path or (r[1] if r else "")
            if "placeholder" not in str(curr_p) and "placeholder" not in str(curr_s):
                query += ", media_status = 'COMPLETE'"
            
        query += " WHERE emp_id = ?"
        params.append(emp_id)
        
        cursor.execute(query, tuple(params))
        conn.commit()
        log_audit_event("UPDATE_STAFF", emp_id, f"Updated staff details/media for '{full_name}' ({emp_id})", region=region)
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
    log_audit_event("DELETE_STAFF", emp_id, f"Deleted staff record '{emp_id}'")

# ==========================================
# BULK DATA IMPORT & VALIDATION ENGINE
# ==========================================
def generate_sample_csv_template():
    df = pd.DataFrame([
        {
            "emp_id": "YEDC/STAFF/001",
            "full_name": "Musa Adamu",
            "category": "Permanent",
            "designation": "Principal Engineer",
            "department": "Technical",
            "region": "Adamawa"
        },
        {
            "emp_id": "YEDC/STAFF/002",
            "full_name": "Amina Usman",
            "category": "Contract",
            "designation": "Senior Commercial Officer",
            "department": "Commercial",
            "region": "Borno"
        },
        {
            "emp_id": "YEDC/STAFF/003",
            "full_name": "John Okafor",
            "category": "Junior Staff",
            "designation": "Technician",
            "department": "Technical",
            "region": "Taraba"
        }
    ])
    return df.to_csv(index=False).encode('utf-8')

def validate_bulk_staff_df(df, user_region, user_role):
    """
    Validates uploaded bulk dataframe.
    Returns: (valid_records_list, errors_list)
    """
    required_cols = ["emp_id", "full_name", "category", "designation", "department", "region"]
    missing_cols = [c for c in required_cols if c not in df.columns]
    if missing_cols:
        return [], [{"row": "Header", "emp_id": "N/A", "error": f"Missing required columns: {', '.join(missing_cols)}"}]
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT emp_id FROM staff_records")
    existing_emp_ids = set(r[0] for r in cursor.fetchall())
    conn.close()
    
    valid_records = []
    errors = []
    seen_file_ids = set()
    
    allowed_categories = ["Permanent", "Contract", "Intern", "NYSC"]
    
    for idx, row in df.iterrows():
        row_num = idx + 2  # 1-based header offset
        emp_id = str(row.get("emp_id", "")).strip()
        full_name = str(row.get("full_name", "")).strip()
        category = str(row.get("category", "")).strip()
        designation = str(row.get("designation", "Staff Member")).strip() or "Staff Member"
        department = str(row.get("department", "Technical")).strip() or "Technical"
        region = str(row.get("region", "Adamawa")).strip()
        
        row_errors = []
        if not emp_id or emp_id == "nan":
            row_errors.append("Employee ID is required")
        elif emp_id in existing_emp_ids:
            row_errors.append(f"Employee ID '{emp_id}' is already saved in database (Already Imported)")
        elif emp_id in seen_file_ids:
            row_errors.append(f"Duplicate Employee ID '{emp_id}' within file")
        else:
            seen_file_ids.add(emp_id)
            
        if not full_name or full_name == "nan":
            row_errors.append("Full Name is required")
            
        if region not in REGIONS:
            row_errors.append(f"Invalid Region '{region}'. Must be one of: {', '.join(REGIONS)}")
        elif user_role != "super_admin" and region != user_region:
            row_errors.append(f"Region '{region}' is outside your assigned region scope ({user_region})")
            
        matched_cat = next((c for c in allowed_categories if c.lower() == category.lower()), None)
        if matched_cat:
            category = matched_cat
        else:
            row_errors.append(f"Invalid Category '{category}'. Allowed: {', '.join(allowed_categories)}")

        if row_errors:
            for err in row_errors:
                errors.append({"row": f"Row {row_num}", "emp_id": emp_id if emp_id != 'nan' else 'N/A', "error": err})
        else:
            valid_records.append({
                "emp_id": emp_id,
                "full_name": full_name,
                "category": category,
                "designation": designation,
                "department": department,
                "region": region
            })
            
    return valid_records, errors

def process_bulk_staff_import(valid_records):
    """
    Inserts valid bulk staff records, auto-generates QR codes, and logs audit event.
    Returns: (success_count, fail_count, error_messages)
    """
    success_count = 0
    fail_count = 0
    err_msgs = []
    
    placeholder_photo = DIRS["photos"] / "placeholder.png"
    placeholder_sig = DIRS["signatures"] / "placeholder.png"
    
    if not placeholder_photo.exists():
        img = Image.new('RGB', (300, 375), color=(220, 225, 230))
        img.save(placeholder_photo)
    if not placeholder_sig.exists():
        img = Image.new('RGB', (300, 100), color=(240, 240, 240))
        img.save(placeholder_sig)
        
    for rec in valid_records:
        emp_id = rec["emp_id"]
        full_name = rec["full_name"]
        category = rec["category"]
        designation = rec["designation"]
        department = rec["department"]
        region = rec["region"]
        
        photo_rel = "photos/placeholder.png"
        sig_rel = "signatures/placeholder.png"
            
        qr_p = auto_generate_staff_qr(emp_id, full_name, category, region)
        
        ok, msg = insert_staff_record(
            emp_id=emp_id,
            full_name=full_name,
            category=category,
            designation=designation,
            department=department,
            region=region,
            photo_path=photo_rel,
            signature_path=sig_rel,
            qr_path=qr_p,
            media_status="PENDING_MEDIA"
        )
        if ok:
            success_count += 1
        else:
            fail_count += 1
            err_msgs.append(f"{emp_id}: {msg}")
            
    if success_count > 0:
        log_audit_event("BULK_IMPORT", f"BATCH_{success_count}", f"Successfully imported {success_count} staff records via Bulk Import (CSV/Excel)", region="ALL")
        
    return success_count, fail_count, err_msgs

# ==========================================
# 3. HELPER UTILITIES & IMAGE / DUAL PDF RENDERER (WKHTMLTOPDF + XHTML2PDF FALLBACK)
# ==========================================
def validate_white_background(photo_input, min_rgb=200, max_diff=35, required_ratio=0.70):
    """
    Validates whether an input photo has a plain white background.
    Samples top-left and top-right corners of the photo.
    Returns (bool, str).
    """
    try:
        if isinstance(photo_input, Image.Image):
            img = photo_input.copy()
        else:
            img = Image.open(photo_input)
            
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
            
        if img.mode != "RGB":
            img = img.convert("RGB")
            
        arr = np.array(img)
        h, w, _ = arr.shape
        
        # Sample top-left and top-right corner patches (top 15% height, left/right 15% width)
        h_crop = max(1, int(h * 0.15))
        w_crop = max(1, int(w * 0.15))
        
        top_left = arr[0:h_crop, 0:w_crop]
        top_right = arr[0:h_crop, w-w_crop:w]
        
        corner_pixels = np.vstack((top_left.reshape(-1, 3), top_right.reshape(-1, 3)))
        
        r, g, b = corner_pixels[:, 0], corner_pixels[:, 1], corner_pixels[:, 2]
        is_bright = (r >= min_rgb) & (g >= min_rgb) & (b >= min_rgb)
        color_range = np.maximum(r, np.maximum(g, b)) - np.minimum(r, np.minimum(g, b))
        is_neutral = color_range <= max_diff
        
        white_pixels = is_bright & is_neutral
        white_ratio = np.mean(white_pixels)
        
        if white_ratio >= required_ratio:
            return True, f"White background verified ({white_ratio*100:.1f}% white detected)."
        else:
            avg_r = int(np.mean(r)) if len(r) > 0 else 0
            avg_g = int(np.mean(g)) if len(g) > 0 else 0
            avg_b = int(np.mean(b)) if len(b) > 0 else 0
            
            diagnostic_items = [
                f"1. **Insufficient Background Whiteness**: Detected **{white_ratio*100:.1f}%** white area in top corner background patches (Minimum required: **{required_ratio*100:.0f}%**).",
                f"2. **Measured Light Intensity**: Sampled corner RGB brightness level is **({avg_r}, {avg_g}, {avg_b})** — Minimum brightness required is **({min_rgb}, {min_rgb}, {min_rgb})**.",
                "3. **Recommended Fix**: Stand the staff member directly in front of a well-lit, plain white wall or white backdrop before snapping the photo with your mobile camera."
            ]
            return False, "\n\n".join(diagnostic_items)
    except Exception as e:
        return False, f"Could not analyze image background: {str(e)}"

def process_and_optimize_photo(photo_file):
    raw_image = Image.open(photo_file)
    try:
        raw_image = ImageOps.exif_transpose(raw_image)
    except Exception:
        pass

    if raw_image.mode != "RGB":
        raw_image = raw_image.convert("RGB")

    # Ultra High-Definition 1 MB Target Photo Resolution (1600x2000)
    optimized_image = ImageOps.fit(
        raw_image, 
        (1600, 2000), 
        centering=(0.5, 0.5), 
        method=Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS
    )
    return optimized_image

def auto_generate_staff_qr(emp_id, full_name, category, region):
    """Automatically generate a scannable dummy QR code for newly registered staff records."""
    try:
        import qrcode
        qr_data = f"YEDC OFFICIAL ID VERIFICATION\nID: {emp_id}\nName: {full_name}\nCategory: {category}\nRegion: {region}\nStatus: VERIFIED ACTIVE"
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
        qr.add_data(qr_data)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="#0F172A", back_color="#FFFFFF")
    except Exception:
        qr_img = Image.new("RGB", (200, 200), color=(15, 23, 42))

    safe_emp_id = re.sub(r'[^a-zA-Z0-9_-]', '_', emp_id)
    qr_filename = f"{safe_emp_id}_qr.png"
    qr_rel_path = f"qr_codes/{qr_filename}"
    qr_abs_path = DIRS["qr_codes"] / qr_filename
    qr_img.save(qr_abs_path)
    return qr_rel_path

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

CONTRACT_TEMPLATE_PATH = BASE_DIR / "templates" / "contract_template.html"
PERMANENT_TEMPLATE_PATH = BASE_DIR / "templates" / "permanent_template.html"
AUTH_SIG_PATH = DIRS["assets"] / "authorised_signature.png"

def generate_pdf_card(record):
    rec_cat = record.get("category", "")
    
    if rec_cat == "Contract" and CONTRACT_TEMPLATE_PATH.exists():
        t_path = CONTRACT_TEMPLATE_PATH
    elif rec_cat == "Permanent" and PERMANENT_TEMPLATE_PATH.exists():
        t_path = PERMANENT_TEMPLATE_PATH
    elif PERMANENT_TEMPLATE_PATH.exists():
        t_path = PERMANENT_TEMPLATE_PATH
    else:
        t_path = TEMPLATE_PATH

    if not t_path.exists():
        return False, f"Template file not found at {t_path}"

    with open(t_path, "r", encoding="utf-8") as f:
        template_content = f.read()

    template = jinja2.Template(template_content)

    logo_uri = get_asset_b64(LOGO_PATH)
    nysc_logo_uri = get_asset_b64(NYSC_LOGO_PATH)
    auth_sig_uri = get_asset_b64(AUTH_SIG_PATH)

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
        "auth_sig_path": auth_sig_uri,
        "photo_path": get_asset_b64(record["photo_path"]),
        "signature_path": get_asset_b64(record["signature_path"]),
        "qr_path": get_asset_b64(record["qr_path"])
    }

    rendered_html = template.render(context)
    output_pdf_path = DIRS["generated_pdfs"] / f"{record['emp_id']}_ID_Card.pdf"
    res_ok, res_path = convert_html_to_pdf(rendered_html, output_pdf_path, is_card=True)
    if res_ok:
        log_audit_event("EXPORT_PDF_CARD", record["emp_id"], f"Generated CR80 Plastic ID Card PDF for {record['full_name']} ({record['emp_id']})", region=record.get("region", "ALL"))
    return res_ok, res_path

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
    clean_name = re.sub(r'[^a-zA-Z0-9_-]', '_', record['full_name'])
    clean_id = re.sub(r'[^a-zA-Z0-9_-]', '_', record['emp_id'])
    output_pdf_path = DIRS["generated_pdfs"] / f"{clean_id}_{clean_name}_Request_Form.pdf"
    res_ok, res_path = convert_html_to_pdf(rendered_html, output_pdf_path, is_card=False)
    if res_ok:
        log_audit_event("EXPORT_REQUEST_FORM", record["emp_id"], f"Generated ID Card Request Form PDF for {record['full_name']} ({record['emp_id']})", region=record.get("region", "ALL"))
    return res_ok, res_path

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
    res_ok, res_path = convert_html_to_pdf(rendered_html, output_pdf_path, is_card=False)
    if res_ok:
        log_audit_event("EXPORT_MASTER_REPORT", f"MASTER_{region_filter}", f"Generated Master Staff Directory PDF report for region filter '{region_filter}' (Total: {len(records)})", region=region_filter)
    return res_ok, res_path

def image_to_base64(img_path):
    try:
        if not os.path.exists(img_path):
            return ""
        with open(img_path, "rb") as image_file:
            return f"data:image/png;base64,{base64.b64encode(image_file.read()).decode()}"
    except Exception:
        return ""

def create_pdf_zip_archive(records=None):
    if records is None:
        records = fetch_all_records()
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for r in records:
            ok, pdf_path = generate_id_request_form_pdf(r)
            if ok and os.path.exists(pdf_path):
                zip_file.write(pdf_path, arcname=os.path.basename(pdf_path))
    zip_buffer.seek(0)
    log_audit_event("EXPORT_BATCH_ZIP", f"BATCH_ZIP_{len(records)}", f"Generated Batch ZIP Archive containing {len(records)} staff request forms", region="ALL")
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
if st.session_state.get("authenticated") and st.session_state.get("user"):
    current_user = st.session_state.user
    user_role = current_user["role"]
    user_region = current_user["region"]
    is_super_admin = (user_role == "super_admin")
    is_enrollment_assistant = (user_role == "enrollment_assistant")
else:
    current_user = {"username": "system", "full_name": "System", "role": "super_admin", "region": "ALL"}
    user_role = "super_admin"
    user_region = "ALL"
    is_super_admin = True
    is_enrollment_assistant = False

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

# Security Enforcement for Enrollment Assistants (Dashboard, Staff Register & Batch Processing)
if is_enrollment_assistant and st.session_state.current_page not in ["📊 Dashboard", "📝 Staff Register", "⚙️ Batch Processing"]:
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
    nav_item("⚙️ Batch Processing", "btn_nav_batch")
    
    # Restricted from Enrollment Assistants (Admins only)
    if user_role in ["super_admin", "regional_admin"]:
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
records_pending_media = [
    r for r in records_all
    if r.get("media_status") == "PENDING_MEDIA" or "placeholder.png" in r.get("photo_path", "") or "placeholder.png" in r.get("signature_path", "")
]
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
            <div class="metric-num" style="color: #d97706;">{len(records_pending_media)}</div>
            <div class="metric-lbl">AWAITING PHOTO / SIG</div>
        </div>
        """, unsafe_allow_html=True)

    with m4:
        st.markdown(f"""
        <div class="metric-card-box">
            <div class="metric-num" style="color: #059669;">{generated_pdfs_count}</div>
            <div class="metric-lbl">PDF CARDS GENERATED</div>
        </div>
        """, unsafe_allow_html=True)

    if records_pending_media:
        st.markdown("<br>", unsafe_allow_html=True)
        d_col1, d_col2 = st.columns([3, 1])
        with d_col1:
            st.warning(f"⚠️ **{len(records_pending_media)} Staff Member(s)** imported via CSV are currently awaiting Photo & Signature capture!")
        with d_col2:
            st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)
            if st.button("📷 Open Pending Queue", type="primary", use_container_width=True, key="btn_dash_open_pending_queue"):
                st.session_state.current_page = "🔍 Staff Directory"
                st.session_state.dir_status_filter_select = "⚠️ Awaiting Photo & Signature (Pending Queue)"
                st.rerun()

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
    st.markdown("### Staff Enrollment & Registration")
    
    tab_single, tab_bulk = st.tabs(["✍️ Single Staff Enrollment", "📥 Bulk Data Import (CSV / Excel)"])

    with tab_single:
        col_form, col_prev = st.columns([1.2, 0.8], gap="large")

        f_cnt = st.session_state.form_counter

        with col_form:
            # Category Selection outside/top of form for 100% reactive Department & Role behavior
            reg_cat_key = f"inp_cat_{f_cnt}"
            category = st.selectbox("Staff Category *", ["Permanent", "Contract", "Intern", "NYSC"], key=reg_cat_key)

            with st.form(f"staff_capture_form_{f_cnt}", clear_on_submit=False):
                f_col1, f_col2 = st.columns(2)
                with f_col1:
                    if category == "NYSC":
                        emp_id = st.text_input("State Code / Call-Up No *", placeholder="e.g. AD/23B/1045", key=f"inp_emp_{f_cnt}").strip()
                    elif category == "Intern":
                        emp_id = st.text_input("Intern / Student ID *", placeholder="e.g. INT-2026-045", key=f"inp_emp_{f_cnt}").strip()
                    else:
                        emp_id = st.text_input("Employee Staff ID *", placeholder="e.g. YEDC-1045", key=f"inp_emp_{f_cnt}").strip()

                    full_name = st.text_input("Full Name *", placeholder="e.g. John Doe", key=f"inp_name_{f_cnt}").strip()
                    
                with f_col2:
                    if category == "Intern":
                        st.text_input("Role / Designation", value="Intern", disabled=True, key=f"inp_desig_dis_{f_cnt}")
                        dept_input = st.selectbox("Assigned Department / Unit *", DEPARTMENTS, key=f"inp_dept_{f_cnt}")
                        designation = "Intern"
                    elif category == "NYSC":
                        st.text_input("Role / Designation", value="NYSC Corper", disabled=True, key=f"inp_desig_dis_{f_cnt}")
                        dept_input = st.selectbox("PPA Department / Unit *", DEPARTMENTS, key=f"inp_dept_{f_cnt}")
                        designation = "NYSC Corper"
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

                st.markdown("##### 📷 Staff Photo Capture")
                st.caption("Click below to select a photo file or open your device's native camera window:")
                st.caption("⚠️ **Requirement:** Only photos with a **plain white background** are accepted. Non-white backgrounds will be rejected.")

                photo_file = st.file_uploader("📷 Select / Capture Staff Photo (JPG, PNG, HEIC) *", type=["jpg", "jpeg", "png", "heic", "heif"], key=f"uploader_photo_{f_cnt}")

                if photo_file is not None:
                    is_bg_valid, bg_msg = validate_white_background(photo_file)
                    if not is_bg_valid:
                        st.error(f"❌ Photo Rejected: {bg_msg}")
                    else:
                        st.caption(f"✓ Photo loaded ({photo_file.size / 1024:.1f} KB) - White Background Verified")

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

            photo_render = f'<img src="{preview_photo_b64}" style="width: 100%; height: 100%; object-fit: cover;">' if preview_photo_b64 else '<div style="color:#94a3b8;font-size:11px;font-weight:bold;">[ Photo ]</div>'
            sig_render = f'<img src="{preview_sig_b64}" style="max-height:22px;max-width:55px;vertical-align:middle;">' if preview_sig_b64 else '<span style="font-size:8px;color:#94a3b8;">[ Signature ]</span>'

            if disp_cat == "Contract":
                st.markdown(f"""
                <div style="width: 240px; height: 380px; background: #ffffff; border-radius: 14px; border: 2px solid #0c1a40; box-shadow: 0 12px 30px rgba(0,0,0,0.12); overflow: hidden; margin: 0 auto; position: relative; font-family: 'Plus Jakarta Sans', Arial, sans-serif;">
                    <!-- Top Red & Orange Stripes -->
                    <div style="position: absolute; top: 0; right: 0; width: 140px; height: 16px;">
                        <div style="position: absolute; top: 0; right: 0; width: 95px; height: 8px; background: #e52e04;"></div>
                        <div style="position: absolute; top: 9px; right: 0; width: 65px; height: 5px; background: #f96302;"></div>
                    </div>
                    <!-- Header Logo & Title -->
                    <div style="padding: 14px 14px 4px 14px; display: flex; align-items: center; gap: 8px;">
                        <img src="{logo_b64}" style="height: 28px;">
                        <div>
                            <div style="font-size: 8.5px; font-weight: 800; color: #0c1a40; letter-spacing: 0.3px; line-height: 1.1;">YOLA ELECTRICITY</div>
                            <div style="font-size: 7.5px; font-weight: 700; color: #e52e04; letter-spacing: 0.2px;">DISTRIBUTION CO.</div>
                        </div>
                    </div>
                    <!-- Category Header Banner -->
                    <div style="background: #e52e04; color: #ffffff; font-size: 9px; font-weight: 800; text-align: center; padding: 3px 0; text-transform: uppercase; letter-spacing: 1px;">CONTRACT STAFF</div>
                    <!-- Photo Box -->
                    <div style="margin: 10px auto 6px auto; width: 95px; height: 118px; border: 2px solid #0c1a40; border-radius: 6px; overflow: hidden; background: #f8fafc; display: flex; align-items: center; justify-content: center;">
                        {photo_render}
                    </div>
                    <!-- Staff Details -->
                    <div style="text-align: center; padding: 0 10px;">
                        <div style="font-size: 11px; font-weight: 800; color: #0c1a40; margin-bottom: 2px; text-transform: uppercase; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{disp_name}</div>
                        <div style="font-size: 9px; font-weight: 700; color: #e52e04; margin-bottom: 4px;">{disp_id}</div>
                        <div style="font-size: 8.5px; font-weight: 600; color: #475569; margin-bottom: 1px;">Role: {disp_desig}</div>
                        <div style="font-size: 8.5px; font-weight: 600; color: #475569; margin-bottom: 1px;">Dept: {disp_dept}</div>
                        <div style="font-size: 8.5px; font-weight: 700; color: #0c1a40;">Region: {disp_reg}</div>
                    </div>
                    <!-- Bottom Decorative Footer Curves -->
                    <div style="position: absolute; bottom: 0; left: 0; width: 85px; height: 38px; background: linear-gradient(135deg, #ff5522, #f03a17); border-top-right-radius: 38px; z-index: 1;"></div>
                    <div style="position: absolute; bottom: 0; right: 0; width: 95px; height: 44px; background: #0c1a40; border-top-left-radius: 44px; z-index: 1;"></div>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div style="width: 240px; height: 380px; background: #ffffff; border-radius: 14px; border: 2px solid #0c1a40; box-shadow: 0 12px 30px rgba(0,0,0,0.12); overflow: hidden; margin: 0 auto; position: relative; font-family: 'Plus Jakarta Sans', Arial, sans-serif;">
                    <!-- Header Logo & Title -->
                    <div style="padding: 14px 14px 4px 14px; display: flex; align-items: center; gap: 8px;">
                        <img src="{logo_b64}" style="height: 28px;">
                        <div>
                            <div style="font-size: 8.5px; font-weight: 800; color: #0c1a40; letter-spacing: 0.3px; line-height: 1.1;">YOLA ELECTRICITY</div>
                            <div style="font-size: 7.5px; font-weight: 700; color: #ff6b00; letter-spacing: 0.2px;">DISTRIBUTION CO.</div>
                        </div>
                    </div>
                    <!-- Category Header Banner -->
                    <div style="background: #0c1a40; color: #ffffff; font-size: 9px; font-weight: 800; text-align: center; padding: 3px 0; text-transform: uppercase; letter-spacing: 1px;">{disp_cat} IDENTITY CARD</div>
                    <!-- Photo Box -->
                    <div style="margin: 10px auto 6px auto; width: 95px; height: 118px; border: 2px solid #ff6b00; border-radius: 6px; overflow: hidden; background: #f8fafc; display: flex; align-items: center; justify-content: center;">
                        {photo_render}
                    </div>
                    <!-- Staff Details -->
                    <div style="text-align: center; padding: 0 10px;">
                        <div style="font-size: 11px; font-weight: 800; color: #0c1a40; margin-bottom: 2px; text-transform: uppercase; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{disp_name}</div>
                        <div style="font-size: 9px; font-weight: 700; color: #ff6b00; margin-bottom: 4px;">{disp_id}</div>
                        <div style="font-size: 8.5px; font-weight: 600; color: #475569; margin-bottom: 1px;">Role: {disp_desig}</div>
                        <div style="font-size: 8.5px; font-weight: 600; color: #475569; margin-bottom: 1px;">Dept: {disp_dept}</div>
                        <div style="font-size: 8.5px; font-weight: 700; color: #0c1a40;">Region: {disp_reg}</div>
                    </div>
                    <!-- Bottom Decorative Footer Curves -->
                    <div style="position: absolute; bottom: 0; left: 0; width: 85px; height: 38px; background: linear-gradient(135deg, #ff6b00, #ea580c); border-top-right-radius: 38px; z-index: 1;"></div>
                    <div style="position: absolute; bottom: 0; right: 0; width: 95px; height: 44px; background: #0c1a40; border-top-left-radius: 44px; z-index: 1;"></div>
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
                    is_bg_valid, bg_msg = validate_white_background(photo_file)
                    if not is_bg_valid:
                        st.error(f"❌ Registration Rejected: {bg_msg}")
                    else:
                        processed_photo = process_and_optimize_photo(photo_file)

                        photo_filename = f"{emp_id}_photo.jpg"
                        photo_rel_path = f"photos/{photo_filename}"
                        photo_abs_path = DIRS["photos"] / photo_filename
                        processed_photo.save(photo_abs_path, format="JPEG", quality=99, optimize=True)

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
                        processed_photo.save(buf_photo, format="JPEG", quality=99)
                        photo_b64_str = f"data:image/jpeg;base64,{base64.b64encode(buf_photo.getvalue()).decode()}"

                        buf_sig = io.BytesIO()
                        sig_image.save(buf_sig, format="PNG")
                        sig_b64_str = f"data:image/png;base64,{base64.b64encode(buf_sig.getvalue()).decode()}"

                        qr_rel_path = "NOT_REQUIRED"

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
                            # Pre-generate official A4 ID Card Request Form PDF immediately
                            new_record = {
                                "emp_id": emp_id,
                                "full_name": full_name,
                                "category": category,
                                "designation": final_desig,
                                "department": final_dept,
                                "region": staff_region,
                                "photo_path": photo_rel_path,
                                "signature_path": sig_rel_path,
                                "qr_path": qr_rel_path
                            }
                            generate_id_request_form_pdf(new_record)

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

    with tab_bulk:
        st.markdown("#### 📥 Bulk Staff Enrollment via CSV / Excel")
        st.caption("Upload a spreadsheet containing staff records to batch import multiple employee profiles at once.")
        
        if st.session_state.get("bulk_import_success_msg"):
            st.success(st.session_state.pop("bulk_import_success_msg"))
            st.info("💡 **Tip:** The imported staff records are now saved in the database! Go to **🔍 Staff Directory** to view them or use the **⚠️ Pending Photo & Signature Queue** banner to attach photos and signatures.")

        b_col1, b_col2 = st.columns([1, 1], gap="medium")
        with b_col1:
            st.markdown("##### 📄 Step 1: Download Sample CSV Template")
            st.caption("Ensure your spreadsheet file matches the required column headers:")
            st.download_button(
                label="📥 Download Sample CSV Template",
                data=generate_sample_csv_template(),
                file_name="yedc_staff_import_template.csv",
                mime="text/csv",
                use_container_width=True
            )
            
        with b_col2:
            st.markdown("##### 📤 Step 2: Upload Completed Spreadsheet")
            uploaded_bulk_file = st.file_uploader(
                "Upload CSV or Excel file (.csv, .xlsx, .xls)",
                type=["csv", "xlsx", "xls"],
                key="bulk_staff_uploader"
            )
            
        if uploaded_bulk_file is not None:
            try:
                if uploaded_bulk_file.name.endswith(".csv"):
                    bulk_df = pd.read_csv(uploaded_bulk_file)
                else:
                    bulk_df = pd.read_excel(uploaded_bulk_file)
                    
                st.markdown("---")
                st.markdown("##### 🔍 Pre-Import Validation & Audit Summary")
                
                valid_recs, validation_errors = validate_bulk_staff_df(bulk_df, user_region=user_region, user_role=user_role)
                
                v_m1, v_m2, v_m3 = st.columns(3)
                with v_m1:
                    st.metric("Total Rows in File", len(bulk_df))
                with v_m2:
                    st.metric("Ready to Import (Valid)", len(valid_recs))
                with v_m3:
                    st.metric("Errors Detected", len(validation_errors))
                    
                if validation_errors:
                    st.error(f"⚠️ Found {len(validation_errors)} error(s) in uploaded file. Please review the details below:")
                    st.dataframe(pd.DataFrame(validation_errors), use_container_width=True)
                    
                if valid_recs:
                    st.success(f"✓ Found {len(valid_recs)} valid staff record(s) ready for database insertion.")
                    st.markdown("###### Preview of Valid Records to Import:")
                    st.dataframe(pd.DataFrame(valid_recs), use_container_width=True)
                    
                    if st.button(f"🚀 Import {len(valid_recs)} Valid Staff Record(s)", type="primary", use_container_width=True, key="btn_run_bulk_import"):
                        with st.spinner("Processing bulk staff records & generating QR codes..."):
                            s_cnt, f_cnt_err, err_list = process_bulk_staff_import(valid_recs)
                        if s_cnt > 0:
                            st.session_state.bulk_import_success_msg = f"🎉 Successfully imported {s_cnt} staff record(s) into the database with auto-generated QR codes!"
                            if err_list:
                                st.warning(f"⚠️ {f_cnt_err} record(s) failed insertion: " + ", ".join(err_list))
                            st.balloons()
                            st.rerun()
                        else:
                            st.error(f"❌ Bulk import failed: {', '.join(err_list)}")
            except Exception as ex:
                st.error(f"❌ Error reading file: {str(ex)}")

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
# 9. PAGE 3: BATCH PROCESSING
# ==========================================
elif selected_page == "⚙️ Batch Processing":
    if is_super_admin:
        b_col1, b_col2 = st.columns([2.5, 1])
        with b_col1:
            selected_batch_regions = st.multiselect(
                "Filter Batch by Region(s) (Select 1, 2, 3, or all 4):",
                REGIONS,
                default=st.session_state.get("batch_selected_regions", REGIONS),
                key="batch_region_multiselect_input"
            )
            if not selected_batch_regions:
                selected_batch_regions = REGIONS
                st.caption("⚠️ No region selected — defaulting to ALL regions.")
            st.session_state.batch_selected_regions = selected_batch_regions
            
        region_header_str = ", ".join(selected_batch_regions) if len(selected_batch_regions) < len(REGIONS) else "ALL REGIONS"
        st.markdown(f"### Batch PDF Request Forms Generation ({region_header_str})")
        all_records = fetch_all_records("ALL")
        ready_records = [r for r in all_records if r.get("region", "Adamawa") in selected_batch_regions]
    else:
        selected_batch_regions = [user_region]
        st.markdown(f"### Batch PDF Request Forms Generation ({user_region} Region)")
        st.info(f"📍 Showing batch records strictly scoped for **{user_region}** Region.")
        ready_records = fetch_all_records(region_filter=user_region)

    st.markdown("##### 📄 ID Card Request Forms Batch Processing")

    if ready_records:
        st.markdown("##### 🎯 Select Staff Members for Batch Export")
        st.caption("Check or uncheck individual checkboxes directly in the table below to select which staff members to include:")
        
        btn_s1, btn_s2, _ = st.columns([1, 1, 3])
        with btn_s1:
            if st.button("☑️ Select All Staff", key="btn_batch_select_all", use_container_width=True):
                st.session_state.batch_select_all_flag = True
                st.session_state.batch_deselect_all_flag = False
                st.rerun()
        with btn_s2:
            if st.button("☒ Deselect All", key="btn_batch_deselect_all", use_container_width=True):
                st.session_state.batch_deselect_all_flag = True
                st.session_state.batch_select_all_flag = False
                st.rerun()

        all_emp_ids = [r["emp_id"] for r in ready_records]

        # Handle Select All / Deselect All overrides
        if st.session_state.get("batch_select_all_flag"):
            st.session_state.selected_batch_staff_ids = all_emp_ids
            st.session_state.batch_select_all_flag = False
        elif st.session_state.get("batch_deselect_all_flag"):
            st.session_state.selected_batch_staff_ids = []
            st.session_state.batch_deselect_all_flag = False

        if "selected_batch_staff_ids" not in st.session_state:
            st.session_state.selected_batch_staff_ids = all_emp_ids

        currently_selected = set(st.session_state.get("selected_batch_staff_ids", all_emp_ids))

        df_batch_data = pd.DataFrame([
            {
                "Select": r["emp_id"] in currently_selected,
                "Staff ID / Code": r["emp_id"],
                "Full Name": r["full_name"],
                "Department": "N/A" if r.get("category") in ["Intern", "NYSC"] else r.get("department", "Technical"),
                "Role / Designation": r["category"] if r.get("category") in ["Intern", "NYSC"] else r.get("designation", "Staff"),
                "Category": r["category"],
                "Region": r.get("region", "Adamawa"),
                "Status": "Ready for Request Form PDF"
            }
            for r in ready_records
        ])

        edited_batch_df = st.data_editor(
            df_batch_data,
            column_config={
                "Select": st.column_config.CheckboxColumn(
                    "Select",
                    help="Check to include this staff member in PDF batch export",
                    default=True
                )
            },
            disabled=["Staff ID / Code", "Full Name", "Department", "Role / Designation", "Category", "Region", "Status"],
            hide_index=True,
            use_container_width=True,
            key="batch_interactive_data_editor"
        )

        selected_rows = edited_batch_df[edited_batch_df["Select"] == True]
        selected_emp_ids = set(selected_rows["Staff ID / Code"])
        st.session_state.selected_batch_staff_ids = list(selected_emp_ids)

        records_to_process = [r for r in ready_records if r["emp_id"] in selected_emp_ids]

        st.caption(f"✓ **{len(records_to_process)}** of **{len(ready_records)}** staff member(s) selected for PDF batch processing")

        if records_to_process:
            p_col1, p_col2 = st.columns([1, 1])
            with p_col1:
                if st.button(f"📄 Generate Selected Request Form PDFs ({len(records_to_process)})", type="primary", use_container_width=True):
                    pdf_success_count = 0
                    pdf_errors = []

                    with st.spinner("Generating official ID Request Form PDFs..."):
                        for record in records_to_process:
                            ok, res = generate_id_request_form_pdf(record)
                            if ok:
                                pdf_success_count += 1
                            else:
                                pdf_errors.append((record["emp_id"], res))

                    if pdf_success_count > 0:
                        st.success(f"Successfully generated {pdf_success_count} Request Form PDF(s) in `generated_pdfs/`!")

                    if pdf_errors:
                        st.error("Errors during PDF generation:")
                        for eid, err in pdf_errors:
                            st.write(f"- **{eid}**: {err}")

            with p_col2:
                zip_buf = create_pdf_zip_archive(records_to_process)
                if zip_buf:
                    zip_filename = f"YEDC_Staff_ID_Request_Forms_Selected_{len(records_to_process)}.zip"
                    st.download_button(
                        label=f"📦 Download Selected Request Forms ZIP ({len(records_to_process)} Files)",
                        data=zip_buf,
                        file_name=zip_filename,
                        mime="application/zip",
                        use_container_width=True
                    )
        else:
            st.warning("⚠️ No staff members selected. Please check at least one staff member checkbox in the table above to generate or download PDFs.")
    else:
        st.info("No staff records found for PDF generation in the selected region scope.")


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
    pending_media_staff = [
        s for s in all_staff 
        if s.get("media_status") == "PENDING_MEDIA" or "placeholder.png" in s.get("photo_path", "") or "placeholder.png" in s.get("signature_path", "")
    ]

    # PROMINENT PENDING CAPTURE QUEUE BANNER
    if pending_media_staff:
        st.warning(f"⚠️ **{len(pending_media_staff)} Staff Member(s)** imported via CSV are currently awaiting Photo & Signature capture!")
        p_col1, p_col2 = st.columns([2, 1])
        with p_col1:
            pending_select_id = st.selectbox(
                "Select Pending Staff Member to Complete Enrollment:",
                options=[s["emp_id"] for s in pending_media_staff],
                format_func=lambda x: f"🪪 {x} - {next((s['full_name'] for s in pending_media_staff if s['emp_id']==x), '')} ({next((s['department'] for s in pending_media_staff if s['emp_id']==x), '')})",
                key="pending_queue_selectbox"
            )
        with p_col2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("📷 Capture Photo & Signature", type="primary", use_container_width=True, key="btn_open_pending_capture"):
                st.session_state.editing_emp_id = pending_select_id
                st.session_state.active_form_mode = "capture"
                st.rerun()

    # PROMINENT AUTO-OPENING EDIT & CAPTURE FORM AT TOP OF DIRECTORY
    editing_id = st.session_state.get("editing_emp_id")
    active_mode = st.session_state.get("active_form_mode", "capture")

    if editing_id:
        target_staff = next((s for s in all_staff if s["emp_id"] == editing_id), None)
        if not target_staff:
            target_staff = next((s for s in fetch_all_records("ALL") if s["emp_id"] == editing_id), None)

        if target_staff:
            st.markdown('<div class="top-edit-form-card">', unsafe_allow_html=True)
            
            t_hdr1, t_hdr2 = st.columns([3, 1])
            with t_hdr1:
                if active_mode == "capture":
                    st.markdown("#### 📱 Fast Mobile Photo & Signature Capture")
                    st.markdown(f"""
                    <div style="background:#0f172a; color:white; padding:12px 16px; border-radius:10px; border-left:5px solid #ff6b00; margin:10px 0;">
                        <span style="font-size:16px; font-weight:700; color:#ff6b00;">🪪 {target_staff['full_name']}</span> &nbsp;|&nbsp; <code>{target_staff['emp_id']}</code><br>
                        <span style="font-size:12px; color:#cbd5e1;">Role: <b>{target_staff.get('designation', 'Staff')}</b> | Dept: <b>{target_staff.get('department', 'Technical')}</b> | Region: <b>{target_staff.get('region', 'Adamawa')}</b> ({target_staff['category']})</span>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.markdown(f"#### ✏️ Full Details Edit & Media Update: **{target_staff['emp_id']}**")
                    st.caption("Update staff text details, photo, or digital signature.")
                    
            with t_hdr2:
                if st.button("❌ Close Form", key="btn_close_top_edit", use_container_width=True):
                    st.session_state.editing_emp_id = None
                    st.rerun()

            if active_mode == "capture":
                # ==========================================
                # FAST MOBILE MEDIA CAPTURE VIEW (NO TEXT FIELD CLUTTER)
                # ==========================================
                st.markdown("###### 📷 1. Staff Photo Capture (Device Camera Preview)")
                st.markdown("""
                <div style="background:#eff6ff; border:1.5px solid #3b82f6; border-radius:10px; padding:12px; font-size:13px; color:#1e40af; margin-bottom:12px;">
                    📱 <b>Native Device Camera Preview Mode:</b> Tapping the uploader button below on your mobile device automatically launches your smartphone's <b>Native Camera App</b> (with full Flash, Autofocus, HDR, and Zoom controls).
                </div>
                """, unsafe_allow_html=True)
                
                selected_photo = st.file_uploader(
                    "📸 Open Device Camera Preview / Select Staff Photo (JPG/PNG/HEIC)",
                    type=["jpg", "jpeg", "png", "heic", "heif"],
                    key=f"fast_device_photo_{target_staff['emp_id']}"
                )

                st.markdown("###### ✍️ 2. Touch Screen Digital Signature Pad")
                st.caption("Draw staff signature inside the white box below:")
                fast_canvas_result = st_canvas(
                    fill_color="rgba(255, 255, 255, 0)",
                    stroke_width=2.5,
                    stroke_color="#0F172A",
                    background_color="#FFFFFF",
                    height=135,
                    width=330,
                    drawing_mode="freedraw",
                    key=f"fast_sig_canvas_{target_staff['emp_id']}"
                )

                b_col1, b_col2 = st.columns([2, 1])
                with b_col1:
                    save_capture = st.button("💾 Save & Complete Capture", type="primary", use_container_width=True, key=f"btn_save_fast_capture_{target_staff['emp_id']}")
                with b_col2:
                    if st.button("✏️ Edit Text Details", use_container_width=True, key=f"btn_switch_edit_{target_staff['emp_id']}"):
                        st.session_state.active_form_mode = "edit"
                        st.rerun()

                if save_capture:
                    has_error = False
                    updated_photo_rel = None
                    updated_sig_rel = None
                    safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', target_staff['emp_id'])

                    # 1. Validate Photo
                    if selected_photo is None and "placeholder.png" in target_staff["photo_path"]:
                        st.error("⚠️ **Photo Missing**: Please snap a live photo using the camera tab or upload a photo file.")
                        has_error = True
                    elif selected_photo is not None:
                        is_bg_valid, bg_msg = validate_white_background(selected_photo)
                        if not is_bg_valid:
                            st.error(f"### ❌ Photo Background Rejected\n\nPlease review the details below:\n\n{bg_msg}")
                            st.info("💡 **Instructions**: The capture form remains open above. Adjust lighting or staff position against a white backdrop and snap the photo again.")
                            has_error = True
                        else:
                            try:
                                opt_p = process_and_optimize_photo(selected_photo)
                                p_fname = f"{safe_id}_photo.jpg"
                                p_abs = DIRS["photos"] / p_fname
                                opt_p.save(p_abs, format="JPEG", quality=95, optimize=True)
                                updated_photo_rel = f"photos/{p_fname}"
                            except Exception as ex_p:
                                st.error(f"❌ **Photo File Error**: Could not process image — {str(ex_p)}")
                                has_error = True

                    # 2. Validate Signature
                    has_sig_stroke = False
                    if fast_canvas_result is not None and fast_canvas_result.image_data is not None:
                        sig_arr = fast_canvas_result.image_data.astype(np.uint8)
                        if np.any(sig_arr[:, :, 3] > 0):
                            has_sig_stroke = True
                            try:
                                sig_img = Image.fromarray(sig_arr)
                                if sig_img.mode == "RGBA":
                                    bg = Image.new("RGB", sig_img.size, (255, 255, 255))
                                    bg.paste(sig_img, mask=sig_img.split()[3])
                                    sig_img = bg
                                s_fname = f"{safe_id}_sig.png"
                                s_abs = DIRS["signatures"] / s_fname
                                sig_img.save(s_abs, format="PNG")
                                updated_sig_rel = f"signatures/{s_fname}"
                            except Exception as ex_s:
                                st.error(f"❌ **Signature File Error**: Could not process signature file — {str(ex_s)}")
                                has_error = True

                    if not has_sig_stroke and "placeholder.png" in target_staff["signature_path"]:
                        st.error("⚠️ **Digital Signature Missing**: Please draw staff signature inside the white box above.")
                        has_error = True

                    # 3. Finalize
                    if not has_error:
                        ok, u_msg = update_staff_record(
                            emp_id=target_staff["emp_id"],
                            full_name=target_staff["full_name"],
                            category=target_staff["category"],
                            designation=target_staff.get("designation", "Staff Member"),
                            department=target_staff.get("department", "Technical"),
                            region=target_staff.get("region", user_region),
                            photo_path=updated_photo_rel,
                            signature_path=updated_sig_rel,
                            media_status="COMPLETE"
                        )
                        if ok:
                            updated_full_record = fetch_all_records("ALL")
                            match_r = next((r for r in updated_full_record if r["emp_id"] == target_staff["emp_id"]), None)
                            if match_r:
                                generate_id_request_form_pdf(match_r)
                            st.success(f"🎉 Media enrollment complete for {target_staff['full_name']}!")
                            st.session_state.editing_emp_id = None
                            st.rerun()
                        else:
                            st.error(f"❌ **Database Update Failed**: {u_msg}")

            else:
                # ==========================================
                # FULL DETAILS EDIT VIEW (TEXT INPUT FIELDS)
                # ==========================================
                with st.form(key=f"top_edit_form_{target_staff['emp_id']}"):
                    e_c1, e_c2 = st.columns(2)
                    with e_c1:
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

                    st.markdown("###### 📷 Staff Photo Capture (White Background Required)")
                    new_photo = st.file_uploader("📷 Select / Capture Staff Photo (JPG/PNG/HEIC)", type=["jpg", "jpeg", "png", "heic", "heif"], key=f"edit_photo_upload_{target_staff['emp_id']}")

                    st.markdown("###### ✍️ Digital Signature Canvas")
                    edit_canvas_result = st_canvas(
                        fill_color="rgba(255, 255, 255, 0)",
                        stroke_width=2.5,
                        stroke_color="#0F172A",
                        background_color="#FFFFFF",
                        height=130,
                        width=330,
                        drawing_mode="freedraw",
                        key=f"edit_sig_canvas_{target_staff['emp_id']}"
                    )

                    e_save = st.form_submit_button("💾 Save Profile Changes", type="primary", use_container_width=True)

                    if e_save:
                        if not e_name:
                            st.error("Full Name cannot be empty.")
                        else:
                            try:
                                updated_photo_rel = None
                                updated_sig_rel = None
                                safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', target_staff['emp_id'])
                                
                                if new_photo is not None:
                                    is_bg_valid, bg_msg = validate_white_background(new_photo)
                                    if not is_bg_valid:
                                        st.error(f"❌ Photo Update Rejected:\n\n{bg_msg}")
                                    else:
                                        opt_p = process_and_optimize_photo(new_photo)
                                        p_fname = f"{safe_id}_photo.jpg"
                                        p_abs = DIRS["photos"] / p_fname
                                        opt_p.save(p_abs, format="JPEG", quality=95, optimize=True)
                                        updated_photo_rel = f"photos/{p_fname}"

                                if edit_canvas_result is not None and edit_canvas_result.image_data is not None:
                                    sig_arr = edit_canvas_result.image_data.astype(np.uint8)
                                    if np.any(sig_arr[:, :, 3] > 0):
                                        sig_img = Image.fromarray(sig_arr)
                                        if sig_img.mode == "RGBA":
                                            bg = Image.new("RGB", sig_img.size, (255, 255, 255))
                                            bg.paste(sig_img, mask=sig_img.split()[3])
                                            sig_img = bg
                                        s_fname = f"{safe_id}_sig.png"
                                        s_abs = DIRS["signatures"] / s_fname
                                        sig_img.save(s_abs, format="PNG")
                                        updated_sig_rel = f"signatures/{s_fname}"

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
                                    photo_path=updated_photo_rel,
                                    signature_path=updated_sig_rel
                                )

                                if ok:
                                    updated_full_record = fetch_all_records("ALL")
                                    match_r = next((r for r in updated_full_record if r["emp_id"] == target_staff["emp_id"]), None)
                                    if match_r:
                                        generate_id_request_form_pdf(match_r)
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
        sf_col1, sf_col2 = st.columns([2, 1])
        with sf_col1:
            search_query = st.text_input("🔍 Search Staff by Name, Employee ID, or Department:", placeholder="e.g. YEDC-1045 or Technical").strip().lower()
        with sf_col2:
            default_status_filter_idx = 0
            if st.session_state.get("dir_status_filter_select") == "⚠️ Awaiting Photo & Signature (Pending Queue)":
                default_status_filter_idx = 1
            dir_status_filter = st.selectbox(
                "Filter Profile Status:",
                ["ALL", "⚠️ Awaiting Photo & Signature (Pending Queue)", "✅ Complete Profiles"],
                index=default_status_filter_idx,
                key="dir_status_filter_select"
            )

        filtered_staff = all_staff

        if dir_status_filter == "⚠️ Awaiting Photo & Signature (Pending Queue)":
            filtered_staff = [
                s for s in filtered_staff 
                if s.get("media_status") == "PENDING_MEDIA" or "placeholder.png" in s.get("photo_path", "") or "placeholder.png" in s.get("signature_path", "")
            ]
        elif dir_status_filter == "✅ Complete Profiles":
            filtered_staff = [
                s for s in filtered_staff 
                if s.get("media_status") != "PENDING_MEDIA" and "placeholder.png" not in s.get("photo_path", "") and "placeholder.png" not in s.get("signature_path", "")
            ]

        if search_query:
            filtered_staff = [
                s for s in filtered_staff
                if search_query in s["emp_id"].lower() or search_query in s["full_name"].lower() or search_query in s["category"].lower() or search_query in s.get("department", "").lower() or search_query in s.get("region", "").lower()
            ]

        st.caption(f"Showing {len(filtered_staff)} of {len(all_staff)} record(s)")

        for staff in filtered_staff:
            display_dept_label = "N/A" if staff.get('category') in ['Intern', 'NYSC'] else staff.get('department', 'Technical')
            display_desig_label = staff.get('category') if staff.get('category') in ['Intern', 'NYSC'] else staff.get('designation', 'Staff Member')
            
            is_pending = staff.get("media_status") == "PENDING_MEDIA" or "placeholder.png" in staff.get("photo_path", "") or "placeholder.png" in staff.get("signature_path", "")
            pending_tag = " [⚠️ AWAITING PHOTO & SIGNATURE]" if is_pending else ""

            with st.expander(f"🪪 **{staff['emp_id']}** - {staff['full_name']}{pending_tag} (Role: {display_desig_label} | Dept: {display_dept_label} - {staff['category']} - {staff.get('region', 'Adamawa')})"):
                c1, c2, c3 = st.columns([1.2, 1.2, 1.6])

                photo_abs = BASE_DIR / staff["photo_path"]
                sig_abs = BASE_DIR / staff["signature_path"]

                with c1:
                    st.markdown("**Staff Photo**")
                    if photo_abs.exists():
                        st.image(str(photo_abs), width=105)
                    else:
                        st.caption("Missing")

                with c2:
                    st.markdown("**Signature**")
                    if sig_abs.exists():
                        st.image(str(sig_abs), width=120)
                    else:
                        st.caption("Missing")

                with c3:
                    st.markdown("**Actions**")
                    if is_pending:
                        st.warning("⚠️ Photo/Signature Pending")
                        if st.button("📷 Capture Photo & Signature", key=f"btn_cap_pending_{staff['emp_id']}", type="primary", use_container_width=True):
                            st.session_state.editing_emp_id = staff["emp_id"]
                            st.session_state.active_form_mode = "capture"
                            st.session_state.scroll_to_top = True
                            st.rerun()

                    # 1. Download Official ID Card Request Form PDF (Fits 100% on 1 A4 Page)
                    ok_req, req_pdf_path = generate_id_request_form_pdf(staff)
                    clean_name = re.sub(r'[^a-zA-Z0-9_-]', '_', staff['full_name'])
                    clean_id = re.sub(r'[^a-zA-Z0-9_-]', '_', staff['emp_id'])
                    
                    if ok_req and os.path.exists(req_pdf_path):
                        with open(req_pdf_path, "rb") as req_f:
                            st.download_button(
                                label="📄 Download Request Form PDF",
                                data=req_f,
                                file_name=f"{clean_id}_{clean_name}_Request_Form.pdf",
                                mime="application/pdf",
                                type="secondary" if is_pending else "primary",
                                use_container_width=True,
                                key=f"dl_req_form_{staff['emp_id']}"
                            )

                    # 2. Edit Record Action
                    if st.button("✏️ Edit Record", key=f"btn_edit_{staff['emp_id']}", use_container_width=True):
                        st.session_state.editing_emp_id = staff["emp_id"]
                        st.session_state.active_form_mode = "edit"
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

    tab_analytics, tab_audit = st.tabs(["📊 Analytics Overview", "📜 Audit & Activity Logs"])

    with tab_analytics:
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

        perm_count = sum(1 for r in rep_records if str(r.get("category", "")).strip().lower() == "permanent")
        contract_count = sum(1 for r in rep_records if str(r.get("category", "")).strip().lower() == "contract")
        intern_count = sum(1 for r in rep_records if str(r.get("category", "")).strip().lower() == "intern")
        nysc_count = sum(1 for r in rep_records if str(r.get("category", "")).strip().lower() == "nysc")
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

    with tab_audit:
        st.markdown("#### 📜 System Audit & Activity Logs")
        st.caption("Complete timestamped history of staff registration, updates, deletions, PDF card exports, and user logins.")
        
        a_col1, a_col2 = st.columns([1, 1], gap="medium")
        with a_col1:
            action_filter_opt = st.selectbox(
                "Filter Action Type:",
                ["ALL", "CREATE_STAFF", "UPDATE_STAFF", "DELETE_STAFF", "BULK_IMPORT", "EXPORT_PDF_CARD", "EXPORT_REQUEST_FORM", "EXPORT_MASTER_REPORT", "EXPORT_BATCH_ZIP", "USER_LOGIN", "CREATE_USER", "DELETE_USER"],
                key="audit_action_select"
            )
        with a_col2:
            audit_region_opt = rep_region_filter if 'rep_region_filter' in locals() else (user_region if not is_super_admin else "ALL")
            st.info(f"Audit log region scope: **{audit_region_opt}**")
            
        audit_records = fetch_audit_logs(region_filter=audit_region_opt, action_filter=action_filter_opt, limit=500)
        
        # Summary Audit Metrics
        aud_m1, aud_m2, aud_m3, aud_m4 = st.columns(4)
        with aud_m1:
            st.metric("Total Audit Events", len(audit_records))
        with aud_m2:
            staff_ops = sum(1 for a in audit_records if a["action"] in ["CREATE_STAFF", "UPDATE_STAFF", "DELETE_STAFF", "BULK_IMPORT"])
            st.metric("Staff Modifications", staff_ops)
        with aud_m3:
            pdf_ops = sum(1 for a in audit_records if "EXPORT" in a["action"])
            st.metric("PDF / Report Exports", pdf_ops)
        with aud_m4:
            login_ops = sum(1 for a in audit_records if a["action"] == "USER_LOGIN")
            st.metric("User Logins", login_ops)
            
        st.markdown("<br>", unsafe_allow_html=True)
        
        if audit_records:
            df_audit = pd.DataFrame(audit_records)
            df_audit_display = df_audit[["timestamp", "username", "role", "action", "target_id", "details", "region"]].copy()
            df_audit_display.columns = ["Timestamp", "User", "Role", "Action Type", "Target ID", "Activity Details", "Region"]
            
            st.dataframe(df_audit_display, use_container_width=True)
            
            audit_csv = df_audit_display.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Export Audit Logs (CSV)",
                data=audit_csv,
                file_name=f"YEDC_Audit_Logs_{audit_region_opt}_{action_filter_opt}.csv",
                mime="text/csv",
                type="primary",
                use_container_width=True,
                key="btn_dl_audit_logs_csv"
            )
        else:
            st.info("No audit logs matching the selected filter criteria.")


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
