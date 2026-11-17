from PIL import Image, ImageDraw
import os

res_dir = r"./app/res"
os.makedirs(os.path.join(res_dir, "drawable"), exist_ok=True)
os.makedirs(os.path.join(res_dir, "mipmap-anydpi-v26"), exist_ok=True)

# Adaptive icon foreground (432x432 with safe zone in center 288x288)
size = 432
fg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
draw = ImageDraw.Draw(fg)

cx, cy = size // 2, size // 2
# Outer glow ring inside safe zone
draw.ellipse([cx - 110, cy - 110, cx + 110, cy + 110], fill=(12, 24, 33, 255), outline=(91, 255, 173, 255), width=8)
draw.ellipse([cx - 75, cy - 75, cx + 75, cy + 75], outline=(66, 217, 255, 220), width=6)
draw.ellipse([cx - 42, cy - 42, cx + 42, cy + 42], outline=(241, 196, 15, 230), width=6)
draw.ellipse([cx - 16, cy - 16, cx + 16, cy + 16], fill=(91, 255, 173, 255))

# Antenna mast
draw.line([cx, cy + 16, cx, cy + 95], fill=(91, 255, 173, 255), width=10)
draw.line([cx - 38, cy + 95, cx + 38, cy + 95], fill=(91, 255, 173, 255), width=10)

fg_path = os.path.join(res_dir, "drawable", "ic_launcher_fg.png")
fg.save(fg_path, "PNG")

# Adaptive icon XML for Android 8.0 - Android 16+
xml_content = """<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@android:color/black" />
    <foreground android:drawable="@drawable/ic_launcher_fg" />
    <monochrome android:drawable="@drawable/ic_launcher_fg" />
</adaptive-icon>
"""
with open(os.path.join(res_dir, "mipmap-anydpi-v26", "ic_launcher.xml"), "w", encoding="utf-8") as f:
    f.write(xml_content)

# Network security config XML for Android 9 (Pie) through Android 16
xml_dir = os.path.join(res_dir, "xml")
os.makedirs(xml_dir, exist_ok=True)
net_sec = """<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="true">
        <trust-anchors>
            <certificates src="system" />
            <certificates src="user" />
        </trust-anchors>
    </base-config>
</network-security-config>
"""
with open(os.path.join(xml_dir, "network_security_config.xml"), "w", encoding="utf-8") as f:
    f.write(net_sec)

print("Resources generated successfully")
