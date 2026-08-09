# ⚡ YEDC Enterprise Staff Identity Management Portal

An enterprise-grade, multi-region Staff Registration, Identity Card Generation, and HR Analytics System custom-built for **Yola Electricity Distribution Company (YEDC)**.

---

## 🌟 Key Features

### 🏢 1. Four Operational Regions Scoping
Strict regional boundary enforcement across all operational workflows:
* **Adamawa Region**
* **Borno Region**
* **Taraba Region**
* **Yobe Region**

### 🔒 2. Role-Based Access Control (RBAC)
* **👑 Super Admin**: Full access across all 4 regions, directory filters, global analytics, master PDF exports, and user management.
* **🛡️ Regional Admin**: Strictly scoped to their assigned region across Registration, Directory, Batch Processing, and Reports.
* **✍️ Enrollment Assistant**: Strictly restricted to **Dashboard** and **Staff Register** tabs for their assigned region.

### 📝 3. Digital Staff Enrollment & Canvas Signature
* **Ultra-HD Photo Optimization**: Camera capture or file upload automatically optimized to ~600 KB print quality (1200x1600 resolution).
* **Digital Signature Pad**: Freehand drawing canvas with transparent alpha-masking onto solid background to prevent cutout lines.
* **Live Draft Preview**: Real-time plastic ID card preview with official NYSC emblem support.

### 🪪 4. Dual PDF Generation Engine
1. **CR80 Plastic ID Card PDF (54mm × 86mm)**: Standard credit-card size badge formatted for direct PVC plastic card printers.
2. **Official YEDC HR Identity Card Request Form (1-Page A4 PDF)**: Formatted Jinja2 template matching official YEDC HR document layout (Passport photo, dotted lines for Name/ID/Dept/Role, checkboxes, request/expected dates, staff sign, and HR officer sign).
3. **Master Directory Roster PDF**: Complete A4 table report with staff photos.

### 📦 5. Batch QR Processing & Bulk ZIP Download
* Upload batch QR code image files named by Employee ID (e.g., `YEDC-1045.png`).
* Automatic database matching and status updates.
* One-click bulk PDF card generation and `.zip` archive download.

### 📈 6. Category Reports & Analytics
* Category metric breakdown cards: **Total Staff**, **Permanent**, **Contract**, **Intern**, and **NYSC Members** (featuring official NYSC crest logo).
* One-click CSV and Master PDF report export.

---

## 🔐 Default Test Credentials

| Role | Username | Password | Region Scope | Permitted Tabs |
| :--- | :--- | :--- | :--- | :--- |
| **Super Admin** | `admin` | `admin123` | **ALL Regions** | All Tabs + User Management |
| **Regional Admin** | `adamawa_admin` | `adamawa123` | **Adamawa** | Dashboard, Register, Batch, Directory, Reports |
| **Regional Admin** | `borno_admin` | `borno123` | **Borno** | Dashboard, Register, Batch, Directory, Reports |
| **Regional Admin** | `taraba_admin` | `taraba123` | **Taraba** | Dashboard, Register, Batch, Directory, Reports |
| **Regional Admin** | `yobe_admin` | `yobe123` | **Yobe** | Dashboard, Register, Batch, Directory, Reports |
| **Enrollment Assistant**| `adamawa_assistant` | `assist123` | **Adamawa** | Dashboard & Staff Register Only |
| **Enrollment Assistant**| `borno_assistant` | `assist123` | **Borno** | Dashboard & Staff Register Only |

---

## 🚀 Quick Start (Local Setup)

### Prerequisites
* Python **3.10+**
* Git

### 1. Clone Repository
```bash
git clone https://github.com/bomai60/-yedc-staff-id-system.git
cd -yedc-staff-id-system
```

### 2. Set Up Virtual Environment & Install Dependencies
```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# Install Requirements
pip install -r requirements.txt
```

### 3. Launch Streamlit Application
```bash
streamlit run app.py
```
Open **`http://localhost:8501`** in your browser.

---

## 📂 Project Directory Structure

```text
-yedc-staff-id-system/
├── app.py                         # Main Streamlit Application Logic
├── requirements.txt               # Python Dependencies
├── template.html                  # Jinja2 Template for CR80 Plastic ID Badges
├── id_request_form_template.html  # Jinja2 Template for Official A4 HR Request Form
├── staff_report_template.html     # Jinja2 Template for Master Directory PDF Reports
├── staff_id_data.db               # SQLite Database (Auto-initialized)
├── assets/                        # Corporate Logos & Emblem Badges
│   ├── logo.png                   # YEDC Official Logo
│   └── nysc_logo.png              # Official NYSC Crest Logo
├── photos/                        # Optimized Staff Passport Photos
├── signatures/                    # Digital Signature Captures
├── qr_codes/                      # Matched Employee QR Codes
└── generated_pdfs/                # Generated PDF ID Cards & Reports
```

---

## ☁️ Deployment

### Streamlit Community Cloud (Free 24/7 Deployment)
1. Push code to GitHub repository: `https://github.com/bomai60/-yedc-staff-id-system.git`
2. Log in at **[share.streamlit.io](https://share.streamlit.io)**.
3. Click **New App**, select your repository `bomai60/-yedc-staff-id-system`, branch `master`, and main file `app.py`.
4. Click **Deploy**.

---

## 🛠️ Technology Stack
* **Frontend UI & State Management**: [Streamlit](https://streamlit.io/)
* **Database**: SQLite3
* **Templating Engine**: Jinja2
* **PDF Rendering Engine**: Dual-Engine (`wkhtmltopdf` / `pdfkit` with pure-Python `xhtml2pdf` fallback)
* **Image Processing**: Pillow & Pillow-HEIF (iPhone HEIC/HEIF photo support)
* **Signature Canvas**: `streamlit-drawable-canvas`

---

## 📄 License
Internal Corporate Software developed for **Yola Electricity Distribution Company (YEDC)**. All rights reserved.
