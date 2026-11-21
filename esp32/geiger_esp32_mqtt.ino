#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <PubSubClient.h>
#include <TFT_eSPI.h>
#include <UniversalTelegramBot.h>
#include <ArduinoJson.h>
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"
#include <esp_adc_cal.h>
#include <time.h>

// ====================== НАСТРОЙКИ ======================
const char* WIFI_SSID = "example_wifi_ssid";
const char* WIFI_PASS = "example_wifi_password";
const char* BOT_TOKEN = "example_telegram_bot_token";
const char* CHAT_ID = "example_telegram_chat_id";

// Настройки MQTT (Raspberry Pi Mesh Monitor)
const char* MQTT_HOST = "192.168.0.x";
const uint16_t MQTT_PORT = 1883;
const char* MQTT_USER = "example_mqtt_user";
const char* MQTT_PASS = "example_mqtt_password";
const char* MQTT_TOPIC = "geiger/telemetry";

const char* GMC_USER_ID = "example_gmc_user_id";
const char* GMC_DEV_ID = "example_gmc_dev_id";
const char* RADMON_USER = "example_radmon_user";
const char* RADMON_PASS = "example_radmon_pass";
const char* THINGSPEAK_API_KEY = "example_thingspeak_api_key";

// NTP настройки
const char* NTP_SERVER = "pool.ntp.org";
const long GMT_OFFSET_SEC = 2 * 3600; // Киев / Украина = UTC+2 (зимой)
const int DAYLIGHT_OFFSET_SEC = 3600; // летнее время (+1 час)

// Пины TTGO T-Display
#define GEIGER_PIN 25
#define BATTERY_PIN 34 // ADC батареи
#define ADC_EN_PIN 14 // Включение делителя батареи

#define CONV_FACTOR 0.0057f
const float ALARM_THRESHOLD = 0.30f;
bool isAlarmActive = false;

// Функция URL-кодирования (обязательно добавить!)
String urlEncode(String str) {
String encoded = "";
char c;
char code0;
char code1;
for (int i = 0; i < str.length(); i++) {
c = str.charAt(i);
if (c == ' ') {
encoded += '+';
} else if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') {
encoded += c;
} else {
code1 = (c & 0xf) + '0';
if ((c & 0xf) > 9) code1 = (c & 0xf) - 10 + 'A';
c = (c >> 4) & 0xf;
code0 = c + '0';
if (c > 9) code0 = c - 10 + 'A';
encoded += '%';
encoded += code0;
encoded += code1;
}
}
return encoded;
}

// ====================== ОБЪЕКТЫ ======================
TFT_eSPI tft = TFT_eSPI();
WiFiClientSecure clientSecure;
UniversalTelegramBot bot(BOT_TOKEN, clientSecure);
WiFiClient espMqttClient;
PubSubClient mqttClient(espMqttClient);

// ====================== ПЕРЕМЕННЫЕ ======================
volatile unsigned long pulseCount = 0;
volatile unsigned long lastPulseMicros = 0;

const int HISTORY_SIZE = 180;
float historyCPM[HISTORY_SIZE];
int historyIdx = 0;
bool historyFull = false;

unsigned long lastMinuteTimer = 0;
unsigned long lastPulseReset = 0;
unsigned long lastMqttUpload = 0;
unsigned long lastWebUpload = 0;
unsigned long lastTgAutoUpload = 0;
unsigned long lastTgCheck = 0;
unsigned long lastDisplayUpdate = 0;
unsigned long lastBatteryRead = 0;
unsigned long lastRestart = 0;
const unsigned long RESTART_INTERVAL = 4UL * 60 * 60 * 1000 + 4UL * 60 * 1000; // 4 часа 4 минуты

float currentCPM = 0.0f;
float currentuSvh = 0.0f;
float avg3HuSvh = 0.0f;
int batteryPercent = 0;
float batteryVoltage = 0.0f;

// ====================== ПРЕРЫВАНИЕ ГЕЙГЕРА ======================
void IRAM_ATTR countPulse() {
pulseCount++;
}

