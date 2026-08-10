import sqlite3
import os
import qrcode
from pathlib import Path

BASE_DIR = Path(r"C:\Users\Mohammed Bomai\.gemini\antigravity-ide\scratch\staff_id_system")
DB_PATH = BASE_DIR / "staff_id_data.db"
QR_DIR = BASE_DIR / "qr_codes"

os.makedirs(QR_DIR, exist_ok=True)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("SELECT * FROM staff_records")
records = cursor.fetchall()

updated_count = 0
for r in records:
    emp_id = r["emp_id"]
    full_name = r["full_name"]
    category = r["category"]
    region = r["region"]
    
    # Scannable payload data formatted for YEDC ID Verification
    qr_data = f"YEDC STAFF ID VERIFICATION\nID: {emp_id}\nName: {full_name}\nCategory: {category}\nRegion: {region}\nStatus: VERIFIED ACTIVE"
    
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(qr_data)
    qr.make(fit=True)
    
    qr_img = qr.make_image(fill_color="#0F172A", back_color="#FFFFFF")
    
    qr_filename = f"{emp_id}_qr.png"
    qr_rel_path = f"qr_codes/{qr_filename}"
    qr_abs_path = QR_DIR / qr_filename
    
    qr_img.save(qr_abs_path)
    
    cursor.execute("UPDATE staff_records SET qr_path = ? WHERE emp_id = ?", (qr_rel_path, emp_id))
    updated_count += 1

conn.commit()
conn.close()

print(f"SUCCESS: Generated scannable QR codes for {updated_count} staff records.")
