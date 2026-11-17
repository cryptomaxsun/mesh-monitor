# Mesh Monitor

[![Platform: Raspberry Pi](https://img.shields.io/badge/Server-Raspberry%20Pi%20(Flask%20%2B%20MQTT)-C51A4A?logo=raspberrypi&logoColor=white)](#)
[![Android: 5.0–16](https://img.shields.io/badge/Android-5.0%E2%80%9316%20(API%2021%E2%80%9336)-3DDC84?logo=android&logoColor=white)](#)
[![ESP32: TTGO T-Display](https://img.shields.io/badge/Firmware-ESP32%20%2B%20SBM--20-E7352C?logo=espressif&logoColor=white)](#)

**Мова / Language:** [🇺🇦 Українська](#-українська) · [🇬🇧 English](#-english)

---

## 🇺🇦 Українська

**Mesh Monitor** — це автономна система моніторингу радіаційного фону, метеоданих (температура, вологість, тиск, IAQ), стану вузлів **Meshtastic**, мережі **Reticulum (RNS)** та системних параметрів **Raspberry Pi** з веб-панеллю, нативним Android-додатком, фоновими сповіщеннями та віджетом головного екрана.

### 🧱 Архітектура проєкту

```text
ESP32 TTGO T-Display (СБМ-20) ──▶ MQTT (geiger/telemetry) ──┐
                                                             ├──▶ Raspberry Pi (Flask + Mosquitto)
Вузли Meshtastic (LoRa/MQTT)  ──▶ MQTT (msh/#)             ──┘      ├── Веб-панель (Chart.js offline)
                                                                    ├── API (/api/data, /api/notify, /api/export.csv)
                                                                    └── Android-додаток (LAN → Tailscale → Funnel)
```

### ✨ Основні можливості

- **Веб-дашборд (`server/app.py`)**
  - Адаптивні графіки (лінії, свічки OHLC, діапазони min–max, режим `✨ Авто`) з підтримкою зуму та дотику на мобільних пристроях (`chart.umd.min.js`, `hammer.min.js`, `chartjs-plugin-zoom.min.js` працюють локально без інтернету).
  - Детальна адаптивна шкала радіації (`0.05 мкЗв/год`) для мобільних екранів і точні підказки при натисканні.
  - Барометр із відстеженням тенденції тиску та штормовим попередженням (при різкому падінні тиску ≥ `3.5 hPa`).
  - Моніторинг вузлів Meshtastic (заряд батареї, напруга, SNR/RSSI, телеметрія), радіоканалу, стану Reticulum (`rnstatus`) та Raspberry Pi (CPU, температура, RAM, диск).
  - Експорт історії вимірювань у CSV (`/api/export.csv`).
- **Android-додаток (`android/`)**
  - Підтримка Android 5.0–16 (`minSdk 21`, `targetSdk 36`).
  - Автоматичне перемикання каналів зв'язку: локальна мережа Wi-Fi → Tailscale VPN → Tailscale Funnel.
  - Постійне беззвучне сповіщення у шторці з рівнем радіації, тенденцією барометра та кнопкою ручного оновлення (`5 / 10 / 15 хв`).
  - Критичні тривожні сповіщення: радіація ≥ `0.30 мкЗв/год`, штормове падіння тиску, низький заряд батареї, перегрів Raspberry Pi.
  - **Віджет головного екрана (4×1)**: поточна радіація, тенденція барометра, атмосферний тиск (`hPa`), час оновлення та регулювання прозорості фону (`0%`, `30%`, `50%`, `70%`, `90%`).
- **Прошивка дозиметра (`esp32/geiger_esp32_mqtt.ino`)**
  - Підрахунок імпульсів із трубки СБМ-20 на ESP32 TTGO T-Display з виведенням на TFT-екран.
  - Публікація телеметрії в локальний MQTT-брокер, Telegram-сповіщення та опційне відправлення в зовнішні сервіси (GMCMap, Radmon, Safecast, ThingSpeak, OpenRadiation).

### 📂 Структура репозиторію

```text
├── server/
│   ├── app.py                  # Веб-сервер Flask та обробник MQTT для Raspberry Pi
│   ├── requirements.txt        # Залежності Python (flask, paho-mqtt)
│   ├── icon-192.png            # Іконка PWA 192×192
│   ├── icon-512.png            # Іконка PWA 512×512
│   └── static/                 # Локальні JS-бібліотеки (Chart.js, Hammer.js, Zoom plugin)
├── android/
│   ├── app/                    # Вихідний код Android-додатка (Java + XML)
│   ├── build_apk36.py          # Скрипт збірки та підпису APK (Android SDK 36)
│   ├── deploy_apk.py           # Скрипт деплою на Raspberry Pi через SFTP
│   ├── gen_adaptive_res.py     # Генератор адаптивних ресурсів іконок
│   └── gen_stat_icon.py        # Генератор іконки сповіщень
├── esp32/
│   └── geiger_esp32_mqtt.ino   # Прошивка ESP32 TTGO T-Display + СБМ-20
└── GITHUB_UPLOAD_GUIDE.md      # Покрокова інструкція завантаження на GitHub
```

### 🚀 Швидкий старт

> **Важливо:** У публічній версії всі паролі, токени, координати та IP-адреси замінені на шаблони (`example_*`, `192.168.0.x`, `your-node.example.ts.net`). Перед запуском вкажіть власні значення.

#### 1. Сервер на Raspberry Pi
```bash
cd server
pip install -r requirements.txt
export MQTT_PASSWORD="your_mqtt_password"
python3 app.py
```
Для автозапуску рекомендується налаштувати `systemd`-сервіс (`mesh-monitor.service`).

#### 2. Прошивка ESP32
Відкрийте `esp32/geiger_esp32_mqtt.ino` в Arduino IDE, заповніть свої `WIFI_SSID`, `WIFI_PASS`, `MQTT_HOST`, `MQTT_USER`, `MQTT_PASS`, координати `MY_LATITUDE` / `MY_LONGITUDE` (та за бажанням ключі Telegram / радіаційних сервісів) і прошийте плату TTGO T-Display.

#### 3. Збірка Android APK
Вкажіть свої адреси сервера у `MainActivity.java` та `NotifyHelper.java`, налаштуйте шляхи до JDK 17 і Android Build-Tools 36 у `android/build_apk36.py` та запустіть:
```bash
cd android
python build_apk36.py
```

---

## 🇬🇧 English

**Mesh Monitor** is a self-hosted monitoring system for radiation background, weather telemetry (temperature, humidity, barometric pressure, IAQ), **Meshtastic** nodes, **Reticulum (RNS)** network status, and **Raspberry Pi** system health — featuring an offline-ready web dashboard, a native Android app with background alerts, and a home-screen widget.

### 🧱 Architecture

```text
ESP32 TTGO T-Display (SBM-20) ──▶ MQTT (geiger/telemetry) ──┐
                                                             ├──▶ Raspberry Pi (Flask + Mosquitto)
Meshtastic Nodes (LoRa/MQTT)  ──▶ MQTT (msh/#)             ──┘      ├── Web Dashboard (offline Chart.js)
                                                                    ├── REST API (/api/data, /api/notify, /api/export.csv)
                                                                    └── Android App (LAN → Tailscale → Funnel)
```

### ✨ Key Features

- **Web Dashboard (`server/app.py`)**
  - Responsive charts (line, OHLC candlesticks, min–max bands, `✨ Auto` mode) with touch & zoom support using locally hosted `chart.umd.min.js`, `hammer.min.js`, and `chartjs-plugin-zoom.min.js` (works offline).
  - Fine-grained adaptive radiation Y-axis (`0.05 µSv/h` step) for mobile screens and reliable touch tooltips.
  - Barometer trend tracking with storm warning alerts (rapid pressure drop ≥ `3.5 hPa`).
  - Meshtastic node monitoring (battery, voltage, SNR/RSSI, telemetry), radio stats, Reticulum (`rnstatus`) status, and Raspberry Pi metrics (CPU, temperature, RAM, disk).
  - CSV history export (`/api/export.csv`).
- **Android App (`android/`)**
  - Compatible with Android 5.0–16 (`minSdk 21`, `targetSdk 36`).
  - Automatic connection failover: Local Wi-Fi (LAN) → Tailscale VPN → Tailscale Funnel.
  - Persistent silent status-bar notification with radiation level, barometer trend, manual refresh button, and `5 / 10 / 15 min` polling interval toggle.
  - High-priority alerts for radiation ≥ `0.30 µSv/h`, storm pressure drops, low node/dosimeter battery, and Raspberry Pi overheating.
  - **Home-Screen Widget (4×1)**: displays current radiation, barometer trend, atmospheric pressure (`hPa`), last update time, and configurable background transparency (`0%`, `30%`, `50%`, `70%`, `90%`).
- **ESP32 Geiger Firmware (`esp32/geiger_esp32_mqtt.ino`)**
  - Pulse counting from an SBM-20 Geiger-Müller tube on ESP32 TTGO T-Display with TFT readout.
  - Publishes telemetry to a local MQTT broker, sends Telegram alerts, and optionally forwards readings to GMCMap, Radmon, Safecast, ThingSpeak, and OpenRadiation.

### 📂 Repository Structure

```text
├── server/
│   ├── app.py                  # Flask web server & MQTT worker for Raspberry Pi
│   ├── requirements.txt        # Python dependencies (flask, paho-mqtt)
│   ├── icon-192.png            # PWA icon 192×192
│   ├── icon-512.png            # PWA icon 512×512
│   └── static/                 # Offline JS assets (Chart.js, Hammer.js, Zoom plugin)
├── android/
│   ├── app/                    # Android app source code (Java + XML resources)
│   ├── build_apk36.py          # APK build & signing script (Android SDK 36)
│   ├── deploy_apk.py           # SFTP deployment script for Raspberry Pi
│   ├── gen_adaptive_res.py     # Adaptive icon resource generator
│   └── gen_stat_icon.py        # Notification icon generator
├── esp32/
│   └── geiger_esp32_mqtt.ino   # ESP32 TTGO T-Display + SBM-20 firmware
└── GITHUB_UPLOAD_GUIDE.md      # Step-by-step GitHub upload guide
```

### 🚀 Quick Start

> **Note:** All passwords, API tokens, coordinates, and personal IP addresses have been replaced with placeholders (`example_*`, `192.168.0.x`, `your-node.example.ts.net`). Configure your own values before deploying.

#### 1. Raspberry Pi Server
```bash
cd server
pip install -r requirements.txt
export MQTT_PASSWORD="your_mqtt_password"
python3 app.py
```
For production use, run it as a `systemd` service (`mesh-monitor.service`).

#### 2. ESP32 Firmware
Open `esp32/geiger_esp32_mqtt.ino` in Arduino IDE, set your `WIFI_SSID`, `WIFI_PASS`, `MQTT_HOST`, `MQTT_USER`, `MQTT_PASS`, and `MY_LATITUDE` / `MY_LONGITUDE` (plus optional Telegram/monitoring API keys), then flash your TTGO T-Display.

#### 3. Android APK Build
Update your server URLs in `MainActivity.java` and `NotifyHelper.java`, configure JDK 17 and Android Build-Tools 36 paths in `android/build_apk36.py`, and run:
```bash
cd android
python build_apk36.py
```