// ====================== ЧТЕНИЕ БАТАРЕИ (TTGO T-Display) ======================
void readBattery() {
static bool adcCalInit = false;
static esp_adc_cal_characteristics_t adcChars;
if (!adcCalInit) {
esp_adc_cal_characterize(ADC_UNIT_1, ADC_ATTEN_DB_11, ADC_WIDTH_BIT_12, 1100, &adcChars);
adcCalInit = true;
}

// Включаем делитель
digitalWrite(ADC_EN_PIN, HIGH);
delay(10);

// Многократное усреднение (важно!)
uint32_t sum = 0;
const int samples = 64;
for (int i = 0; i < samples; i++) {
sum += analogRead(BATTERY_PIN);
delayMicroseconds(300);
}
float adc = sum / (float)samples;

// Выключаем делитель
digitalWrite(ADC_EN_PIN, LOW);

// Заводская eFuse-калибровка АЦП + делитель 1:2 на TTGO T-Display
uint32_t mv = esp_adc_cal_raw_to_voltage((uint32_t)(adc + 0.5f), &adcChars);
float rawVoltage = (mv * 2.0f) / 1000.0f;

// Плавное сглаживание (EMA) без искажения первого замера
static bool batInit = false;
if (!batInit) {
batteryVoltage = rawVoltage;
batInit = true;
} else {
batteryVoltage = batteryVoltage * 0.75f + rawVoltage * 0.25f;
}

// Кусочно-линейная кривая разряда LiPo (шаг 1%)
static const float vPts[] = {4.20f, 4.10f, 4.00f, 3.90f, 3.82f, 3.76f, 3.70f, 3.62f, 3.50f, 3.35f, 3.20f};
static const int   pPts[] = {100,   92,    82,    70,    58,    44,    30,    18,    8,     2,     0};
const int nPts = sizeof(pPts) / sizeof(pPts[0]);

if (batteryVoltage >= vPts[0]) {
batteryPercent = 100;
} else if (batteryVoltage <= vPts[nPts - 1]) {
batteryPercent = 0;
} else {
for (int i = 0; i < nPts - 1; i++) {
if (batteryVoltage >= vPts[i + 1]) {
float ratio = (batteryVoltage - vPts[i + 1]) / (vPts[i] - vPts[i + 1]);
batteryPercent = (int)(pPts[i + 1] + ratio * (pPts[i] - pPts[i + 1]) + 0.5f);
break;
}
}
}
}

void syncTime() {
configTime(GMT_OFFSET_SEC, DAYLIGHT_OFFSET_SEC, NTP_SERVER);

Serial.print("Синхронизация времени");
int attempts = 0;
time_t now = time(nullptr);

while (now < 100000 && attempts < 20) { // ждём, пока время станет нормальным
delay(500);
Serial.print(".");
now = time(nullptr);
attempts++;
}

if (now > 100000) {
Serial.println(" OK");
struct tm timeinfo;
localtime_r(&now, &timeinfo);
Serial.printf("Текущее время: %02d:%02d:%02d %02d.%02d.%04d\n",
timeinfo.tm_hour, timeinfo.tm_min, timeinfo.tm_sec,
timeinfo.tm_mday, timeinfo.tm_mon + 1, timeinfo.tm_year + 1900);
} else {
Serial.println(" FAILED");
}
}

// ====================== 3-ЧАСОВОЕ СРЕДНЕЕ ======================
float get3HourAverage() {
float sum = 0;
int count = historyFull ? HISTORY_SIZE : historyIdx;
if (count == 0) return currentuSvh;
for (int i = 0; i < count; i++) sum += historyCPM[i];
return (sum / count) * CONV_FACTOR;
}

