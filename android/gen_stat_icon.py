from PIL import Image, ImageDraw
import math

out = r"./app/res/drawable/ic_stat.png"
S = 96
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
c = S / 2
W = (255, 255, 255, 255)
# Знак радіації: центральне коло + три сектори
d.ellipse([c - 9, c - 9, c + 9, c + 9], fill=W)
for k in range(3):
    start = -90 + k * 120 - 30
    d.pieslice([c - 44, c - 44, c + 44, c + 44], start, start + 60, fill=W)
d.ellipse([c - 15, c - 15, c + 15, c + 15], fill=(0, 0, 0, 0))
d.ellipse([c - 9, c - 9, c + 9, c + 9], fill=W)
img.save(out)
print("ic_stat.png saved")
