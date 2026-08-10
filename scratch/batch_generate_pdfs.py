import sys
import sqlite3
from pathlib import Path

BASE_DIR = Path(r"C:\Users\Mohammed Bomai\.gemini\antigravity-ide\scratch\staff_id_system").resolve()
sys.path.insert(0, str(BASE_DIR))

import app  # import pdf generator from app.py

DB_PATH = BASE_DIR / "staff_id_data.db"

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute("SELECT * FROM staff_records WHERE qr_path != 'PENDING'")
records = [dict(r) for r in cursor.fetchall()]

print(f"Generating CR80 PDF ID cards for {len(records)} staff records...")

count = 0
for r in records:
    ok, res = app.generate_pdf_card(r)
    if ok:
        count += 1
        print(f"SUCCESS: Generated PDF ID Card for {r['emp_id']} ({r['full_name']})")
    else:
        print(f"FAILED for {r['emp_id']}: {res}")

conn.close()
print(f"\nSUCCESS: Generated {count} CR80 PDF ID Cards!")