// ====================== ДИСПЛЕЙ ======================
void updateDisplay() {
tft.fillScreen(TFT_BLACK);

// Заголовок
tft.setTextColor(TFT_CYAN, TFT_BLACK);
tft.setTextDatum(TC_DATUM); // выравнивание по центру сверху
tft.drawString("RADIATION MONITOR", 120, 3, 2);
tft.setTextDatum(TL_DATUM); // возвращаем обычное выравнивание
tft.drawFastHLine(0, 21, 240, TFT_DARKGREY);

// Текущий уровень
tft.setTextColor(TFT_WHITE, TFT_BLACK);
tft.drawString("NOW:", 6, 26, 2);

uint16_t radColor = TFT_GREEN;
if (currentuSvh >= ALARM_THRESHOLD) radColor = TFT_RED;
else if (currentuSvh > 0.20f) radColor = TFT_YELLOW;

tft.setTextColor(radColor, TFT_BLACK);
tft.drawFloat(currentuSvh, 3, 8, 45, 6);
tft.setTextColor(TFT_WHITE, TFT_BLACK);
tft.drawString("uSv/h", 151, 68, 2);

// Среднее 3 часа
tft.setTextColor(TFT_WHITE, TFT_BLACK);
tft.drawString("AVG 3H:", 6, 92, 2);
tft.setTextColor(TFT_GREENYELLOW, TFT_BLACK);
tft.drawFloat(avg3HuSvh, 3, 82, 92, 4);
tft.setTextColor(TFT_WHITE, TFT_BLACK);
tft.drawString("uSv/h", 160, 97, 2);

// Нижняя линия
tft.drawFastHLine(0, 116, 240, TFT_DARKGREY);

// WiFi + CPM
tft.setTextColor(TFT_ORANGE, TFT_BLACK);
tft.drawString("WiFi:" + String(WiFi.RSSI()) + " dBm", 4, 120, 2);
tft.drawString("CPM: " + String((int)currentCPM), 105, 120, 2);

// ===== БАТАРЕЯ КРУПНО =====
uint16_t batColor = TFT_GREEN;
if (batteryPercent <= 15) batColor = TFT_RED;
else if (batteryPercent <= 35) batColor = TFT_YELLOW;

tft.setTextColor(batColor, TFT_BLACK);
tft.drawString(String(batteryPercent) + "%", 175, 115, 4); // крупный шрифт
}

// ====================== TELEGRAM ======================
void sendTelegramMessage(String text) {
bot.sendMessage(CHAT_ID, text, "Markdown");
}

void checkRadiationAlarm() {
if (currentuSvh >= ALARM_THRESHOLD && !isAlarmActive) {
isAlarmActive = true;
String msg = "🚨 **УВАГА! РАДІАЦІЙНА ТРИВОГА!** 🚨\n\n";
msg += "Рівень вище 0.30 мкЗв/г!\n";
msg += "Зараз: **" + String(currentuSvh, 3) + " мкЗв/г**";
sendTelegramMessage(msg);
}
else if (currentuSvh < (ALARM_THRESHOLD - 0.05f) && isAlarmActive) {
isAlarmActive = false;
sendTelegramMessage("✅ Фон повернувся до норми: " + String(currentuSvh, 3) + " мкЗв/г");
}
}

void handleTelegramCommands(int numNewMessages) {
for (int i = 0; i < numNewMessages; i++) {
String chat_id = String(bot.messages[i].chat_id);
String text = bot.messages[i].text;

if (text == "/start") {
String welcome = "Радіомонітор готовий!\n\n";
welcome += "/status — поточні дані\n";
welcome += "/stats — середня за 3 години\n";
welcome += "/settings — поріг тривоги\n";
welcome += "/help — довідка";
bot.sendMessage(chat_id, welcome, "");
}
else if (text == "/status") {
String msg = "📊 **Поточні дані**\n";
msg += "Рівень: `" + String(currentuSvh, 3) + "` мкЗв/г\n";
msg += "Середня 3 год: `" + String(avg3HuSvh, 3) + "` мкЗв/г\n";
msg += "CPM: `" + String((int)currentCPM) + "`\n";
msg += "Батарея: `" + String(batteryPercent) + "%` (" + String(batteryVoltage, 2) + " V)\n";
msg += "WiFi: `" + String(WiFi.RSSI()) + "` dBm";
bot.sendMessage(chat_id, msg, "Markdown");
}
else if (text == "/stats") {
bot.sendMessage(chat_id, "📈 Середнє за 3 години: " + String(avg3HuSvh, 3) + " мкЗв/г", "");
}
else if (text == "/settings") {
bot.sendMessage(chat_id, "⚙️ Поріг тривоги: " + String(ALARM_THRESHOLD, 2) + " мкЗв/г", "");
}
else if (text == "/help") {
bot.sendMessage(chat_id, "ℹ️ TTGO T-Display + GGreg20 + СБМ-20\nТревога при > 0.30 мкЗв/г ", "");
}
}
}

