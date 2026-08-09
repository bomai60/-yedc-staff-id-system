# Staff ID Card Data Collection & Automated PDF Generation System

A complete local web application built with **Python**, **Streamlit**, **SQLite**, **Pillow**, **streamlit-drawable-canvas**, **Jinja2**, and **pdfkit**.

---

## 📌 Features

1. **Automatic Storage Initialization**: Creates `photos/`, `signatures/`, `qr_codes/`, and `generated_pdfs/` directories and initializes SQLite `staff_id_data.db`.
2. **Tab 1 - Data Collection**:
   - Capture Employee ID, Full Name, and Category (Permanent, Contract, Intern, NYSC).
   - Staff Photo Upload / Camera Capture automatically cropped & resized to 600x800 px using Pillow (`ImageOps.fit`).
   - Touch-friendly digital signature canvas using `streamlit-drawable-canvas`.
   - Local SQLite database insertion (preventing duplicate Employee IDs).
3. **Tab 2 - Admin Batch Processing & PDF Generation**:
   - Batch upload QR Code image files named by Employee ID (e.g. `YEDC-1045.png`).
   - Automatic filename ID extraction and database matching.
   - Jinja2 HTML rendering (`template.html`).
   - `pdfkit` (wkhtmltopdf) conversion to print-ready CR80 card size (`54mm x 86mm`, 0mm margins, portrait).

---

## 🚀 Quick Setup & Execution

### 1. Prerequisites & Installation

Create a virtual environment and install the required Python packages:

```bash
# Navigate to the project directory
cd staff_id_system

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# On Windows PowerShell:
.venv\Scripts\Activate.ps1
# On Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Install wkhtmltopdf (Required for PDF Generation)

`pdfkit` requires **wkhtmltopdf** to render HTML to PDF.

- **Windows**:
  1. Download installer from [wkhtmltopdf downloads](https://wkhtmltopdf.org/downloads.html).
  2. Install to default path (`C:\Program Files\wkhtmltopdf`) or add `bin` directory to your System `PATH`.
- **Linux (Ubuntu/Debian)**:
  ```bash
  sudo apt-get update
  sudo apt-get install -y wkhtmltopdf
  ```
- **macOS**:
  ```bash
  brew install wkhtmltopdf
  ```

---

## 🏃 Running the Application

Launch the Streamlit web application:

```bash
streamlit run app.py
```

Open your browser at `http://localhost:8501`.

---

## 📁 Directory Structure

```text
staff_id_system/
├── app.py                   # Main Streamlit application
├── template.html            # Jinja2 CR80 HTML template
├── requirements.txt         # Python dependencies
├── README.md                # Documentation & Setup Guide
├── staff_id_data.db         # Local SQLite Database (auto-created)
├── photos/                  # 600x800 cropped staff photos
├── signatures/              # Digital signatures (PNG)
├── qr_codes/                # Batch uploaded QR codes
└── generated_pdfs/          # Output CR80 PDF ID cards
```
