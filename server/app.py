import os
import re
import json
import math
import bisect
import subprocess
import threading
import time
from flask import Flask, jsonify, render_template_string, request, send_file, Response
import paho.mqtt.client as mqtt

app = Flask(__name__)


def round_text(text, digits=2):
    """Округляет все дробные числа в строке: '20.99874 °C' -> '21.00 °C'."""
    return re.sub(r"-?\d+\.\d+", lambda m: f"{float(m.group()):.{digits}f}", str(text))


def node_hex(num):
    """Номер ноды из JSON ("from") -> привычный вид !a1b2c3d4."""
    return f"!{int(num) & 0xffffffff:08x}"


def show(value, unit="", digits=None):
    if value is None:
        return "—"
    if digits is not None:
        value = f"{float(value):.{digits}f}"
    return f"{value}{unit}"


def fmt_size(num_bytes):
    """Байты -> читаемый вид: 1536 -> '1.5 КБ'."""
    value = float(num_bytes)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ТБ"


def save_json(path, data):
    """Атомарная запись (tmp + rename), чтобы файл не побился при потере питания."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as e:
        print(f"Save error {path}: {e}", flush=True)


def read_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return int(f.read().strip()) / 1000
    except (OSError, ValueError):
        return None


def read_cpu_times():
    """(idle, total) из /proc/stat — для расчёта загрузки CPU."""
    try:
        with open("/proc/stat", "r") as f:
            values = [int(x) for x in f.readline().split()[1:9]]
        return values[3] + values[4], sum(values)
    except (OSError, ValueError, IndexError):
        return None


def memory_info():
    """(использовано, всего) байт; использовано = всего - доступно."""
    try:
        info = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                key, _, rest = line.partition(":")
                if key in ("MemTotal", "MemAvailable"):
                    info[key] = int(rest.split()[0]) * 1024
        return info["MemTotal"] - info["MemAvailable"], info["MemTotal"]
    except (OSError, KeyError, ValueError, IndexError):
        return None


def disk_info(path="/"):
    """(вільно, всього) байт для microSD (корінь файлової системи)."""
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    return (free, total) if total > 0 else None


def read_wifi():
    """(інтерфейс, рівень сигналу dBm) з /proc/net/wireless або None."""
    try:
        with open("/proc/net/wireless", "r") as f:
            lines = f.readlines()[2:]
    except OSError:
        return None
    for line in lines:
        name, _, rest = line.partition(":")
        fields = rest.split()
        try:
            level = float(fields[2].rstrip("."))
        except (IndexError, ValueError):
            continue
        if level < 0:
            return name.strip(), level
    return None


def uptime_text():
    try:
        with open("/proc/uptime", "r") as f:
            seconds = int(float(f.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return "Недоступно"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    parts = []
    if days:
        parts.append(f"{days} д")
    if days or hours:
        parts.append(f"{hours} год")
    parts.append(f"{rest // 60} хв")
    return " ".join(parts)


_service_cache = {}
_ygg_cache = {"time": 0, "info": None}
_rns_cache = {"time": 0, "info": None}


def service_state(unit):
    """Стан systemd-сервісу, кеш на 10 секунд."""
    now = time.time()
    cached = _service_cache.get(unit)
    if cached and now - cached[0] < 10:
        return cached[1]
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=3
        )
        state = result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        state = "Недоступно"
    _service_cache[unit] = (now, state)
    return state


def yggdrasil_info():
    """Статус пірів Yggdrasil через yggdrasilctl -json getPeers (кеш 15 с)."""
    now = time.time()
    if _ygg_cache["info"] is not None and now - _ygg_cache["time"] < 15:
        return _ygg_cache["info"]
    try:
        res = subprocess.run(
            ["yggdrasilctl", "-json", "getPeers"],
            capture_output=True, text=True, timeout=3
        )
        data = json.loads(res.stdout)
        peers = data.get("peers") or []
        up_peers = [p for p in peers if p.get("up")]
        lats = [p.get("latency", 0) / 1e6 for p in up_peers if p.get("latency", 0) > 0]
        min_lat = min(lats) if lats else None
        rx = sum(p.get("bytes_recvd", 0) for p in peers)
        tx = sum(p.get("bytes_sent", 0) for p in peers)
        info = {
            "up": len(up_peers),
            "total": len(peers),
            "min_lat": min_lat,
            "rx": rx,
            "tx": tx
        }
    except Exception:
        info = None
    _ygg_cache["time"] = now
    _ygg_cache["info"] = info
    return info


def rns_info():
    """Кількість активних інтерфейсів та сумарний трафік Reticulum (кеш 15 с)."""
    now = time.time()
    if _rns_cache["info"] is not None and now - _rns_cache["time"] < 15:
        return _rns_cache["info"]
    try:
        res = subprocess.run(
            ["/home/example/.local/bin/rnstatus", "-j"],
            capture_output=True, text=True, timeout=4
        )
        data = json.loads(res.stdout)
        ifaces = [
            i for i in data.get("interfaces", [])
            if not str(i.get("name", "")).startswith("Shared Instance")
        ]
        up_ifaces = [i for i in ifaces if i.get("status")]
        ygg_up = [i for i in up_ifaces if "yggdrasil" in str(i.get("name", "")).lower()]
        rxb = sum(i.get("rxb", 0) for i in ifaces)
        txb = sum(i.get("txb", 0) for i in ifaces)
        info = {
            "up": len(up_ifaces),
            "total": len(ifaces),
            "ygg_up": len(ygg_up),
            "rx": rxb,
            "tx": txb
        }
    except Exception:
        info = None
    _rns_cache["time"] = now
    _rns_cache["info"] = info
    return info


# ---------- погода: розрахунки ----------
IAQ_LEVELS = [
    (50, "Відмінне", "#5bffad"),
    (100, "Добре", "#9be564"),
    (150, "Легке забруднення", "#f1c40f"),
    (200, "Помірне забруднення", "#e67e22"),
    (250, "Сильне забруднення", "#ff5c5c"),
    (350, "Дуже сильне забруднення", "#c04bff"),
]


def iaq_level(value):
    for limit, label, color in IAQ_LEVELS:
        if value <= limit:
            return label, color
    return "Екстремальне забруднення", "#ff2d55"


def dew_point(temp_c, humidity):
    """Точка роси (формула Магнуса), °C."""
    if temp_c is None or humidity is None or humidity <= 0:
        return None
    a, b = 17.62, 243.12
    g = math.log(humidity / 100) + a * temp_c / (b + temp_c)
    return b * g / (a - g)


def dew_comfort(dp):
    if dp < 10:
        return "Сухо"
    if dp < 16:
        return "Комфортно"
    if dp < 18:
        return "Трохи волого"
    if dp < 21:
        return "Волого, відчувається задуха"
    return "Дуже волого"


def last_value(rows, key):
    for row in reversed(rows):
        value = row.get(key)
        if isinstance(value, (int, float)):
            return value
    return None


def pressure_trend(rows):
    """Тенденція тиску приблизно за 3 години (береться найближча точка в межах 1-4 годин тому)."""
    points = [r for r in rows if isinstance(r.get("pressure"), (int, float))]
    if not points:
        return None
    last = points[-1]
    target = last["time"] - 3 * 3600
    candidates = [
        r for r in points
        if last["time"] - 4 * 3600 <= r["time"] <= last["time"] - 3600
    ]
    if not candidates:
        return None
    old = min(candidates, key=lambda r: abs(r["time"] - target))
    hours = (last["time"] - old["time"]) / 3600
    delta = last["pressure"] - old["pressure"]
    rate = delta * 3 / hours  # для оцінки приводимо до швидкості за 3 години
    if abs(rate) < 1:
        arrow, text, hint, color = "→", "Стабільний", "Без суттєвих змін погоди", GREEN
    elif rate > 0:
        if rate >= 3.5:
            arrow, text, hint, color = "↑", "Швидко росте", "Різке зростання, можливий вітер", YELLOW
        else:
            arrow, text, hint, color = "↗", "Повільно росте", "Ймовірне покращення погоди", GREEN
    else:
        if rate <= -3.5:
            arrow, text, hint, color = "↓", "Швидко падає", "Різке падіння, можливі опади чи шторм", RED
        else:
            arrow, text, hint, color = "↘", "Повільно падає", "Можливе погіршення погоди", YELLOW
    return {
        "arrow": arrow, "text": text, "hint": hint, "color": color,
        "delta": delta, "hours": round(hours, 1)
    }


def barometer_card(rows):
    title = "🧭 Барометр · тенденція"
    points = [r for r in rows if isinstance(r.get("pressure"), (int, float))]
    if not points:
        return card(title, "Немає даних")
    trend = pressure_trend(rows)
    if trend is None:
        span = (points[-1]["time"] - points[0]["time"]) / 3600
        if span < 1:
            return card(title, "Збір даних…", sub=f"Потрібна історія від 1 години, зараз {span:.1f} год")
        return card(title, "Збір даних…", sub="Немає вимірювань за 1-4 години до останнього (була перерва в даних)")
    return card(
        title, f"{trend['arrow']} {trend['text']}",
        sub=f"{trend['delta']:+.1f} hPa за {trend['hours']:g} год\n{trend['hint']}",
        color=trend["color"]
    )


def day_range(rows, key, unit, digits=2):
    since = time.time() - 86400
    values = [
        r[key] for r in rows
        if r.get("time", 0) >= since and isinstance(r.get(key), (int, float))
    ]
    if not values:
        return None
    return f"Мін {min(values):.{digits}f} · Макс {max(values):.{digits}f} {unit} за 24 год"


def bucketize(rows, since, keys, max_points=400, digits=2, candle_buckets=36):
    """Відбирає рядки за період, усереднює їх до ~max_points точок для лінії та формує свічки OHLC."""
    rows = [r for r in rows if r.get("time", 0) >= since]
    step = max(1, -(-len(rows) // max_points))
    out = {"t": [], "candles": {}, "min": {}, "max": {}}
    for key in keys:
        out[key] = []
        out["min"][key] = []
        out["max"][key] = []
    for i in range(0, len(rows), step):
        chunk = rows[i:i + step]
        out["t"].append(int(chunk[-1]["time"]))
        for key in keys:
            values = [r[key] for r in chunk if isinstance(r.get(key), (int, float))]
            out[key].append(round(sum(values) / len(values), digits) if values else None)

    # Коридор мін–макс: ковзне вікно ~1/30 періоду навколо кожної точки,
    # щоб смуга показувала реальний розкид, а не лише кілька сусідніх вимірів.
    if rows:
        times = [r.get("time", 0) for r in rows]
        half = max(300, (times[-1] - times[0]) / 60)
        for key in keys:
            vals = [(r.get(key) if isinstance(r.get(key), (int, float)) else None) for r in rows]
            for t in out["t"]:
                lo = bisect.bisect_left(times, t - half)
                hi = bisect.bisect_right(times, t + half)
                window = [v for v in vals[lo:hi] if v is not None]
                out["min"][key].append(round(min(window), digits) if window else None)
                out["max"][key].append(round(max(window), digits) if window else None)

    c_step = max(1, -(-len(rows) // candle_buckets))
    c_times = []
    for key in keys:
        out["candles"][key] = []
    prev_close = {}
    for i in range(0, len(rows), c_step):
        chunk = rows[i:i + c_step]
        c_times.append(int(chunk[-1]["time"]))
        for key in keys:
            vals = [r[key] for r in chunk if isinstance(r.get(key), (int, float))]
            if vals:
                o_val = prev_close.get(key, vals[0])
                c_val = vals[-1]
                h_val = max(vals + [o_val])
                l_val = min(vals + [o_val])
                prev_close[key] = c_val
                out["candles"][key].append({
                    "o": round(o_val, digits),
                    "h": round(h_val, digits),
                    "l": round(l_val, digits),
                    "c": round(c_val, digits)
                })
            else:
                out["candles"][key].append(None)
    out["candles"]["t"] = c_times
    return out


# ---------- кольори та картки ----------
GREEN, YELLOW, RED = "#5bffad", "#f1c40f", "#ff5c5c"


def clamp_pct(value):
    return max(0.0, min(100.0, float(value)))


def usage_color(pct):
    return GREEN if pct < 60 else YELLOW if pct < 85 else RED


def quality_color(pct):
    return GREEN if pct >= 60 else YELLOW if pct >= 35 else RED


def card(label, value, sub=None, pct=None, color=None, bar_color=None):
    """Картка для сторінки. pct (0-100) малює смужку під значенням."""
    bar = None
    if pct is not None:
        bar = {"pct": round(clamp_pct(pct), 1), "color": bar_color or color or GREEN}
    return {"label": label, "value": value, "sub": sub, "bar": bar, "color": color}


def rssi_quality(rssi):
    if rssi >= -70:
        return "Відмінний"
    if rssi >= -90:
        return "Добрий"
    if rssi >= -105:
        return "Помірний"
    return "Слабкий"


def snr_quality(snr):
    if snr >= 5:
        return "Відмінний"
    if snr >= 0:
        return "Добрий"
    if snr >= -7:
        return "Помірний"
    return "Слабкий"


def wifi_quality(dbm):
    if dbm >= -55:
        return "Відмінний"
    if dbm >= -67:
        return "Добрий"
    if dbm >= -75:
        return "Помірний"
    return "Слабкий"


def rad_level(usvh):
    if usvh is None:
        return "Очікування даних", None
    if usvh >= 0.30:
        return "🚨 ТРИВОГА! Вище 0.30 мкЗв/год", RED
    if usvh > 0.20:
        return "Підвищений фон", YELLOW
    return "Нормальний фон", GREEN


MQTT_HOST = "127.0.0.1"
MQTT_PORT = 1883
MQTT_USER = "example_mqtt_user"

TELEMETRY_FILE = "/home/example/mesh-monitor/telemetry.json"
HISTORY_FILE = "/home/example/mesh-monitor/history.json"
WEATHER_HISTORY_FILE = "/home/example/mesh-monitor/weather_history.json"
SYS_HISTORY_FILE = "/home/example/mesh-monitor/sys_history.json"
RAD_FILE = "/home/example/mesh-monitor/radiation.json"
RAD_HISTORY_FILE = "/home/example/mesh-monitor/radiation_history.json"
SYS_SAMPLE_SECONDS = 60       # как часто писать точку для графиков CPU/сети
SYS_SAVE_SECONDS = 1800       # как часто сохранять историю на microSD
SYS_HISTORY_MAX = 10080       # 7 суток по минуте
telemetry = {
    "temperature": "Нет данных",
    "humidity": "Нет данных",
    "pressure": "Нет данных",
    "iaq": "Нет данных",
    "updated": "Ожидание телеметрии"
}

try:
    with open(TELEMETRY_FILE, "r") as f:
        saved_telemetry = json.load(f)
    telemetry.update(saved_telemetry)
    for _k in ("temperature", "humidity", "pressure"):
        telemetry[_k] = round_text(telemetry[_k])
except (OSError, ValueError):
    pass

radiation = {
    "usvh": None,
    "avg3h": None,
    "cpm": None,
    "battery": None,
    "voltage": None,
    "rssi": None,
    "alarm": False,
    "updated": "Очікування даних"
}
try:
    with open(RAD_FILE, "r") as f:
        saved_rad = json.load(f)
    if isinstance(saved_rad, dict):
        radiation.update(saved_rad)
except (OSError, ValueError):
    pass

last_seen = 0
nodes = {}
net_speed = {"iface": None, "rx": None, "tx": None}
ygg_speed = {"rx": None, "tx": None, "rx_total": None, "tx_total": None}
sys_state = {"cpu_usage": None, "cpu_temp": None, "wifi": None}
sys_history = []
history = []
weather_history = []
rad_history = []

for filename, target in [
    (HISTORY_FILE, history),
    (WEATHER_HISTORY_FILE, weather_history),
    (SYS_HISTORY_FILE, sys_history),
    (RAD_HISTORY_FILE, rad_history)
]:
    try:
        with open(filename, "r") as f:
            saved = json.load(f)
        if isinstance(saved, list):
            target.extend(saved)
    except (OSError, ValueError):
        pass

# Последний принятый по радио пакет (RSSI/SNR) храним отдельно и сохраняем на диск,
# чтобы после перезапуска значения не пропадали.
RADIO_FILE = "/home/example/mesh-monitor/radio.json"
radio = {}
try:
    with open(RADIO_FILE, "r") as f:
        saved_radio = json.load(f)
    if isinstance(saved_radio, dict):
        radio.update(saved_radio)
except (OSError, ValueError):
    pass

# Первый запуск этой версии: берём последние известные значения из history.json
for item in history:
    node_key = item.get("node", "unknown")
    if node_key != "unknown":
        n = nodes.setdefault(node_key, {})
        for k in ("battery", "voltage"):
            if item.get(k) is not None:
                n[k] = item[k]
        n["updated"] = time.strftime("%H:%M:%S", time.localtime(item.get("time", 0)))
    if not radio.get("_from_file"):
        if item.get("rssi") is not None:
            radio["rssi"] = item["rssi"]
        if item.get("snr") is not None:
            radio["snr"] = item["snr"]
        if item.get("rssi") is not None or item.get("snr") is not None:
            radio["src"] = "—"
            radio["updated"] = time.strftime("%d.%m %H:%M:%S", time.localtime(item.get("time", 0)))

HTML = r"""
<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#070b10">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="manifest" href="/manifest.json">
<link rel="icon" type="image/png" href="/icon-192.png">
<link rel="apple-touch-icon" href="/icon-192.png">
<title>Mesh Monitor</title>
<style>
* {
    box-sizing: border-box;
}