// ====================== ОТПРАВКА В MQTT (Raspberry Pi) ======================
void publishMQTT() {
if (WiFi.status() != WL_CONNECTED) return;

if (!mqttClient.connected()) {
String clientId = "ESP32-Geiger-" + String((uint32_t)ESP.getEfuseMac(), HEX);
if (!mqttClient.connect(clientId.c_str(), MQTT_USER, MQTT_PASS)) {
Serial.printf("MQTT ошибка подключения, rc=%d\n", mqttClient.state());
return;
}
}

String payload = "{";
payload += "\"usvh\":" + String(currentuSvh, 4) + ",";
payload += "\"avg3h\":" + String(avg3HuSvh, 4) + ",";
payload += "\"cpm\":" + String((int)currentCPM) + ",";
payload += "\"battery\":" + String(batteryPercent) + ",";
payload += "\"voltage\":" + String(batteryVoltage, 2) + ",";
payload += "\"rssi\":" + String(WiFi.RSSI()) + ",";
payload += "\"alarm\":" + String(isAlarmActive ? "true" : "false");
payload += "}";

if (mqttClient.publish(MQTT_TOPIC, payload.c_str(), true)) {
Serial.println("MQTT OK: " + payload);
} else {
Serial.println("MQTT publish ERROR");
}
}

// ====================== ВЫГРУЗКА НА САЙТЫ ======================
void uploadToServices() {
if (WiFi.status() != WL_CONNECTED) return;

HTTPClient http;

// ---------- 1. GMC Map ----------
{
String gmcUrl = "http://www.gmcmap.com/log2.asp?AID=" + String(GMC_USER_ID) +
"&GID=" + String(GMC_DEV_ID) +
"&CPM=" + String((int)currentCPM) +
"&uSV=" + String(currentuSvh, 4);

http.begin(gmcUrl);
int code = http.GET();
Serial.printf("GMC Map: %d\n", code);
http.end();
}
delay(600);

// ---------- 2. Radmon ----------
{
String radmonUrl = "http://radmon.org/radmon.php?function=submit&user=" + String(RADMON_USER) +
"&password=" + String(RADMON_PASS) +
"&value=" + String((int)currentCPM) + "&unit=CPM";

http.begin(radmonUrl);
int code = http.GET();
Serial.printf("Radmon: %d\n", code);
http.end();
}
delay(600);

// ---------- 3. ThingSpeak ----------
{
// Формируем текст статуса
String statusText = "Радиация: " + String(currentuSvh, 3) + " uSv/h";
statusText += " | CPM: " + String((int)currentCPM);
statusText += " | Батарея: " + String(batteryPercent) + "%";

if (currentuSvh >= ALARM_THRESHOLD) {
statusText = "ТРЕВОГА! " + statusText;
}

String encodedStatus = urlEncode(statusText);

// http:// — надёжнее https на ESP32 (без сюрпризов с TLS-сертификатами),
// ThingSpeak прекрасно принимает и обычный HTTP.
String thingspeakUrl = "http://api.thingspeak.com/update?api_key=";
thingspeakUrl += String(THINGSPEAK_API_KEY);
thingspeakUrl += "&field1=" + String(currentuSvh, 4);
thingspeakUrl += "&field2=" + String((int)currentCPM);
thingspeakUrl += "&field3=" + String(batteryPercent);
thingspeakUrl += "&field4=" + String(batteryVoltage, 2);
thingspeakUrl += "&field5=" + String(WiFi.RSSI());
thingspeakUrl += "&field6=" + String(avg3HuSvh, 4);
thingspeakUrl += "&status=" + encodedStatus;

HTTPClient http;
http.begin(thingspeakUrl);

int code = http.GET();
String response = http.getString();

// ThingSpeak почти всегда отвечает 200, даже если апдейт отклонён.
// Реальный успех — тело ответа: число > 0 = ID записи, "0" = отклонено.
if (code == 200 && response.toInt() > 0) {
Serial.println("ThingSpeak OK, entry ID: " + response);
} else {
Serial.printf("ThingSpeak ERROR, HTTP code: %d, ответ: %s\n", code, response.c_str());
}

http.end();
}
}

// ====================== SETUP ======================
void setup() {
WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);
pinMode(GEIGER_PIN, INPUT_PULLUP);
attachInterrupt(digitalPinToInterrupt(GEIGER_PIN), countPulse, FALLING);

// Батарея
pinMode(ADC_EN_PIN, OUTPUT);
digitalWrite(ADC_EN_PIN, LOW);
analogReadResolution(12);
analogSetAttenuation(ADC_11db);
analogSetPinAttenuation(BATTERY_PIN, ADC_11db);

tft.init();
tft.setRotation(1);
tft.fillScreen(TFT_BLACK);
tft.setTextColor(TFT_WHITE, TFT_BLACK);
tft.drawString("Connecting WiFi...", 15, 50, 4);

// ---- Подключение Wi-Fi с таймаутом ----
WiFi.mode(WIFI_STA);
WiFi.begin(WIFI_SSID, WIFI_PASS);

