import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(r"C:\Users\Mohammed Bomai\.gemini\antigravity-ide\scratch\staff_id_system")
ASSETS_DIR = BASE_DIR / "assets"
os.makedirs(ASSETS_DIR, exist_ok=True)

# Create a clean transparent high-res signature image for Authorized Signature
sig_img = Image.new("RGBA", (400, 150), (255, 255, 255, 0))
draw = ImageDraw.Draw(sig_img)

# Draw elegant cursive-like signature strokes matching the template
points = [(30, 90), (60, 40), (110, 30), (160, 60), (200, 100), (230, 40), (250, 110), (270, 70), (320, 80), (360, 75)]
draw.line(points, fill="#0F172A", width=4, joint="curve")

# Loop stroke
draw.arc([40, 20, 180, 110], start=180, end=360, fill="#0F172A", width=3)
draw.line([(20, 120), (380, 120)], fill="#0F172A", width=4)

sig_path = ASSETS_DIR / "authorised_signature.png"
sig_img.save(sig_path, "PNG")
print(f"Created authorised signature asset at: {sig_path}")