body {
    margin: 0;
    padding: 20px;
    background: #070b10;
    color: #d8fbea;
    font-family: Arial, sans-serif;
    max-width: 1000px;
    margin-left: auto;
    margin-right: auto;
}

h1 {
    color: #5bffad;
    font-size: 30px;
    text-shadow: 0 0 14px rgba(91,255,173,.35);
    border-bottom: 1px solid #245441;
    padding-bottom: 16px;
}

h2 {
    color: #42d9ff;
    font-size: 19px;
}

p {
    color: #83a99a;
    font-size: 13px;
}

.card {
    background: linear-gradient(145deg, #14212a, #0c131a);
    border: 1px solid #20523f;
    border-radius: 14px;
    padding: 20px;
    margin: 12px 0;
    box-shadow: 0 0 16px rgba(0,255,150,.06);
    overflow-wrap: anywhere;
}

.card:hover {
    border-color: #5bffad;
}

.label {
    color: #42d9ff;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
}

.value {
    color: #5bffad;
    font-size: 25px;
    font-weight: bold;
    margin-top: 10px;
    text-shadow: 0 0 10px rgba(91,255,173,.18);
}

/* Смартфон: одна колонка. Компьютер: две колонки. */
.grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 16px;
}

.grid > .card {
    margin: 0;
    min-width: 0;
}

.card canvas {
    max-width: 100%;
    touch-action: pan-y;
}

.ctrl-row {
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    align-items: center;
    margin-top: 10px;
}

.ctrl-row select {
    margin-top: 0;
}

@media (min-width: 700px) {
    body {
        padding: 28px;
    }

    .grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .grid > .wide {
        grid-column: 1 / -1;
    }
}