int attempts = 0;
while (WiFi.status() != WL_CONNECTED && attempts < 40) { // максимум ~16 секунд
delay(400);
attempts++;

// Показываем точки
tft.fillRect(15, 80, 210, 20, TFT_BLACK);
tft.setCursor(15, 80);
for (int i = 0; i < attempts % 6; i++) tft.print(".");
}

if (WiFi.status() == WL_CONNECTED) {
tft.fillScreen(TFT_BLACK);
tft.setTextColor(TFT_GREEN, TFT_BLACK);
tft.drawString("WiFi OK!", 70, 55, 4);
delay(500);

// Синхронизация времени
tft.setTextColor(TFT_CYAN, TFT_BLACK);
tft.drawString("Sync time...", 55, 90, 2);
syncTime();
delay(800);
} else {
tft.fillScreen(TFT_BLACK);
tft.setTextColor(TFT_RED, TFT_BLACK);
tft.drawString("WiFi FAIL", 55, 45, 4);
tft.drawString("Check SSID/PASS", 30, 80, 2);
delay(3000);
}
lastRestart = millis();

clientSecure.setInsecure();
mqttClient.setServer(MQTT_HOST, MQTT_PORT);
lastPulseReset = millis();
lastMinuteTimer = millis();
lastDisplayUpdate = millis();
lastBatteryRead = millis();
readBattery();
updateDisplay();
}

// ====================== LOOP ======================
void loop() {
unsigned long now = millis();

if (mqttClient.connected()) {
mqttClient.loop();
}

// ====================== АВТОПЕРЕЗАГРУЗКА ======================
// Каждые 4 часа 4 минуты
if (now - lastRestart >= RESTART_INTERVAL) {
Serial.println("Плановая перезагрузка через 4ч 4мин...");
delay(300);
ESP.restart();
}

// ----- Подсчёт каждую минуту -----
if (now - lastMinuteTimer >= 60000UL) {
lastMinuteTimer = now;

noInterrupts();
unsigned long counts = pulseCount;
pulseCount = 0;
interrupts();

currentCPM = (float)counts;
currentuSvh = currentCPM * CONV_FACTOR;

historyCPM[historyIdx] = currentCPM;
historyIdx++;
if (historyIdx >= HISTORY_SIZE) {
historyIdx = 0;
historyFull = true;
}

avg3HuSvh = get3HourAverage();
checkRadiationAlarm();

// ----- Отправка в MQTT (первый раз через 1 мин, далее каждые 10 минут = 600000 мс) -----
if (lastMqttUpload == 0 || (now - lastMqttUpload >= 600000UL)) {
lastMqttUpload = now;
publishMQTT();
}
}

// ----- Батарея каждые 20 секунд -----
if (now - lastBatteryRead >= 20000) {
lastBatteryRead = now;
readBattery();
}

// ----- Обновление экрана каждые 2 секунды -----
if (now - lastDisplayUpdate >= 2000) {
lastDisplayUpdate = now;
updateDisplay();
}

// ----- Выгрузка на сайты (раз в 30 минут) -----
if ((lastWebUpload == 0 && now >= 120000UL) || (now - lastWebUpload >= 1800000UL)) {
lastWebUpload = now;
uploadToServices();
}

// ----- Плановый отчёт в Telegram (раз в 2 часа) -----
if (now - lastTgAutoUpload >= 7200000UL) {
lastTgAutoUpload = now;

String msg = "📊 *Плановий звіт*\n";
msg += "Поточний: " + String(currentuSvh, 3) + " мкЗв/г\n";
msg += "Середній (3год): " + String(avg3HuSvh, 3) + " мкЗв/г\n";
msg += "Батарея: " + String(batteryPercent) + "% (" + String(batteryVoltage, 2) + " В)";

sendTelegramMessage(msg);
}

// ----- Улучшенная проверка команд Telegram (раз в 25 секунд) -----
if (now - lastTgCheck >= 25000UL) {
lastTgCheck = now;

if (WiFi.status() == WL_CONNECTED) {
int numNewMessages = bot.getUpdates(bot.last_message_received + 1);

int attempts = 0;
while (numNewMessages && attempts < 5) { // защита от зацикливания
handleTelegramCommands(numNewMessages);
numNewMessages = bot.getUpdates(bot.last_message_received + 1);
attempts++;
delay(50);
}
}
}
}
