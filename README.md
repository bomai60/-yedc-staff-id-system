# ⚡ YEDC Enterprise Staff Identity Management System

An enterprise-grade, multi-region Staff Registration, Identity Card Generation, and HR Analytics System custom-built for **Yola Electricity Distribution Company (YEDC)**.

---

## 🔗 Quick Links

* **GitHub Repository**: [https://github.com/bomai60/-yedc-staff-id-system.git](https://github.com/bomai60/-yedc-staff-id-system.git)
* **Local Web Application**: [http://localhost:8501](http://localhost:8501)

---

## 🛠️ Technology Stack & System Architecture

```text
+-----------------------------------------------------------------------------------+
|                                  USER INTERFACE                                   |
|   Streamlit (v1.30+) | Custom Enterprise CSS3 | Google Fonts (Outfit / Jakarta)   |
|   HTML5 Camera API (st.camera_input) | Signature Canvas (drawable-canvas)         |
|   Interactive In-Table Selection (st.data_editor + CheckboxColumn)                |
+-----------------------------------------+-----------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|                             CORE APPLICATION LOGIC                                |
|   Python 3.10+ | Event Dispatcher & Session State Management                          |
+--------------------+--------------------+--------------------+--------------------+
                     |                    |                    |
                     v                    v                    v
+------------------------+ +-------------------+ +----------------------------------+
|    DATABASE ENGINE     | |  IMAGE PROCESSOR  | |       DOCUMENT PDF ENGINE        |
|  SQLite3 (Native SQL)  | |  PIL + NumPy      | |  Jinja2 HTML Templating          |
|  SHA-256 Hashing       | |  White BG Checker | |  Dual Engine:                    |
|  Tables: staff_records,| |  LANCZOS Resample | |  - Primary: pdfkit / wkhtmltopdf |
|          users         | |  RGBA Alpha-Mask  | |  - Fallback: xhtml2pdf (pisa)    |
+------------------------+ +-------------------+ +----------------------------------+
```

### 1. 🐍 Core Runtime & Web Framework
* **Language**: Python 3.10+
* **Web Framework**: [Streamlit](https://streamlit.io/) (v1.30+)
* **Server Infrastructure**: Built-in Uvicorn / Tornado async WSGI/ASGI application server.

### 2. 🗄️ Database Architecture
* **Database Engine**: **SQLite3** (`staff_id_data.db`)
* **Driver**: Native Python `sqlite3` module with dictionary row factory (`sqlite3.Row`).
* **Database Schemas**:
  * **`staff_records`**: `emp_id` *(PRIMARY KEY)*, `full_name`, `category`, `designation`, `department`, `region`, `photo_path`, `signature_path`, `qr_path`.
  * **`users`**: `username` *(PRIMARY KEY)*, `password_hash` *(SHA-256)*, `full_name`, `role`, `region`.

### 3. 🎨 Frontend Design System
* **Styling**: Vanilla CSS3 with YEDC Electric Orange (`#ff6b00`) theme and Dark Navy (`#0f172a`) glassmorphism headers.
* **Typography**: Google Fonts (`Outfit` for headings, `Plus Jakarta Sans` for form labels and data tables).
* **Canvas Signature Pad**: `streamlit-drawable-canvas` (HTML5 Canvas vector drawing engine).
* **In-Table Selection**: `st.data_editor` with interactive `CheckboxColumn` for row-by-row batch export selection.

### 4. 🖼️ Photo Processing & White Background Validation Engine
* **Libraries**: `Pillow` (PIL) + `NumPy` + `pillow-heif` (Supports iPhone HEIC/HEIF photos).
* **Automated White Background Detection (`validate_white_background`)**:
  * Samples top-left ($15\% \times 15\%$) and top-right ($15\% \times 15\%$) corner patches of uploaded staff photos.
  * Checks RGB brightness ($R, G, B \ge 200$) and color saturation neutrality ($\max(RGB) - \min(RGB) \le 35$).
  * Automatically rejects non-white background uploads while preserving form input data (`clear_on_submit=False`).
* **Algorithms**: EXIF transposition auto-rotation + Ultra HD `LANCZOS` resampling (1600×2000 resolution @ ~1 MB print quality) + RGBA alpha-channel compositing.

### 5. 📄 Document Templating & Dual PDF Engines
* **Templating Engine**: `Jinja2` HTML/CSS context rendering.
* **Image Data Transport**: Base64 Data URIs (`data:image/png;base64,...`) for 100% reliable offline PDF rendering.
* **Guaranteed 1-Page A4 Layout**: Optimized logo (`38px`), signature (`22px`), passport photo (`100px x 125px`), and print margins (`8mm 12mm`) fitting 100% on 1 A4 page.
* **Dual PDF Engine**:
  1. **Primary Engine**: `pdfkit` + `wkhtmltopdf` (High-precision vector PDF rendering).
  2. **Automatic Fallback Engine**: `xhtml2pdf` (pisa) (Pure-Python PDF rendering).

---

## 🌟 Core System Features

### 🏢 1. Four Operational Regions Scoping
Strict regional boundary enforcement across all operational workflows:
* **Adamawa Region**
* **Borno Region**
* **Taraba Region**
* **Yobe Region**

### 🔒 2. Role-Based Access Control (RBAC)
* **👑 Super Admin**: Full access across all 4 regions, directory filters, global analytics, multi-region batch selection, master PDF exports, and user management.
* **🛡️ Regional Admin**: Strictly scoped to their assigned region across Registration, Directory, Batch Processing, and Reports.
* **✍️ Enrollment Assistant**: Scoped to **Dashboard**, **Staff Register**, and **Batch Processing** for their assigned region.

### 🪪 3. Multi-Format Output Reports & Cards
1. **CR80 Plastic ID Card PDF (54mm × 86mm)**: Standard credit-card size badge formatted for direct PVC plastic card printers.
2. **Official YEDC HR Identity Card Request Form (1-Page A4 PDF)**: Formatted Jinja2 template matching official YEDC HR document layout (Passport photo, dotted lines for Name/ID/Dept/Role, checkboxes, request/expected dates, staff sign, and HR officer sign).
3. **Master Directory Roster PDF & CSV**: Complete A4 table report with staff photos.

### ⚙️ 4. Interactive Batch Selection & Multi-Region Processing
* **Multi-Select Region Filter (Super Admin)**: Allows Super Admins to select 1, 2, 3, or all 4 regions simultaneously for batch exports.
* **In-Table Row Checkboxes (`[ ] Select`)**: Row-by-row checkboxes inside `st.data_editor` allowing users to pick specific staff members (e.g. 2 out of 9) without filling up screen space.
* **Quick Action Controls**: `☑️ Select All Staff` and `☒ Deselect All` buttons for instant bulk checking.
* **One-Click Bulk PDF & ZIP Download**: One-click bulk PDF generation and `.zip` archive download for selected records.

---

## 🔐 Default Test Credentials

| Role | Username | Password | Region Scope | Permitted Tabs |
| :--- | :--- | :--- | :--- | :--- |
| **Super Admin** | `admin` | `admin123` | **ALL Regions** | All Tabs + User Management |
| **Regional Admin** | `adamawa_admin` | `adamawa123` | **Adamawa** | Dashboard, Register, Batch, Directory, Reports |
| **Regional Admin** | `borno_admin` | `borno123` | **Borno** | Dashboard, Register, Batch, Directory, Reports |
| **Regional Admin** | `taraba_admin` | `taraba123` | **Taraba** | Dashboard, Register, Batch, Directory, Reports |
| **Regional Admin** | `yobe_admin` | `yobe123` | **Yobe** | Dashboard, Register, Batch, Directory, Reports |
| **Enrollment Assistant**| `adamawa_assistant` | `assist123` | **Adamawa** | Dashboard, Staff Register & Batch Processing |
| **Enrollment Assistant**| `borno_assistant` | `assist123` | **Borno** | Dashboard, Staff Register & Batch Processing |

---

## 🚀 Quick Start (Local Setup)

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

### 3. Launch Application
```bash
streamlit run app.py
```
Open **[http://localhost:8501](http://localhost:8501)** in your browser.

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
3. Click **New App**, select repository `bomai60/-yedc-staff-id-system`, branch `master`, and main file `app.py`.
4. Click **Deploy**.

---

## 📄 License
Internal Corporate Software developed for **Yola Electricity Distribution Company (YEDC)**. All rights reserved.