@media (max-width: 480px) {
    body {
        padding: 12px;
    }

    h1 {
        font-size: 24px;
    }

    .card {
        padding: 16px;
    }

    .value {
        font-size: 22px;
    }
}

#cards {
    display: contents;
}

.section {
    grid-column: 1 / -1;
    margin-top: 10px;
    padding-bottom: 6px;
    color: #58d68d;
    font-size: 18px;
    font-weight: 700;
    letter-spacing: .5px;
    border-bottom: 1px solid #20523f;
}

.sub {
    color: #83a99a;
    font-size: 13px;
    margin-top: 8px;
    white-space: pre-line;
}

.bar {
    height: 6px;
    margin-top: 12px;
    background: #0b1117;
    border: 1px solid #20523f;
    border-radius: 4px;
    overflow: hidden;
}

.bar > span {
    display: block;
    height: 100%;
    border-radius: 3px;
    transition: width .4s;
}

#status.err {
    color: #ff5c5c;
}

select {
    margin-top: 10px;
    padding: 8px 10px;
    font-size: 14px;
    color: #d8fbea;
    background: #0c131a;
    border: 1px solid #20523f;
    border-radius: 8px;
}

.dl-row {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    margin-top: 14px;
}

.dl-btn {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 12px 20px;
    background: linear-gradient(135deg, #1b4d3e, #0f2d24);
    color: #5bffad;
    font-weight: bold;
    font-size: 15px;
    text-decoration: none;
    border: 1px solid #5bffad;
    border-radius: 10px;
    box-shadow: 0 0 12px rgba(91,255,173,.2);
    cursor: pointer;
    transition: transform .15s, box-shadow .15s, background .15s;
}

.dl-btn:hover {
    background: linear-gradient(135deg, #246652, #153c30);
    box-shadow: 0 0 18px rgba(91,255,173,.4);
    transform: translateY(-1px);
}

.dl-btn.secondary {
    background: linear-gradient(135deg, #162a38, #0d1821);
    color: #42d9ff;
    border-color: #42d9ff;
    box-shadow: 0 0 12px rgba(66,217,255,.15);
}

.dl-btn.secondary:hover {
    background: linear-gradient(135deg, #1e3a4d, #12222e);
    box-shadow: 0 0 18px rgba(66,217,255,.35);
}

.subtitle {
    color: #58d68d;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px 8px;
}

.subtitle .sub-item {
    white-space: nowrap;
}

@media (max-width: 699px) {
    .subtitle {
        flex-direction: column;
        align-items: flex-start;
        font-size: 17px;
        gap: 4px;
    }

    .subtitle .sub-sep {
        display: none;
    }
}
</style>
</head>
<body>
<h1>📡 Mesh Monitor</h1>
<h2 class="subtitle"><span class="sub-item">🌦️ Heltec V4 Weather Station</span><span class="sub-sep">·</span><span class="sub-item">☢️ Дозиметр СБМ-20</span></h2>
<p>Raspberry Pi 3A+ · живе оновлення кожні 15 с · <span id="status">завантаження…</span></p>

<div class="grid">
    <div id="cards"></div>

    <div class="section">📈 Графіки</div>

    <div class="card wide">
        <div class="label">📊 Період та вигляд графіків</div>
        <div class="ctrl-row">
            <select id="periodSelect">
                <option value="21600">Останні 6 годин</option>
                <option value="86400" selected>24 години</option>
                <option value="604800">7 днів</option>
            </select>
            <select id="modeSelect">
                <option value="auto" selected>✨ Авто — найкращий вигляд для кожного графіка</option>
                <option value="band">🌫️ Коридор мін–макс + середня лінія</option>
                <option value="candles">🕯️ Свічковий графік + лінія тренду (OHLC)</option>
                <option value="line">📈 Тільки плавна лінія</option>
            </select>
        </div>
        <div class="sub">✨ Авто: температура, вологість, IAQ і радіація за добу — коридор мін–макс; тиск — свічки; радіація за 3 год, CPU і мережа — лінія.
🌫️ Коридор — напівпрозора смуга показує, в яких межах коливалось значення, а лінія — середнє.
🟩🟨🟥 Кольорові зони на фоні (радіація, IAQ, CPU, вологість) — норма / підвищено / небезпечно.</div>
        <div class="dl-row" style="margin-top:10px;">
            <a id="csvRadBtn" class="dl-btn secondary" style="padding:7px 12px;font-size:13px;" href="/api/export.csv?type=radiation&period=86400">⬇️ CSV: Радіація</a>
            <a id="csvWeatherBtn" class="dl-btn secondary" style="padding:7px 12px;font-size:13px;" href="/api/export.csv?type=weather&period=86400">⬇️ CSV: Погода</a>
            <a id="csvSysBtn" class="dl-btn secondary" style="padding:7px 12px;font-size:13px;" href="/api/export.csv?type=system&period=86400">⬇️ CSV: Система Pi</a>
        </div>
    </div>

    <div class="card">
        <div class="label">🌡️ Температура °C</div>
        <canvas id="temperatureChart"></canvas>
    </div>

    <div class="card">
        <div class="label">💧 Вологість %</div>
        <canvas id="humidityChart"></canvas>
    </div>

    <div class="card">
        <div class="label">🌡️ Атмосферний тиск hPa</div>
        <canvas id="pressureChart"></canvas>
    </div>

    <div class="card">
        <div class="label">🌿 Якість повітря IAQ</div>
        <canvas id="iaqChart"></canvas>
    </div>

    <div class="card">
        <div class="label">☢️ Радіація за 3 години, мкЗв/год</div>
        <canvas id="rad3hChart"></canvas>
    </div>

    <div class="card">
        <div class="label">☢️ Радіація за добу (24 год), мкЗв/год</div>
        <canvas id="rad24hChart"></canvas>
    </div>

    <div class="card">
        <div class="label">🔥 Температура CPU °C</div>
        <canvas id="cpuChart"></canvas>
    </div>

    <div class="card">
        <div class="label">🌐 Мережа Pi, КБ/с</div>
        <canvas id="netChart"></canvas>
    </div>

    <div class="section">📱 Мобільний додаток Android</div>

    <div class="card wide">
        <div class="label">📲 Mesh Monitor для смартфона</div>
        <div class="value" style="font-size:20px;margin-top:8px;">Завантажити додаток або встановити на головний екран</div>
        <div class="sub">Нативний додаток Android (APK) автоматично перемикається між домашньою мережею Wi-Fi (192.168.0.x:8080), Tailscale VPN (100.x.x.x:8080) та хмарним доступом (your-node.example.ts.net).</div>
        <div class="dl-row">
            <a class="dl-btn" href="/MeshMonitor.apk" download="MeshMonitor.apk">⬇️ Завантажити MeshMonitor.apk (Android)</a>
            <button id="pwaInstallBtn" class="dl-btn secondary" style="display:none;" type="button">➕ Встановити Web-додаток (PWA)</button>
        </div>
    </div>
</div>

<script src="/static/chart.umd.min.js"></script>
<script src="/static/hammer.min.js"></script>
<script src="/static/chartjs-plugin-zoom.min.js"></script>
<script>
Chart.register(ChartZoom);

const REFRESH_MS = 15000;
const charts = {};
const cursor = {};
const isTouchDevice = ('ontouchstart' in window) || (navigator.maxTouchPoints > 0);
let data = {{ initial | tojson }};
let period = 86400;
let chartMode = 'auto';
let lastRequest = 0;

function modeFor(def) {
    return chartMode === 'auto' ? (def.auto || 'line') : chartMode;
}

// Кольорові зони норми на фоні графіка
Chart.register({
    id: 'thresholdZones',
    beforeDatasetsDraw(chart) {
        const def = chart.$def;
        if (!def || !def.zones) return;
        const area = chart.chartArea;
        const y = chart.scales.y;
        const ctx = chart.ctx;
        ctx.save();
        def.zones.forEach(z => {
            const yTop = z.to === null ? area.top : Math.max(area.top, y.getPixelForValue(z.to));
            const yBot = Math.min(area.bottom, y.getPixelForValue(z.from));
            if (yBot <= area.top || yTop >= area.bottom || yBot <= yTop) return;
            ctx.fillStyle = z.color;
            ctx.fillRect(area.left, yTop, area.right - area.left, yBot - yTop);
            if (z.to !== null && yTop > area.top + 2) {
                ctx.strokeStyle = z.line || 'rgba(255,255,255,0.25)';
                ctx.setLineDash([3, 4]);
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.moveTo(area.left, yTop);
                ctx.lineTo(area.right, yTop);
                ctx.stroke();
                ctx.setLineDash([]);
            }
        });
        ctx.restore();
    }
});

// Коридор мін–макс: заливка між мінімумом і максимумом навколо кожної точки
Chart.register({
    id: 'minMaxBand',
    beforeDatasetsDraw(chart) {
        const def = chart.$def;
        if (!def || modeFor(def) !== 'band') return;
        const source = data[def.group];
        if (!source || !source.min || !source.max) return;
        const x = chart.scales.x;
        const y = chart.scales.y;
        const area = chart.chartArea;
        const ctx = chart.ctx;
        ctx.save();
        ctx.beginPath();
        ctx.rect(area.left, area.top, area.right - area.left, area.bottom - area.top);
        ctx.clip();
        def.datasets.forEach((d, di) => {
            if (di > 0 && def.datasets.length > 1 && d.noBand) return;
            const mins = source.min[d.key] || [];
            const maxs = source.max[d.key] || [];
            const scale = d.scale || 1;
            const alpha = di === 0 ? '33' : '1f';
            let seg = [];
            const flush = () => {
                if (seg.length > 1) {
                    ctx.beginPath();
                    seg.forEach((p, k) => k ? ctx.lineTo(p.x, p.hi) : ctx.moveTo(p.x, p.hi));
                    for (let k = seg.length - 1; k >= 0; k--) ctx.lineTo(seg[k].x, seg[k].lo);
                    ctx.closePath();
                    ctx.fillStyle = d.color + alpha;
                    ctx.fill();
                    ctx.strokeStyle = d.color + '55';
                    ctx.lineWidth = 0.8;
                    ctx.beginPath();
                    seg.forEach((p, k) => k ? ctx.lineTo(p.x, p.hi) : ctx.moveTo(p.x, p.hi));
                    ctx.stroke();
                    ctx.beginPath();
                    seg.forEach((p, k) => k ? ctx.lineTo(p.x, p.lo) : ctx.moveTo(p.x, p.lo));
                    ctx.stroke();
                }
                seg = [];
            };
            for (let i = 0; i < mins.length; i++) {
                if (mins[i] == null || maxs[i] == null) { flush(); continue; }
                seg.push({
                    x: x.getPixelForValue(i),
                    lo: y.getPixelForValue(mins[i] * scale),
                    hi: y.getPixelForValue(maxs[i] * scale)
                });
            }
            flush();
        });
        ctx.restore();
    }
});

Chart.register({
    id: 'candlestickOverlay',
    beforeDatasetsDraw(chart) {
        const def = chart.$def;
        if (!def || modeFor(def) !== 'candles') return;
        const source = data[def.group];
        if (!source || !source.candles) return;
        const primary = def.datasets[0];
        const candles = source.candles[primary.key];
        if (!candles || !candles.length) return;

        const scale = primary.scale || 1;
        const area = chart.chartArea;
        const yScale = chart.scales.y;
        const ctx = chart.ctx;
        const n = candles.length;
        const width = area.right - area.left;
        const stepPx = width / Math.max(1, n);
        const bodyWidth = Math.max(3, Math.min(12, stepPx * 0.56));

        ctx.save();
        ctx.beginPath();
        ctx.rect(area.left, area.top, width, area.bottom - area.top);
        ctx.clip();

        candles.forEach((c, idx) => {
            if (!c) return;
            const x = n === 1 ? (area.left + width / 2) : (area.left + (idx + 0.5) * stepPx);
            const o = c.o * scale;
            const h = c.h * scale;
            const l = c.l * scale;
            const cl = c.c * scale;

            const yOpen = yScale.getPixelForValue(o);
            const yClose = yScale.getPixelForValue(cl);
            const yHigh = yScale.getPixelForValue(h);
            const yLow = yScale.getPixelForValue(l);

            const isUp = cl >= o;
            const wickColor = isUp ? '#5bffad' : '#ff6b6b';
            const fillColor = isUp ? 'rgba(91, 255, 173, 0.38)' : 'rgba(255, 107, 107, 0.38)';

            // Ґнот свічки (High - Low)
            ctx.strokeStyle = wickColor;
            ctx.lineWidth = 1.4;
            ctx.beginPath();
            ctx.moveTo(x, yHigh);
            ctx.lineTo(x, yLow);
            ctx.stroke();

            // Тіло свічки (Open - Close)
            const top = Math.min(yOpen, yClose);
            const bodyH = Math.max(2, Math.abs(yClose - yOpen));
            ctx.fillStyle = fillColor;
            ctx.fillRect(x - bodyWidth / 2, top, bodyWidth, bodyH);
            ctx.strokeRect(x - bodyWidth / 2, top, bodyWidth, bodyH);
        });
        ctx.restore();
    }
});

Chart.register({
    id: 'syncCursor',
    afterDraw(chart) {
        const index = cursor[chart.$group];
        if (index === undefined || index === null) return;
        const x = chart.scales.x.getPixelForValue(index);
        const area = chart.chartArea;
        if (x < area.left || x > area.right) return;

        const ctx = chart.ctx;
        ctx.save();
        ctx.beginPath();
        ctx.moveTo(x, area.top);
        ctx.lineTo(x, area.bottom);
        ctx.lineWidth = 1;
        ctx.strokeStyle = '#ffffff';
        ctx.setLineDash([4, 4]);
        ctx.stroke();
        ctx.restore();
    }
});

const Z_OK = 'rgba(91,255,173,0.07)';
const Z_WARN = 'rgba(241,196,15,0.10)';
const Z_BAD = 'rgba(255,92,92,0.13)';
const RAD_ZONES = [
    { from: 0, to: 0.20, color: Z_OK },
    { from: 0.20, to: 0.30, color: Z_WARN, line: 'rgba(241,196,15,0.5)' },
    { from: 0.30, to: null, color: Z_BAD, line: 'rgba(255,92,92,0.6)' }
];

const chartDefs = [
    { id: 'temperatureChart', group: 'weather', auto: 'band', datasets: [
        { key: 'temperature', label: 'Температура °C', color: '#58d68d' }] },
    { id: 'humidityChart', group: 'weather', auto: 'band',
      zones: [{ from: 40, to: 60, color: Z_OK, line: 'rgba(91,255,173,0.35)' }],
      datasets: [
        { key: 'humidity', label: 'Вологість % (зелена зона — комфорт 40–60%)', color: '#3498db' }] },
    { id: 'pressureChart', group: 'weather', auto: 'candles', datasets: [
        { key: 'pressure', label: 'Тиск hPa', color: '#f1c40f' }] },
    { id: 'iaqChart', group: 'weather', auto: 'band',
      zones: [
        { from: 0, to: 100, color: Z_OK },
        { from: 100, to: 200, color: Z_WARN, line: 'rgba(241,196,15,0.5)' },
        { from: 200, to: null, color: Z_BAD, line: 'rgba(255,92,92,0.6)' }],
      datasets: [
        { key: 'iaq', label: 'IAQ', color: '#e67e22' }] },
    { id: 'rad3hChart', group: 'rad3h', auto: 'line', yStep: 0.05, zones: RAD_ZONES, datasets: [
        { key: 'usvh', label: 'Поточний мкЗв/год', color: '#5bffad', digits: 3 },
        { key: 'avg3h', label: 'Середнє 3 год мкЗв/год', color: '#f1c40f', digits: 3, noBand: true }] },
    { id: 'rad24hChart', group: 'rad24h', auto: 'band', yStep: 0.05, zones: RAD_ZONES, datasets: [
        { key: 'usvh', label: 'Поточний мкЗв/год', color: '#42d9ff', digits: 3 },
        { key: 'avg3h', label: 'Середнє 3 год мкЗв/год', color: '#ff9f43', digits: 3, noBand: true }] },
    { id: 'cpuChart', group: 'system', auto: 'line',
      zones: [
        { from: 0, to: 60, color: Z_OK },
        { from: 60, to: 70, color: Z_WARN, line: 'rgba(241,196,15,0.5)' },
        { from: 70, to: null, color: Z_BAD, line: 'rgba(255,92,92,0.6)' }],
      datasets: [
        { key: 'cpu_temp', label: 'CPU °C', color: '#ff6b6b' }] },
    { id: 'netChart', group: 'system', auto: 'line', datasets: [
        { key: 'rx', label: 'Вхід КБ/с', color: '#5bffad', scale: 1 / 1024 },
        { key: 'tx', label: 'Вихід КБ/с', color: '#42d9ff', scale: 1 / 1024 }] }
];

function esc(value) {
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    return String(value == null ? '' : value).replace(/[&<>"']/g, ch => map[ch]);
}

function formatTime(t) {
    return new Date(t * 1000).toLocaleString('uk-UA', {
        day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
    });
}

function seriesValues(series, scale, digits) {
    if (!series) return [];
    const d = digits !== undefined ? digits : 2;
    return series.map(v => v == null ? null : Number((v * (scale || 1)).toFixed(d)));
}

function renderCards() {
    document.getElementById('cards').innerHTML = data.cards.map(c => {
        if (c.section) return '<div class="section">' + esc(c.section) + '</div>';
        const color = c.color ? ' style="color:' + esc(c.color) + '"' : '';
        const sub = c.sub ? '<div class="sub">' + esc(c.sub) + '</div>' : '';
        const bar = c.bar
            ? '<div class="bar"><span style="width:' + Number(c.bar.pct) +
              '%;background:' + esc(c.bar.color) + '"></span></div>'
            : '';
        return '<div class="card"><div class="label">' + esc(c.label) + '</div>' +
               '<div class="value"' + color + '>' + esc(c.value) + '</div>' + sub + bar + '</div>';
    }).join('');
}

function computeBounds(def, source) {
    if (!source) return {};
    let minVal = Infinity;
    let maxVal = -Infinity;
    def.datasets.forEach(d => {
        const scale = d.scale || 1;
        const arr = source[d.key] || [];
        arr.forEach(v => {
            if (typeof v === 'number') {
                const sv = v * scale;
                if (sv < minVal) minVal = sv;
                if (sv > maxVal) maxVal = sv;
            }
        });
        const mode = modeFor(def);
        if (mode === 'candles' && source.candles && source.candles[d.key]) {
            source.candles[d.key].forEach(c => {
                if (c) {
                    const l = c.l * scale;
                    const h = c.h * scale;
                    if (l < minVal) minVal = l;
                    if (h > maxVal) maxVal = h;
                }
            });
        }
        if (mode === 'band' && !d.noBand && source.min && source.max) {
            (source.min[d.key] || []).forEach(v => {
                if (typeof v === 'number' && v * scale < minVal) minVal = v * scale;
            });
            (source.max[d.key] || []).forEach(v => {
                if (typeof v === 'number' && v * scale > maxVal) maxVal = v * scale;
            });
        }
    });
    if (!isFinite(minVal) || !isFinite(maxVal)) return {};
    const pad = (maxVal - minVal) * 0.08 || (Math.abs(maxVal) * 0.05) || 0.1;
    return { suggestedMin: minVal - pad, suggestedMax: maxVal + pad };
}

function isNarrowScreen() {
    return window.innerWidth < 700;
}

function radStepFor(bounds) {
    return (bounds && bounds.suggestedMax && bounds.suggestedMax > 0.45) ? 0.1 : 0.05;
}

function applyResponsiveAxis(chart, def, bounds) {
    const narrow = isNarrowScreen();
    chart.options.aspectRatio = narrow ? 1.3 : 1.9;
    chart.options.scales.x.ticks.maxTicksLimit = narrow ? 6 : 8;
    chart.options.scales.x.ticks.font = { size: narrow ? 10 : 11 };
    const yTicks = chart.options.scales.y.ticks;
    yTicks.font = { size: narrow ? 10 : 12 };
    yTicks.padding = narrow ? 3 : 6;
    if (def.yStep) {
        yTicks.stepSize = radStepFor(bounds);
        yTicks.autoSkip = false;
        yTicks.maxTicksLimit = 12;
    } else {
        yTicks.maxTicksLimit = narrow ? 8 : 7;
        yTicks.autoSkipPadding = 2;
    }
}

function createChart(def, labels) {
    const canvas = document.getElementById(def.id);
    const bounds = computeBounds(def, data[def.group]);
    const narrow = isNarrowScreen();
    const chart = new Chart(canvas, {
        type: 'line',
        data: {
            labels: labels,
            datasets: def.datasets.map((d, i) => ({
                label: d.label,
                data: seriesValues((data[def.group] || {})[d.key], d.scale, d.digits),
                borderColor: d.color,
                backgroundColor: d.color + '18',
                borderWidth: i === 0 ? 2.2 : 1.8,
                pointStyle: 'line',
                fill: def.datasets.length === 1 && modeFor(def) === 'line' && !def.zones,
                tension: 0.35,
                pointRadius: modeFor(def) === 'line' ? 1.5 : 0,
                pointHoverRadius: 5,
                pointHitRadius: 24,
                spanGaps: true
            }))
        },
        options: {
            responsive: true,
            aspectRatio: narrow ? 1.3 : 1.9,
            events: isTouchDevice ? ['click', 'touchstart'] : ['mousemove', 'mouseout', 'click'],
            interaction: {
                mode: 'index',
                intersect: false
            },
            onHover: (event, elements, chart) => {
                if (event.type === 'mouseout') {
                    cursor[def.group] = null;
                } else {
                    const position = chart.scales.x.getValueForPixel(event.x);
                    cursor[def.group] = Math.max(0, Math.min(
                        chart.data.labels.length - 1,
                        Math.round(position)
                    ));
                }
                Object.values(charts)
                    .filter(c => c.$group === def.group)
                    .forEach(c => c.draw());
            },
            plugins: {
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    backgroundColor: 'rgba(12, 19, 26, 0.94)',
                    borderColor: '#20523f',
                    borderWidth: 1,
                    titleColor: '#42d9ff',
                    bodyColor: '#d8fbea',
                    callbacks: {
                        afterBody(items) {
                            if (!items || !items.length) return '';
                            const idx = items[0].dataIndex;
                            const src = data[def.group];
                            if (!src || modeFor(def) !== 'band' || !src.min || !src.max) return '';
                            const d0 = def.datasets[0];
                            const mn = (src.min[d0.key] || [])[idx];
                            const mx = (src.max[d0.key] || [])[idx];
                            if (mn == null || mx == null) return '';
                            const sc = d0.scale || 1;
                            const dg = d0.digits !== undefined ? d0.digits : 2;
                            return 'Діапазон: ' + (mn * sc).toFixed(dg) + ' – ' + (mx * sc).toFixed(dg);
                        }
                    }
                },
                legend: {
                    labels: {
                        color: '#ffffff',
                        usePointStyle: true,
                        pointStyle: 'line',
                        pointStyleWidth: narrow ? 20 : 28,
                        boxHeight: 2,
                        font: { size: narrow ? 11 : 12 }
                    }
                },
                zoom: {
                    pan: { enabled: !isTouchDevice, mode: 'x' },
                    zoom: {
                        wheel: { enabled: !isTouchDevice },
                        pinch: { enabled: false },
                        mode: 'x'
                    }
                }
            },
            scales: {
                x: {
                    ticks: { color: '#aaaaaa', maxTicksLimit: narrow ? 6 : 8, font: { size: narrow ? 10 : 11 } },
                    grid: { color: '#333333' }
                },
                y: {
                    suggestedMin: bounds.suggestedMin,
                    suggestedMax: bounds.suggestedMax,
                    ticks: {
                        color: '#aaaaaa',
                        font: { size: narrow ? 10 : 12 },
                        padding: narrow ? 3 : 6,
                        autoSkip: def.yStep ? false : true,
                        autoSkipPadding: def.yStep ? 0 : 2,
                        maxTicksLimit: def.yStep ? 12 : (narrow ? 8 : 7),
                        stepSize: def.yStep ? radStepFor(bounds) : undefined,
                        callback: def.yStep ? (v => Number(Number(v).toFixed(2))) : undefined
                    },
                    grid: { color: '#333333' }
                }
            }
        }
    });
    chart.$group = def.group;
    chart.$def = def;
    canvas.addEventListener('dblclick', () => chart.resetZoom());
    return chart;
}

function updateCharts() {
    chartDefs.forEach(def => {
        const source = data[def.group] || { t: [] };
        const labels = (source.t || []).map(formatTime);
        const chart = charts[def.id];
        if (!chart) {
            charts[def.id] = createChart(def, labels);
            return;
        }
        if (typeof chart.isZoomedOrPanned === 'function' && chart.isZoomedOrPanned()) return;
        const bounds = computeBounds(def, source);
        chart.options.scales.y.suggestedMin = bounds.suggestedMin;
        chart.options.scales.y.suggestedMax = bounds.suggestedMax;
        applyResponsiveAxis(chart, def, bounds);
        chart.data.labels = labels;
        def.datasets.forEach((d, i) => {
            chart.data.datasets[i].data = seriesValues(source[d.key], d.scale, d.digits);
            chart.data.datasets[i].fill = def.datasets.length === 1 && modeFor(def) === 'line' && !def.zones;
            chart.data.datasets[i].pointRadius = modeFor(def) === 'line' ? 1.5 : 0;
        });
        chart.update('none');
    });
}

let resizeTimer = null;
window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
        chartDefs.forEach(def => {
            const chart = charts[def.id];
            if (!chart) return;
            const bounds = computeBounds(def, data[def.group]);
            applyResponsiveAxis(chart, def, bounds);
            chart.resize();
            chart.update('none');
        });
    }, 150);
});

function setStatus(text, isError) {
    const el = document.getElementById('status');
    el.textContent = text;
    el.className = isError ? 'err' : '';
}

async function refresh() {
    const requestId = ++lastRequest;
    try {
        const response = await fetch('/api/data?period=' + period, { cache: 'no-store' });
        if (!response.ok) throw new Error('HTTP ' + response.status);
        const fresh = await response.json();
        if (requestId !== lastRequest) return;
        data = fresh;
        renderCards();
        updateCharts();
        setStatus('оновлено ' + data.server_time, false);
    } catch (e) {
        if (requestId === lastRequest) setStatus("немає зв'язку з Pi", true);
    }
}

function updateCsvLinks() {
    const r = document.getElementById('csvRadBtn');
    const w = document.getElementById('csvWeatherBtn');
    const s = document.getElementById('csvSysBtn');
    if (r) r.href = '/api/export.csv?type=radiation&period=' + period;
    if (w) w.href = '/api/export.csv?type=weather&period=' + period;
    if (s) s.href = '/api/export.csv?type=system&period=' + period;
}

const select = document.getElementById('periodSelect');
select.addEventListener('change', () => {
    period = Number(select.value);
    try { localStorage.setItem('meshPeriod', String(period)); } catch (e) {}
    updateCsvLinks();
    Object.values(charts).forEach(c => c.resetZoom());
    refresh();
});

const modeSelect = document.getElementById('modeSelect');
if (modeSelect) {
    modeSelect.addEventListener('change', () => {
        chartMode = modeSelect.value;
        try { localStorage.setItem('meshChartMode2', chartMode); } catch (e) {}
        updateCharts();
    });
}

try {
    const saved = localStorage.getItem('meshPeriod');
    if (saved && Array.from(select.options).some(o => o.value === saved)) {
        select.value = saved;
        period = Number(saved);
    }
    const savedMode = localStorage.getItem('meshChartMode2');
    if (savedMode && modeSelect && Array.from(modeSelect.options).some(o => o.value === savedMode)) {
        modeSelect.value = savedMode;
        chartMode = savedMode;
    }
} catch (e) {}

renderCards();
updateCharts();
updateCsvLinks();
setStatus('оновлено ' + data.server_time, false);
if (period !== 86400) refresh();

setInterval(refresh, REFRESH_MS);
document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refresh();
});

let deferredPrompt = null;
const pwaBtn = document.getElementById('pwaInstallBtn');
if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
}
window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    if (pwaBtn) pwaBtn.style.display = 'inline-flex';
});
if (pwaBtn) {
    pwaBtn.addEventListener('click', async () => {
        if (!deferredPrompt) return;
        deferredPrompt.prompt();
        await deferredPrompt.userChoice;
        deferredPrompt = null;
        pwaBtn.style.display = 'none';
    });
}
</script>
</body>
</html>
"""


def default_interface():
    """Мережевий інтерфейс із маршрутом за замовчуванням (wlan0/eth0), без tailscale0 тощо."""
    try:
        with open("/proc/net/route", "r") as f:
            next(f, None)
            for line in f:
                parts = line.split()
                if len(parts) > 1 and parts[1] == "00000000":
                    return parts[0]
    except OSError:
        pass
    return None


def read_net_bytes(iface):
    """(прийнято байт, відправлено байт) для інтерфейсу з /proc/net/dev."""
    try:
        with open("/proc/net/dev", "r") as f:
            for line in f:
                name, _, data = line.partition(":")
                if name.strip() == iface:
                    fields = data.split()
                    return int(fields[0]), int(fields[8])
    except (OSError, ValueError, IndexError):
        pass
    return None


def speed_text(value):
    return "Збір даних…" if value is None else f"{fmt_size(value)}/с"


def sampler(interval=5):
    """Фоновий потік: швидкість мережі, CPU, Wi-Fi; раз на хвилину пише точку в sys_history."""
    prev_net = None
    prev_ygg = None
    prev_cpu = read_cpu_times()
    acc = {"rx": 0.0, "tx": 0.0, "n": 0}
    next_point = time.time() + SYS_SAMPLE_SECONDS
    last_save = time.time()

    while True:
        now = time.time()

        iface = default_interface()
        counters = read_net_bytes(iface) if iface else None
        if counters is None:
            net_speed.update(iface=iface, rx=None, tx=None)
            prev_net = None
        else:
            if prev_net and prev_net["iface"] == iface and now > prev_net["time"]:
                dt = now - prev_net["time"]
                net_speed.update(
                    iface=iface,
                    rx=max(0, counters[0] - prev_net["rx"]) / dt,
                    tx=max(0, counters[1] - prev_net["tx"]) / dt
                )
            prev_net = {"iface": iface, "time": now, "rx": counters[0], "tx": counters[1]}

        ygg_cnt = read_net_bytes("tun0")
        if ygg_cnt is None:
            ygg_speed.update(rx=None, tx=None, rx_total=None, tx_total=None)
            prev_ygg = None
        else:
            ygg_speed["rx_total"], ygg_speed["tx_total"] = ygg_cnt
            if prev_ygg and now > prev_ygg["time"]:
                dt = now - prev_ygg["time"]
                ygg_speed["rx"] = max(0, ygg_cnt[0] - prev_ygg["rx"]) / dt
                ygg_speed["tx"] = max(0, ygg_cnt[1] - prev_ygg["tx"]) / dt
            prev_ygg = {"time": now, "rx": ygg_cnt[0], "tx": ygg_cnt[1]}

        cpu_now = read_cpu_times()
        if cpu_now and prev_cpu and cpu_now[1] > prev_cpu[1]:
            idle_delta = cpu_now[0] - prev_cpu[0]
            total_delta = cpu_now[1] - prev_cpu[1]
            sys_state["cpu_usage"] = clamp_pct((1 - idle_delta / total_delta) * 100)
        prev_cpu = cpu_now
        sys_state["cpu_temp"] = read_cpu_temp()
        sys_state["wifi"] = read_wifi()

        if net_speed["rx"] is not None:
            acc["rx"] += net_speed["rx"]
            acc["tx"] += net_speed["tx"]
            acc["n"] += 1

        if now >= next_point:
            next_point = now + SYS_SAMPLE_SECONDS
            temp = sys_state["cpu_temp"]
            sys_history.append({
                "time": int(now),
                "cpu_temp": round(temp, 1) if temp is not None else None,
                "rx": round(acc["rx"] / acc["n"]) if acc["n"] else None,
                "tx": round(acc["tx"] / acc["n"]) if acc["n"] else None
            })
            sys_history[:] = sys_history[-SYS_HISTORY_MAX:]
            acc.update(rx=0.0, tx=0.0, n=0)
            if now - last_save >= SYS_SAVE_SECONDS:
                last_save = now
                save_json(SYS_HISTORY_FILE, sys_history)

        time.sleep(interval)


def pi_cards():
    cards = []
    mqtt_state = service_state("mosquitto")
    cards.append(card("Mosquitto MQTT", mqtt_state, color=GREEN if mqtt_state == "active" else RED))

    rns_state = service_state("rnsd")
    rns = rns_info() if rns_state == "active" else None
    if rns:
        rns_sub = (
            f"Інтерфейсів Up: {rns['up']}/{rns['total']} (Yggdrasil: {rns['ygg_up']})\n"
            f"Трафік RNS: ↓ {fmt_size(rns['rx'])} · ↑ {fmt_size(rns['tx'])}"
        )
    else:
        rns_sub = None
    cards.append(card("Reticulum rnsd", rns_state, sub=rns_sub, color=GREEN if rns_state == "active" else RED))

    ygg_state = service_state("yggdrasil")
    ygg = yggdrasil_info() if ygg_state == "active" else None
    y_rx_s = speed_text(ygg_speed["rx"]) if ygg_speed["rx"] is not None else "0 Б/с"
    y_tx_s = speed_text(ygg_speed["tx"]) if ygg_speed["tx"] is not None else "0 Б/с"
    if ygg:
        lat_str = f" · пінг {ygg['min_lat']:.0f} мс" if ygg.get("min_lat") else ""
        tot_rx = ygg_speed["rx_total"] if ygg_speed["rx_total"] is not None else ygg["rx"]
        tot_tx = ygg_speed["tx_total"] if ygg_speed["tx_total"] is not None else ygg["tx"]
        ygg_sub = (
            f"Пірів онлайн: {ygg['up']}/{ygg['total']}{lat_str}\n"
            f"Швидкість: ↓ {y_rx_s} · ↑ {y_tx_s}\n"
            f"Всього (tun0): ↓ {fmt_size(tot_rx)} · ↑ {fmt_size(tot_tx)}"
        )
        ygg_val = f"active · {ygg['up']} peers"
    else:
        ygg_sub = f"tun0: ↓ {y_rx_s} · ↑ {y_tx_s}" if ygg_speed["rx_total"] is not None else None
        ygg_val = ygg_state
    cards.append(card("🌐 Yggdrasil Network", ygg_val, sub=ygg_sub, color=GREEN if ygg_state == "active" else RED))

    temp = sys_state["cpu_temp"]
    if temp is None:
        cards.append(card("Температура CPU", "Недоступно"))
    else:
        color = GREEN if temp < 60 else YELLOW if temp < 70 else RED
        cards.append(card("Температура CPU", f"{temp:.1f} °C", pct=temp / 85 * 100, color=color))

    mem = memory_info()
    if mem:
        used, total = mem
        pct = used / total * 100
        cards.append(card(
            "RAM", f"{used / 1048576:.0f} МБ / {total / 1048576:.0f} МБ",
            sub=f"{pct:.0f}% використано", pct=pct, bar_color=usage_color(pct)
        ))
    else:
        cards.append(card("RAM", "Недоступно"))

    cpu = sys_state["cpu_usage"]
    if cpu is None:
        cards.append(card("CPU Usage", "Збір даних…"))
    else:
        cards.append(card("CPU Usage", f"{cpu:.1f}%", pct=cpu, bar_color=usage_color(cpu)))

    disk = disk_info("/")
    if disk:
        free, total = disk
        used_pct = (total - free) / total * 100
        cards.append(card(
            "💾 Вільне місце на microSD", f"{fmt_size(free)} вільно",
            sub=f"з {fmt_size(total)} · використано {used_pct:.0f}%",
            pct=used_pct, bar_color=usage_color(used_pct)
        ))
    else:
        cards.append(card("💾 Вільне місце на microSD", "Недоступно"))

    iface = net_speed["iface"]
    note = f" ({iface})" if iface else ""
    rx_val = speed_text(net_speed["rx"])
    tx_val = speed_text(net_speed["tx"])
    if net_speed["rx"] is None or net_speed["tx"] is None:
        traf_val = "Збір даних…"
        traf_sub = None
    else:
        traf_val = f"↓ {rx_val} · ↑ {tx_val}"
        traf_sub = f"Вхідний: {rx_val} · Вихідний: {tx_val}"
    cards.append(card(f"🌐 Трафік мережі{note}", traf_val, sub=traf_sub))

    wifi = sys_state["wifi"]
    if wifi:
        name, dbm = wifi
        pct = clamp_pct(2 * (dbm + 100))
        cards.append(card(
            "📶 Wi-Fi сигнал Pi", f"{dbm:.0f} dBm",
            sub=f"{wifi_quality(dbm)} · {name}", pct=pct, bar_color=quality_color(pct)
        ))
    else:
        cards.append(card("📶 Wi-Fi сигнал Pi", "Недоступно", sub="Wi-Fi не використовується"))

    cards.append(card("Час роботи", uptime_text()))
    return cards


def node_cards():
    cards = []
    for node_id, node in list(nodes.items())[-3:]:
        battery = node.get("battery")
        voltage = node.get("voltage")
        parts = []
        pct = None
        if isinstance(battery, (int, float)):
            if battery > 100:
                parts.append("Зовнішнє живлення")
            else:
                parts.append(f"{battery:.0f}%")
                pct = battery
        if isinstance(voltage, (int, float)):
            parts.append(f"{voltage:.2f} V")
        cards.append(card(
            f"🔋 Вузол {node_id}", " · ".join(parts) or "—",
            sub=f"Останній зв'язок: {node.get('updated', '—')}",
            pct=pct, bar_color=quality_color(pct) if pct is not None else None
        ))

    rssi = radio.get("rssi")
    snr = radio.get("snr")
    heard = f"від {radio.get('src', '—')} · {radio.get('updated', '—')}" if radio else "ще не було пакетів"
    if isinstance(rssi, (int, float)):
        pct = (rssi + 120) / 90 * 100
        cards.append(card(
            "📻 Радіо · RSSI", f"{rssi:.0f} dBm",
            sub=f"{rssi_quality(rssi)} · {heard}", pct=pct, bar_color=quality_color(pct)
        ))
    else:
        cards.append(card("📻 Радіо · RSSI", "—", sub=heard))
    if isinstance(snr, (int, float)):
        pct = (snr + 20) / 30 * 100
        cards.append(card(
            "📻 Радіо · SNR", f"{snr:.2f} dB",
            sub=snr_quality(snr), pct=pct, bar_color=quality_color(pct)
        ))
    else:
        cards.append(card("📻 Радіо · SNR", "—", sub=heard))

    cards.append(card("🕒 Остання телеметрія", telemetry["updated"]))
    return cards


def weather_cards(rows):
    cards = [
        card("Температура повітря", telemetry["temperature"], sub=day_range(rows, "temperature", "°C")),
        card("Вологість", telemetry["humidity"], sub=day_range(rows, "humidity", "%")),
        card("Тиск", telemetry["pressure"], sub=day_range(rows, "pressure", "hPa")),
        barometer_card(rows),
    ]

    iaq = last_value(rows, "iaq")
    if iaq is None:
        cards.append(card("Якість повітря IAQ", telemetry["iaq"]))
    else:
        label, color = iaq_level(iaq)
        cards.append(card("Якість повітря IAQ", telemetry["iaq"], sub=label, color=color))

    dp_row = next(
        (r for r in reversed(rows)
         if isinstance(r.get("temperature"), (int, float))
         and isinstance(r.get("humidity"), (int, float))),
        None
    )
    dp = dew_point(dp_row["temperature"], dp_row["humidity"]) if dp_row else None
    if dp is None:
        cards.append(card("Точка роси", "Нет данных"))
    else:
        cards.append(card("Точка роси", f"{dp:.2f} °C", sub=dew_comfort(dp)))
    return cards


def radiation_cards(rows):
    usvh = radiation.get("usvh")
    avg3h = radiation.get("avg3h")
    cpm = radiation.get("cpm")
    bat = radiation.get("battery")
    volt = radiation.get("voltage")
    rssi = radiation.get("rssi")
    updated = radiation.get("updated", "Очікування даних")

    status_text, rad_col = rad_level(usvh)
    range_sub = day_range(rows, "usvh", "мкЗв/год", digits=3)
    sub_now = "\n".join(filter(None, [status_text, range_sub]))
    now_pct = (usvh / 0.30) * 100 if isinstance(usvh, (int, float)) else None

    _, avg_col = rad_level(avg3h)
    avg_pct = (avg3h / 0.30) * 100 if isinstance(avg3h, (int, float)) else None

    cards = [
        card(
            "☢️ Поточний фон",
            f"{usvh:.3f} мкЗв/год" if isinstance(usvh, (int, float)) else "Очікування даних",
            sub=sub_now, pct=now_pct, color=rad_col, bar_color=rad_col
        ),
        card(
            "📊 Середнє за 3 години",
            f"{avg3h:.3f} мкЗв/год" if isinstance(avg3h, (int, float)) else "Очікування даних",
            sub="Усереднення за останні 180 хв", pct=avg_pct, color=avg_col, bar_color=avg_col
        ),
        card(
            "⚡ Імпульси (CPM)",
            f"{int(round(cpm))} імп/хв" if isinstance(cpm, (int, float)) else "—",
            sub=day_range(rows, "cpm", "CPM", digits=0) or "Трубка СБМ-20 (GGreg20)"
        ),
    ]

    bat_parts = []
    bat_pct = None
    if isinstance(bat, (int, float)):
        bat_parts.append(f"{int(round(bat))}%")
        bat_pct = bat
    if isinstance(volt, (int, float)):
        bat_parts.append(f"{volt:.2f} V")
    cards.append(card(
        "🔋 Батарея дозиметра",
        " · ".join(bat_parts) or "—",
        sub="TTGO T-Display LiPo",
        pct=bat_pct,
        bar_color=quality_color(bat_pct) if bat_pct is not None else None
    ))

    if isinstance(rssi, (int, float)):
        w_pct = clamp_pct(2 * (rssi + 100))
        cards.append(card(
            "📶 Wi-Fi дозиметра",
            f"{int(round(rssi))} dBm",
            sub=wifi_quality(rssi),
            pct=w_pct,
            bar_color=quality_color(w_pct)
        ))
    else:
        cards.append(card("📶 Wi-Fi дозиметра", "—", sub="Очікування пакета MQTT"))

    cards.append(card("🕒 Останнє оновлення радіації", updated, sub="Топік: geiger/telemetry"))
    return cards


def build_cards():
    """Порядок на сторінці: Raspberry Pi -> нода Meshtastic -> погода -> радіація."""
    rows = list(weather_history)
    rad_rows = list(rad_history)
    return (
        [{"section": "🖥️ Raspberry Pi"}] + pi_cards()
        + [{"section": "📻 Нода Meshtastic"}] + node_cards()
        + [{"section": "🌦️ Погода"}] + weather_cards(rows)
        + [{"section": "☢️ Радіація (СБМ-20)"}] + radiation_cards(rad_rows)
    )


PERIODS = (21600, 86400, 604800)
DEFAULT_PERIOD = 86400


def parse_period(value):
    try:
        period = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PERIOD
    return period if period in PERIODS else DEFAULT_PERIOD


def build_payload(period):
    now = time.time()
    since = now - period
    rad_list = list(rad_history)
    return {
        "cards": build_cards(),
        "weather": bucketize(list(weather_history), since, ("temperature", "humidity", "pressure", "iaq")),
        "rad3h": bucketize(rad_list, now - 3 * 3600, ("usvh", "avg3h", "cpm"), max_points=180, digits=3),
        "rad24h": bucketize(rad_list, now - 86400, ("usvh", "avg3h", "cpm"), max_points=400, digits=3),
        "system": bucketize(list(sys_history), since, ("cpu_temp", "rx", "tx")),
        "period": period,
        "server_time": time.strftime("%H:%M:%S")
    }


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("MQTT connected", flush=True)
        client.subscribe("msh/+/+/json/#")
        client.subscribe("geiger/#")
    else:
        print(f"MQTT connect failed, rc={rc} (5 = неверный логин/пароль)", flush=True)


def handle_geiger_message(topic, payload):
    now = time.time()
    usvh = payload.get("usvh")
    avg3h = payload.get("avg3h")
    cpm = payload.get("cpm")
    bat = payload.get("battery")
    volt = payload.get("voltage")
    rssi = payload.get("rssi")
    alarm = bool(payload.get("alarm", False))

    if isinstance(usvh, (int, float)):
        radiation["usvh"] = round(float(usvh), 4)
    if isinstance(avg3h, (int, float)):
        radiation["avg3h"] = round(float(avg3h), 4)
    if isinstance(cpm, (int, float)):
        radiation["cpm"] = float(cpm)
    if isinstance(bat, (int, float)):
        radiation["battery"] = int(round(bat))
    if isinstance(volt, (int, float)):
        radiation["voltage"] = round(float(volt), 2)
    if isinstance(rssi, (int, float)):
        radiation["rssi"] = int(round(rssi))
    radiation["alarm"] = alarm
    radiation["updated"] = time.strftime("%H:%M:%S")

    save_json(RAD_FILE, radiation)

    if isinstance(usvh, (int, float)) or isinstance(cpm, (int, float)):
        rad_history.append({
            "time": int(now),
            "usvh": radiation["usvh"],
            "avg3h": radiation["avg3h"],
            "cpm": radiation["cpm"]
        })
        rad_history[:] = rad_history[-10080:]
        save_json(RAD_HISTORY_FILE, rad_history)

    print(
        f"GEIGER {topic} usvh={radiation['usvh']} avg3h={radiation['avg3h']} "
        f"cpm={radiation['cpm']} bat={radiation['battery']}%",
        flush=True
    )


def on_message(client, userdata, msg):
    global last_seen
    try:
        if msg.topic.startswith("geiger/"):
            payload = json.loads(msg.payload.decode("utf-8"))
            if isinstance(payload, dict):
                handle_geiger_message(msg.topic, payload)
            return

        if "/json/" not in msg.topic:
            return
        payload = json.loads(msg.payload.decode("utf-8"))
        if not isinstance(payload, dict):
            return
        print(
            f"MQTT {msg.topic} type={payload.get('type')} from={payload.get('from')} "
            f"rssi={payload.get('rssi')} snr={payload.get('snr')}",
            flush=True
        )

        def find_value(obj, keys):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if key.lower() in keys and isinstance(value, (int, float)):
                        return value
                for value in obj.values():
                    result = find_value(value, keys)
                    if result is not None:
                        return result
            elif isinstance(obj, list):
                for value in obj:
                    result = find_value(value, keys)
                    if result is not None:
                        return result
            return None

        values = {
            "temperature": find_value(payload, {"temperature", "temp"}),
            "humidity": find_value(payload, {"relative_humidity", "humidity"}),
            "pressure": find_value(payload, {"barometric_pressure", "pressure"}),
            "iaq": find_value(payload, {"iaq", "air_quality_index"})
        }

        # "!xxxxxxxx" в топике — шлюз (ваша нода, выгружающая в MQTT); "from" в JSON — кто создал пакет.
        gateway_id = next(
            (p for p in msg.topic.split("/") if p.startswith("!")),
            None
        ) or payload.get("sender")
        sender = payload.get("from")
        from_id = node_hex(sender) if isinstance(sender, int) else None
        is_own = from_id is None or from_id == gateway_id

        battery = find_value(payload, {
            "batterylevel", "battery_level"
        })
        # Телеметрия бывает двух видов: параметры устройства (батарея) и данные датчиков (температура и т.д.).
        # "voltage" есть в обоих, и значение из пакета датчиков затирало напряжение батареи — берём только из пакета устройства.
        inner = payload.get("payload")
        is_sensor_packet = isinstance(inner, dict) and any(
            k in inner for k in (
                "temperature", "relative_humidity", "barometric_pressure",
                "gas_resistance", "iaq", "lux", "current"
            )
        )
        voltage = None if is_sensor_packet else find_value(payload, {
            "voltage", "battery_voltage"
        })
        if voltage is not None and voltage <= 0:
            voltage = None  # 0 В = нода не измеряет напряжение (например, питание по USB без батареи)
        if payload.get("type") == "telemetry":
            print(
                f"TELEMETRY from={from_id} keys={sorted(inner) if isinstance(inner, dict) else None} "
                f"battery={battery} voltage={voltage}",
                flush=True
            )
        # RSSI/SNR берём только с верхнего уровня пакета (во вложенных neighborinfo/traceroute чужие snr).
        # Прошивка добавляет их только для пакетов, принятых по радио.
        rssi = payload.get("rssi", payload.get("rx_rssi"))
        snr = payload.get("snr", payload.get("rx_snr"))
        if not isinstance(rssi, (int, float)):
            rssi = None
        if not isinstance(snr, (int, float)):
            snr = None

        # Радио: сигнал последнего принятого пакета (от любой ноды, которую слышит ваша).
        radio_changed = False
        if rssi is not None and -200 <= float(rssi) <= 0:
            radio["rssi"] = rssi
            radio_changed = True
        if snr is not None and -30 <= float(snr) <= 30:
            radio["snr"] = snr
            radio_changed = True
        if radio_changed:
            radio["src"] = from_id or "—"
            radio["updated"] = time.strftime("%d.%m %H:%M:%S")
            radio["_from_file"] = True
            try:
                with open(RADIO_FILE, "w") as f:
                    json.dump(radio, f)
            except OSError as e:
                print(f"Radio save error: {e}", flush=True)

        # Батарея/напряжение — только из пакетов самой вашей ноды, чтобы чужие не перезаписывали.
        if gateway_id and is_own:
            node = nodes.setdefault(gateway_id, {})
            if battery is not None:
                node["battery"] = battery
            if voltage is not None:
                node["voltage"] = voltage
            node["updated"] = time.strftime("%H:%M:%S")

            history.append({
                "time": time.time(),
                "node": gateway_id,
                "battery": battery,
                "voltage": voltage,
                "rssi": rssi,
                "snr": snr
            })
            history[:] = history[-500:]

            try:
                with open(HISTORY_FILE, "w") as f:
                    json.dump(history, f)
            except OSError as e:
                print(f"History save error: {e}", flush=True)
        if any(v is not None for v in values.values()):
            if values["temperature"] is not None:
                telemetry["temperature"] = f'{values["temperature"]:.2f} °C'
            if values["humidity"] is not None:
                telemetry["humidity"] = f'{values["humidity"]:.2f} %'
            if values["pressure"] is not None:
                telemetry["pressure"] = f'{values["pressure"]:.2f} hPa'
            if values["iaq"] is not None:
                telemetry["iaq"] = str(values["iaq"])
            telemetry["updated"] = time.strftime("%H:%M:%S")
            weather_history.append({
                  "time": time.time(),
                  "temperature": values["temperature"],
                  "humidity": values["humidity"],
                  "pressure": values["pressure"],
                  "iaq": values["iaq"]
              })
            weather_history[:] = weather_history[-10080:]
            try:
                with open(WEATHER_HISTORY_FILE, "w") as f:
                    json.dump(weather_history, f)
            except OSError as e:
                  print(f"Weather history save error: {e}", flush=True)
            try:
                with open(TELEMETRY_FILE, "w") as f:
                    json.dump(telemetry, f, ensure_ascii=False)
            except OSError as e:
                print(f"Telemetry save error: {e}", flush=True)
            last_seen = time.time()
    except Exception as e:
        print(f"MQTT message error: {e}", flush=True)

def mqtt_worker():
    client = mqtt.Client()
    client.username_pw_set(MQTT_USER, os.environ.get("MQTT_PASSWORD", ""))
    client.on_connect = on_connect
    client.on_message = on_message
    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            client.loop_forever()
        except Exception:
            time.sleep(5)

@app.route("/")
def index():
    return render_template_string(HTML, initial=build_payload(DEFAULT_PERIOD))


@app.route("/api/data")
def api_data():
    response = jsonify(build_payload(parse_period(request.args.get("period"))))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/notify")
def api_notify():
    """Компактні дані для сповіщення та віджета Android."""
    usvh = radiation.get("usvh")
    status_text, _ = rad_level(usvh)
    rows = list(weather_history)
    trend = pressure_trend(rows)
    pressure = last_value(rows, "pressure")
    temp = last_value(rows, "temperature")
    hum = last_value(rows, "humidity")
    cpu_t = sys_state.get("cpu_temp")
    rad_bat = radiation.get("battery")
    node_bats = [
        n.get("battery") for n in nodes.values()
        if isinstance(n.get("battery"), (int, float)) and n.get("battery") <= 100
    ]
    min_node_bat = min(node_bats) if node_bats else None
    low_bat = None
    if isinstance(rad_bat, (int, float)) and rad_bat < 15:
        low_bat = int(round(rad_bat))
    elif min_node_bat is not None and min_node_bat < 15:
        low_bat = int(round(min_node_bat))

    storm = bool(trend and trend.get("delta") is not None and trend["delta"] <= -3.5)
    cpu_hot = bool(isinstance(cpu_t, (int, float)) and cpu_t >= 75.0)
    out = {
        "usvh": usvh,
        "avg3h": radiation.get("avg3h"),
        "rad_status": status_text,
        "alarm": isinstance(usvh, (int, float)) and usvh >= 0.30,
        "storm_alert": storm,
        "cpu_alert": cpu_hot,
        "bat_alert": low_bat is not None,
        "low_bat_pct": low_bat,
        "cpu_temp": round(cpu_t, 1) if isinstance(cpu_t, (int, float)) else None,
        "temperature": round(temp, 1) if isinstance(temp, (int, float)) else None,
        "humidity": round(hum, 0) if isinstance(hum, (int, float)) else None,
        "rad_updated": radiation.get("updated"),
        "pressure": round(pressure, 1) if isinstance(pressure, (int, float)) else None,
        "trend_arrow": trend["arrow"] if trend else "…",
        "trend_text": trend["text"] if trend else "Збір даних…",
        "trend_delta": round(trend["delta"], 1) if trend else None,
        "trend_hours": trend["hours"] if trend else None,
        "trend_hint": trend["hint"] if trend else "",
        "server_time": time.strftime("%H:%M"),
    }
    response = jsonify(out)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/export.csv")
def api_export_csv():
    """Експорт історії вимірювань у CSV (з UTF-8 BOM для Excel)."""
    kind = request.args.get("type", "radiation")
    period = parse_period(request.args.get("period"))
    since = time.time() - period
    if kind == "weather":
        rows = [r for r in list(weather_history) if r.get("time", 0) >= since]
        cols = ["temperature", "humidity", "pressure", "iaq"]
    elif kind == "system":
        rows = [r for r in list(sys_history) if r.get("time", 0) >= since]
        cols = ["cpu_temp", "rx", "tx"]
    else:
        kind = "radiation"
        rows = [r for r in list(rad_history) if r.get("time", 0) >= since]
        cols = ["usvh", "avg3h", "cpm"]

    lines = ["time_iso," + ",".join(cols)]
    for r in rows:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.get("time", 0)))
        vals = [("" if r.get(c) is None else str(r.get(c))) for c in cols]
        lines.append(ts + "," + ",".join(vals))
    csv_data = "\ufeff" + "\n".join(lines) + "\n"
    return Response(
        csv_data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="mesh_{kind}_{period}s.csv"'}
    )


@app.route("/static/<path:filename>")
def static_files(filename):
    safe_name = os.path.basename(filename)
    path = os.path.join("/home/example/mesh-monitor/static", safe_name)
    if not os.path.isfile(path):
        return ("Not found", 404)
    return send_file(path, mimetype="application/javascript")


@app.route("/MeshMonitor.apk")
def download_apk():
    return send_file(
        "/home/example/mesh-monitor/MeshMonitor.apk",
        mimetype="application/vnd.android.package-archive",
        as_attachment=True,
        download_name="MeshMonitor.apk"
    )


@app.route("/icon-192.png")
def icon_192():
    return send_file("/home/example/mesh-monitor/icon-192.png", mimetype="image/png")


@app.route("/icon-512.png")
def icon_512():
    return send_file("/home/example/mesh-monitor/icon-512.png", mimetype="image/png")


@app.route("/manifest.json")
def manifest_json():
    return jsonify({
        "name": "Mesh Monitor",
        "short_name": "MeshMonitor",
        "description": "Raspberry Pi Mesh & Radiation Monitor",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#070b10",
        "theme_color": "#070b10",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
        ]
    })


@app.route("/sw.js")
def service_worker():
    sw_code = (
        "self.addEventListener('install', e => self.skipWaiting());\n"
        "self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));\n"
        "self.addEventListener('fetch', e => {});\n"
    )
    return Response(sw_code, mimetype="application/javascript")


if __name__ == "__main__":
    threading.Thread(target=mqtt_worker, daemon=True).start()
    threading.Thread(target=sampler, daemon=True).start()
    app.run(host="0.0.0.0", port=8080, debug=False)
