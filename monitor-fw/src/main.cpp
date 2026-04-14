#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <TFT_eSPI.h>
#include <SPI.h>
#include <SPIFFS.h>
#include <Preferences.h>

// ============== CONFIG ==============
#define AP_SSID "ChemMonitor-Setup"
#define AP_PASS "12345678"
#define MAX_SERVERS 4
#define HIST_LEN 120
#define FETCH_INTERVAL 2000

// ============== OBJECTS ==============
TFT_eSPI tft = TFT_eSPI();
WebServer webServer(80);
DNSServer dnsServer;
Preferences prefs;

// Colors now provided by Theme struct (T.bg, T.card, etc.)

// ============== DATA ==============
float cpuHist[HIST_LEN] = {0};
float ramHist[HIST_LEN] = {0};
int histIdx = 0;

float cpuTotal = 0, ramPct = 0, diskPct = 0;
float ramUsed = 0, ramTotal = 0;
float diskUsed = 0, diskTotal = 0;
float swapPct = 0, swapUsed = 0, swapTotal = 0;
float netSent = 0, netRecv = 0;
float cpuTemp = -1, gpuTemp = -1;
float pingMs = -1, uptimeSec = 0;
int procCount = 0;
float cpuCores[16] = {0};
int numCores = 0;

struct ProcInfo { char name[20]; float cpu; float mem; int pid; };
ProcInfo topProcs[20];
int numProcs = 0;

struct ServerConfig { char name[24]; char url[80]; };
ServerConfig servers[MAX_SERVERS];
int numServers = 0;
int activeServer = 0;

char wifiSSID[33] = "";
char wifiPass[65] = "";
char serverHostname[32] = "";

// ============== STATE ==============
bool dataOk = false;
int failCount = 0;
bool setupMode = false;
int currentView = 0;
#define NUM_VIEWS 5
bool viewChanged = true;
unsigned long lastWifiCheck = 0;

volatile bool fetchDone = false;
volatile int killPid = -1;  // Set from Core 1, executed on Core 0
SemaphoreHandle_t dataMutex;
void fetchTask(void* param);
void resetConfig();

// ============== TOUCH STATE ==============
struct TouchState {
  bool pressed;
  uint16_t startX, startY, curX, curY;
  unsigned long startTime;
  unsigned long lastActivity;
  enum Gesture { NONE, TAP, SWIPE_LEFT, SWIPE_RIGHT, SWIPE_UP, SWIPE_DOWN, LONG_PRESS };
  Gesture detected;
} touch = {false, 0, 0, 0, 0, 0, 0, TouchState::NONE};

// ============== BRIGHTNESS ==============
uint8_t brightness = 255;
bool brightnessOverlay = false;

// ============== SCREENSAVER ==============
bool screensaverActive = false;
#define SCREENSAVER_TIMEOUT 30000
#define MATRIX_COLS 24
int matrixY[MATRIX_COLS] = {0};
int matrixSpeed[MATRIX_COLS] = {0};

// ============== THEMES ==============
struct Theme {
  uint16_t bg, card, border, text, muted, accent, green, yellow, red, cyan;
};
const Theme THEME_DARK  = {0x0861, 0x1124, 0x2969, 0xE73C, 0x8C51, 0x633E, 0x1EA6, 0xED40, 0xEA08, 0x05B4};
const Theme THEME_CYBER = {0x0000, 0x0841, 0x18E3, 0x07FF, 0x4228, 0xF800, 0x07E0, 0xFFE0, 0xF800, 0x07FF};
int currentTheme = 0;
const Theme* themes[] = {&THEME_DARK, &THEME_CYBER};
#define T (*(themes[currentTheme]))

// ============== ANIMATED VALUES ==============
float dispCpu = 0, dispRam = 0, dispDisk = 0;

// ============== HELPERS ==============
uint16_t gaugeColor(float v) { return v > 90 ? T.red : v > 70 ? T.yellow : T.accent; }

char* fmtBytes(float b, char* buf, int sz) {
  if (b >= 1e9) snprintf(buf, sz, "%.1fGB", b / 1e9);
  else if (b >= 1e6) snprintf(buf, sz, "%.0fMB", b / 1e6);
  else snprintf(buf, sz, "%.0fKB", b / 1e3);
  return buf;
}

char* fmtUptime(float sec, char* buf, int sz) {
  int d = (int)(sec / 86400);
  int h = (int)(sec / 3600) % 24;
  int m = (int)(sec / 60) % 60;
  if (d > 0) snprintf(buf, sz, "%dd%dh%dm", d, h, m);
  else snprintf(buf, sz, "%dh%dm", h, m);
  return buf;
}

// ============== PREFERENCES ==============
void loadConfig() {
  prefs.begin("srvmon", true);
  strlcpy(wifiSSID, prefs.getString("ssid", "").c_str(), sizeof(wifiSSID));
  strlcpy(wifiPass, prefs.getString("pass", "").c_str(), sizeof(wifiPass));
  numServers = prefs.getInt("nsrv", 0);
  for (int i = 0; i < numServers && i < MAX_SERVERS; i++) {
    String key = "srv" + String(i);
    String val = prefs.getString(key.c_str(), "");
    // Format: "name|url"
    int sep = val.indexOf('|');
    if (sep > 0) {
      strlcpy(servers[i].name, val.substring(0, sep).c_str(), sizeof(servers[i].name));
      strlcpy(servers[i].url, val.substring(sep + 1).c_str(), sizeof(servers[i].url));
    }
  }
  prefs.end();
}

void saveConfig() {
  prefs.begin("srvmon", false);
  prefs.putString("ssid", wifiSSID);
  prefs.putString("pass", wifiPass);
  prefs.putInt("nsrv", numServers);
  for (int i = 0; i < numServers; i++) {
    String key = "srv" + String(i);
    String val = String(servers[i].name) + "|" + String(servers[i].url);
    prefs.putString(key.c_str(), val);
  }
  prefs.end();
}

// ============== WEB SERVER (Setup Mode) ==============
void setupWebServer() {
  // Serve index.html
  webServer.on("/", []() {
    File f = SPIFFS.open("/index.html", "r");
    if (f) {
      webServer.streamFile(f, "text/html");
      f.close();
    } else {
      webServer.send(200, "text/html", "<h1>SPIFFS Error</h1>");
    }
  });

  // Download server package as ZIP
  webServer.on("/download", []() {
    // Serve files individually - list them
    String html = "<html><body style='background:#0f1117;color:#e1e4ed;font-family:sans-serif;padding:20px'>";
    html += "<h2>Archivos del servidor</h2><p>Descarga estos archivos y ponlos en una carpeta:</p><ul>";
    html += "<li><a href='/dl/server_monitor.py' style='color:#6366f1'>server_monitor.py</a></li>";
    html += "<li><a href='/dl/requirements.txt' style='color:#6366f1'>requirements.txt</a></li>";
    html += "<li><a href='/dl/install.bat' style='color:#6366f1'>install.bat</a> (Windows)</li>";
    html += "<li><a href='/dl/install.sh' style='color:#6366f1'>install.sh</a> (Linux)</li>";
    html += "</ul><p style='color:#8b8fa3'>ChemMonitor ESP32 - by ChemaDev & ClaudeCode</p></body></html>";
    webServer.send(200, "text/html", html);
  });

  // Serve individual files for download
  webServer.on("/dl/server_monitor.py", []() {
    File f = SPIFFS.open("/server/server_monitor.py", "r");
    if (f) { webServer.streamFile(f, "application/octet-stream"); f.close(); }
    else webServer.send(404, "text/plain", "Not found");
  });
  webServer.on("/dl/requirements.txt", []() {
    File f = SPIFFS.open("/server/requirements.txt", "r");
    if (f) { webServer.streamFile(f, "application/octet-stream"); f.close(); }
    else webServer.send(404, "text/plain", "Not found");
  });
  webServer.on("/dl/install.bat", []() {
    File f = SPIFFS.open("/server/install.bat", "r");
    if (f) { webServer.streamFile(f, "application/octet-stream"); f.close(); }
    else webServer.send(404, "text/plain", "Not found");
  });
  webServer.on("/dl/install.sh", []() {
    File f = SPIFFS.open("/server/install.sh", "r");
    if (f) { webServer.streamFile(f, "application/octet-stream"); f.close(); }
    else webServer.send(404, "text/plain", "Not found");
  });

  // API: device info
  webServer.on("/api/device", []() {
    JsonDocument doc;
    doc["chip"] = "ESP32-D0WD-V3";
    doc["mac"] = WiFi.macAddress();
    doc["ssid"] = wifiSSID;
    doc["heap"] = ESP.getFreeHeap();
    String out;
    serializeJson(doc, out);
    webServer.send(200, "application/json", out);
  });

  // API: save WiFi
  webServer.on("/api/wifi", HTTP_POST, []() {
    JsonDocument doc;
    deserializeJson(doc, webServer.arg("plain"));
    strlcpy(wifiSSID, doc["ssid"] | "", sizeof(wifiSSID));
    strlcpy(wifiPass, doc["password"] | "", sizeof(wifiPass));
    saveConfig();
    webServer.send(200, "application/json", "{\"status\":\"ok\"}");
  });

  // API: get servers
  webServer.on("/api/servers", HTTP_GET, []() {
    JsonDocument doc;
    JsonArray arr = doc["servers"].to<JsonArray>();
    for (int i = 0; i < numServers; i++) {
      JsonObject s = arr.add<JsonObject>();
      s["name"] = servers[i].name;
      s["url"] = servers[i].url;
    }
    String out;
    serializeJson(doc, out);
    webServer.send(200, "application/json", out);
  });

  // API: save servers
  webServer.on("/api/servers", HTTP_POST, []() {
    JsonDocument doc;
    deserializeJson(doc, webServer.arg("plain"));
    JsonArray arr = doc["servers"];
    numServers = min((int)arr.size(), MAX_SERVERS);
    for (int i = 0; i < numServers; i++) {
      strlcpy(servers[i].name, arr[i]["name"] | "Server", sizeof(servers[i].name));
      String rawUrl = arr[i]["url"] | "";
      // Ensure http:// prefix
      if (!rawUrl.startsWith("http://") && !rawUrl.startsWith("https://"))
        rawUrl = "http://" + rawUrl;
      // Ensure :8090 port if no port specified
      // Check if there's a port after the host (http://host:port or http://host)
      String afterProto = rawUrl.substring(rawUrl.indexOf("//") + 2);
      if (afterProto.indexOf(':') < 0) {
        // No port found - add default :8090
        int pathStart = afterProto.indexOf('/');
        if (pathStart < 0)
          rawUrl += ":8090";
        else
          rawUrl = rawUrl.substring(0, rawUrl.indexOf("//") + 2 + pathStart) + ":8090" + afterProto.substring(pathStart);
      }
      strlcpy(servers[i].url, rawUrl.c_str(), sizeof(servers[i].url));
    }
    saveConfig();
    webServer.send(200, "application/json", "{\"status\":\"ok\"}");
  });

  // API: test connection
  webServer.on("/api/test", []() {
    JsonDocument doc;
    if (WiFi.status() == WL_CONNECTED) {
      doc["wifi"] = "connected";
      doc["ip"] = WiFi.localIP().toString();
      JsonArray arr = doc["servers"].to<JsonArray>();
      for (int i = 0; i < numServers; i++) {
        JsonObject s = arr.add<JsonObject>();
        s["name"] = servers[i].name;
        HTTPClient http;
        String url = String(servers[i].url) + "/api/config";
        http.begin(url);
        http.setTimeout(3000);
        int code = http.GET();
        s["ok"] = (code == 200);
        http.end();
      }
    } else {
      doc["wifi"] = "disconnected";
    }
    String out;
    serializeJson(doc, out);
    webServer.send(200, "application/json", out);
  });

  // API: reboot
  webServer.on("/api/reboot", HTTP_POST, []() {
    webServer.send(200, "application/json", "{\"status\":\"ok\"}");
    delay(500);
    ESP.restart();
  });

  // API: reset config
  webServer.on("/api/reset", HTTP_POST, []() {
    webServer.send(200, "application/json", "{\"status\":\"ok\"}");
    delay(500);
    resetConfig();
  });

  // Captive portal: redirect all unknown to index
  webServer.onNotFound([]() {
    webServer.sendHeader("Location", "http://192.168.4.1/", true);
    webServer.send(302, "text/plain", "");
  });

  webServer.begin();
}

// ============== TOUCH + GESTURES ==============
void resetConfig() {
  prefs.begin("srvmon", false);
  prefs.clear();
  prefs.end();
  tft.fillScreen(T.bg);
  tft.setTextDatum(MC_DATUM);
  tft.setTextFont(4);
  tft.setTextColor(T.green, T.bg);
  tft.drawString("Config borrada!", tft.width() / 2, tft.height() / 2 - 10);
  tft.setTextFont(2);
  tft.setTextColor(T.muted, T.bg);
  tft.drawString("Reiniciando...", tft.width() / 2, tft.height() / 2 + 20);
  delay(2000);
  ESP.restart();
}

void handleGesture(TouchState::Gesture g, uint16_t tx, uint16_t ty) {
  // In setup mode: only handle reset button tap
  if (setupMode) {
    if (g == TouchState::TAP && ty > 260 && ty < 300 &&
        tx > tft.width() / 2 - 80 && tx < tft.width() / 2 + 80) {
      resetConfig();
    }
    return;
  }
  touch.lastActivity = millis();

  // Exit screensaver on any touch
  if (screensaverActive) {
    screensaverActive = false;
    viewChanged = true;
    return;
  }

  // Dismiss brightness overlay
  if (brightnessOverlay && g != TouchState::LONG_PRESS) {
    brightnessOverlay = false;
    viewChanged = true;
    return;
  }

  switch (g) {
    case TouchState::SWIPE_LEFT:
      currentView = (currentView + 1) % NUM_VIEWS;
      viewChanged = true;
      break;
    case TouchState::SWIPE_RIGHT:
      currentView = (currentView - 1 + NUM_VIEWS) % NUM_VIEWS;
      viewChanged = true;
      break;
    case TouchState::SWIPE_UP:
      if (numServers > 1) {
        activeServer = (activeServer + 1) % numServers;
        serverHostname[0] = 0;  // Re-fetch hostname
        memset(cpuHist, 0, sizeof(cpuHist));
        memset(ramHist, 0, sizeof(ramHist));
        histIdx = 0;
        dataOk = false;
        viewChanged = true;
      }
      break;
    case TouchState::SWIPE_DOWN:
      if (numServers > 1) {
        activeServer = (activeServer - 1 + numServers) % numServers;
        serverHostname[0] = 0;
        memset(cpuHist, 0, sizeof(cpuHist));
        memset(ramHist, 0, sizeof(ramHist));
        histIdx = 0;
        dataOk = false;
        viewChanged = true;
      }
      break;
    case TouchState::TAP:
      // Tap status bar (bottom 18px): switch theme
      if (ty > 302) {
        currentTheme = (currentTheme + 1) % 2;
        viewChanged = true;
      }
      // In process view: kill process (tap on process rows)
      else if (currentView == 4 && ty > 36 && ty < 290) {
        int row = (ty - 58) / 18;
        if (row >= 0 && row < numProcs) {
          killPid = topProcs[row].pid;
          Serial.printf("Kill PID %d (%s)\n", killPid, topProcs[row].name);
        }
      }
      // Bottom nav area (y 290-302): prev/next view
      else if (ty > 290) {
        if (tx < 240) { currentView = (currentView - 1 + NUM_VIEWS) % NUM_VIEWS; viewChanged = true; }
        else { currentView = (currentView + 1) % NUM_VIEWS; viewChanged = true; }
      }
      break;
    case TouchState::LONG_PRESS:
      brightnessOverlay = true;
      break;
    default: break;
  }
}

void checkTouch() {
  uint16_t tx, ty;
  bool touched = tft.getTouch(&tx, &ty, 20);

  if (touched) {
    touch.lastActivity = millis();
    if (!touch.pressed) {
      // Touch start
      touch.pressed = true;
      touch.startX = tx; touch.startY = ty;
      touch.startTime = millis();
      touch.detected = TouchState::NONE;
    }
    touch.curX = tx; touch.curY = ty;

    // Brightness adjust while held in overlay mode
    if (brightnessOverlay) {
      brightness = map(constrain(tx, 10, 470), 10, 470, 10, 255);
      ledcWrite(27, brightness);
    }

    // Long press detection
    if (touch.detected == TouchState::NONE && millis() - touch.startTime > 800) {
      int dx = abs((int)tx - (int)touch.startX);
      int dy = abs((int)ty - (int)touch.startY);
      if (dx < 15 && dy < 15) {
        touch.detected = TouchState::LONG_PRESS;
        handleGesture(TouchState::LONG_PRESS, tx, ty);
      }
    }
  } else if (touch.pressed) {
    // Touch release - determine gesture
    touch.pressed = false;
    if (touch.detected != TouchState::NONE) return;  // Already handled (long press)

    int dx = (int)touch.curX - (int)touch.startX;
    int dy = (int)touch.curY - (int)touch.startY;
    unsigned long dur = millis() - touch.startTime;

    TouchState::Gesture g = TouchState::NONE;
    if (abs(dx) > 40 || abs(dy) > 40) {
      if (dur < 500) {
        if (abs(dx) > abs(dy)) g = dx > 0 ? TouchState::SWIPE_RIGHT : TouchState::SWIPE_LEFT;
        else g = dy > 0 ? TouchState::SWIPE_DOWN : TouchState::SWIPE_UP;
      }
    } else if (dur < 300 && abs(dx) < 15 && abs(dy) < 15) {
      g = TouchState::TAP;
    }

    if (g != TouchState::NONE) handleGesture(g, touch.curX, touch.curY);
  }
}

// ============== SCREENSAVER ==============
void initMatrix() {
  for (int i = 0; i < MATRIX_COLS; i++) {
    matrixY[i] = random(0, 320);
    matrixSpeed[i] = random(4, 12);
  }
}

void drawScreensaver() {
  int W = tft.width(), H = tft.height();
  // Fade effect: darken screen
  for (int i = 0; i < MATRIX_COLS; i++) {
    int x = i * (W / MATRIX_COLS) + 4;
    // Erase old char
    tft.fillRect(x, matrixY[i] - 16, 14, 16, T.bg);
    // Advance
    matrixY[i] += matrixSpeed[i];
    if (matrixY[i] > H) { matrixY[i] = -16; matrixSpeed[i] = random(4, 12); }
    // Draw new char
    char c = random(33, 126);
    tft.setTextFont(2);
    tft.setTextDatum(TL_DATUM);
    tft.setTextColor(T.green, T.bg);
    tft.drawChar(c, x, matrixY[i]);
    // Dimmer trail
    if (matrixY[i] > 0) {
      tft.setTextColor(tft.color565(0, 40, 0), T.bg);
      char c2 = random(33, 126);
      tft.drawChar(c2, x, matrixY[i] - 16);
    }
  }
  // Clock in center
  tft.setTextFont(7);
  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(T.accent, T.bg);
  unsigned long sec = millis() / 1000;
  char timeBuf[10];
  snprintf(timeBuf, sizeof(timeBuf), "%02d:%02d", (int)(sec / 60) % 60, (int)(sec) % 60);
  tft.setTextPadding(120);
  tft.drawString(timeBuf, W / 2, H / 2);
  tft.setTextPadding(0);
}

// ============== BRIGHTNESS OVERLAY ==============
void drawBrightnessOverlay() {
  int W = tft.width();
  int y = 140;
  tft.fillRoundRect(20, y, W - 40, 40, 8, T.card);
  tft.drawRoundRect(20, y, W - 40, 40, 8, T.accent);
  // Bar
  int barX = 30, barW = W - 60, barH = 10, barY = y + 15;
  tft.fillRoundRect(barX, barY, barW, barH, 4, T.border);
  int fillW = map(brightness, 10, 255, 0, barW);
  tft.fillRoundRect(barX, barY, fillW, barH, 4, T.accent);
  // Label
  tft.setTextFont(1); tft.setTextDatum(MC_DATUM);
  tft.setTextColor(T.text, T.card);
  char buf[16]; snprintf(buf, sizeof(buf), "Brillo: %d%%", brightness * 100 / 255);
  tft.drawString(buf, W / 2, y + 6);
}

// ============== DRAWING ==============
void drawArc(int cx, int cy, int r, int thick, float pct, uint16_t fg, uint16_t bg) {
  for (int a = -135; a <= 135; a++) {
    float rad = a * DEG_TO_RAD;
    float cs = cos(rad), sn = sin(rad);
    bool active = a <= -135 + (int)(270 * pct / 100.0);
    uint16_t c = active ? fg : bg;
    for (int t = 0; t < thick; t++)
      tft.drawPixel(cx + cs * (r - t), cy + sn * (r - t), c);
  }
}

void drawGauge(int cx, int cy, int r, float pct, uint16_t color, const char* label, bool big) {
  drawArc(cx, cy, r, big ? 7 : 5, pct, color, T.border);
  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(color, T.bg);
  tft.setTextFont(big ? 7 : 4);
  char buf[8]; snprintf(buf, sizeof(buf), big ? "%.0f" : "%.0f%%", pct);
  tft.drawString(buf, cx, cy - (big ? 10 : 4));
  if (big) { tft.setTextFont(4); tft.drawString("%", cx + 40, cy - 10); }
  tft.setTextFont(2);
  tft.setTextColor(T.muted, T.bg);
  tft.drawString(label, cx, cy + (big ? 30 : 18));
}

void drawBar(int x, int y, int w, int h, float pct, uint16_t color) {
  tft.fillRoundRect(x, y, w, h, 2, T.border);
  int fw = (int)(w * pct / 100.0);
  if (fw > 2) tft.fillRoundRect(x, y, fw, h, 2, color);
}

void drawGraph(int x, int y, int w, int h, float* data, uint16_t color, const char* label, bool full) {
  tft.fillRoundRect(x, y, w, h, 4, T.card);
  tft.setTextDatum(TL_DATUM);
  tft.setTextFont(full ? 4 : 2);
  tft.setTextColor(full ? T.text : T.muted, T.card);
  tft.drawString(label, x + (full ? 8 : 4), y + 2);
  if (full) {
    float cur = data[(histIdx - 1 + HIST_LEN) % HIST_LEN];
    tft.setTextDatum(TR_DATUM);
    tft.setTextColor(color, T.card);
    char buf[10]; snprintf(buf, sizeof(buf), "%.1f%%", cur);
    tft.drawString(buf, x + w - 8, y + 4);
  }
  int gx = x + (full ? 4 : 2), gy = y + (full ? 36 : 20);
  int gw = w - (full ? 8 : 4), gh = h - (full ? 44 : 24);
  for (int i = 1; i < 4; i++) {
    int ly = gy + gh * i / 4;
    for (int lx = gx; lx < gx + gw; lx += 3) tft.drawPixel(lx, ly, T.border);
  }
  int len = min(HIST_LEN, gw);
  for (int i = 1; i < len; i++) {
    int i1 = (histIdx - len + i - 1 + HIST_LEN) % HIST_LEN;
    int i2 = (histIdx - len + i + HIST_LEN) % HIST_LEN;
    int x1 = gx + gw * (i - 1) / (len - 1);
    int x2 = gx + gw * i / (len - 1);
    int y1 = gy + gh - (int)(gh * data[i1] / 100.0);
    int y2 = gy + gh - (int)(gh * data[i2] / 100.0);
    tft.drawLine(x1, y1, x2, y2, color);
    tft.drawLine(x1, y1 + 1, x2, y2 + 1, color);
  }
}

void drawCores(int x, int y, int w, int h, bool full) {
  tft.fillRoundRect(x, y, w, h, 4, T.card);
  tft.setTextDatum(TL_DATUM);
  tft.setTextFont(full ? 4 : 2);
  tft.setTextColor(full ? T.text : T.muted, T.card);
  tft.drawString("CPU CORES", x + (full ? 8 : 4), y + 2);
  if (full) {
    tft.setTextDatum(TR_DATUM);
    tft.setTextColor(T.accent, T.card);
    char buf[16]; snprintf(buf, sizeof(buf), "%.1f%% avg", cpuTotal);
    tft.drawString(buf, x + w - 8, y + 4);
  }
  if (numCores == 0) return;
  int cols = min(numCores, 12);
  int pad = full ? 24 : 12;
  int gap = full ? 4 : 2;
  int bw = (w - pad) / cols - gap;
  int bh = full ? h - 70 : h - 34;
  int by = y + (full ? 40 : 22);
  for (int i = 0; i < numCores && i < 12; i++) {
    int bx = x + pad / 2 + i * (bw + gap);
    uint16_t c = cpuCores[i] > 90 ? T.red : cpuCores[i] > 60 ? T.yellow : T.accent;
    tft.fillRect(bx, by, bw, bh, T.border);
    int fh = (int)(bh * cpuCores[i] / 100.0);
    if (fh > 0) tft.fillRect(bx, by + bh - fh, bw, fh, c);
    if (full) {
      char buf[4]; snprintf(buf, sizeof(buf), "%d", i);
      tft.setTextDatum(TC_DATUM);
      tft.setTextFont(2);
      tft.setTextColor(T.muted, T.card);
      tft.drawString(buf, bx + bw / 2, by + bh + 2);
    }
  }
}

void drawProcesses(int x, int y, int w, int h, bool full) {
  tft.fillRoundRect(x, y, w, h, 4, T.card);
  tft.setTextDatum(TL_DATUM);
  tft.setTextFont(full ? 4 : 2);
  tft.setTextColor(full ? T.text : T.muted, T.card);
  char hdr[32]; snprintf(hdr, sizeof(hdr), "PROCESOS (%d)", procCount);
  tft.drawString(hdr, x + (full ? 8 : 4), y + 2);

  int fontSize = full ? 2 : 1;
  int rowH = full ? 18 : 11;
  int startY = y + (full ? 36 : 22);
  int maxRows = min(numProcs, (h - (full ? 44 : 26)) / rowH);

  tft.setTextFont(fontSize);
  for (int i = 0; i < maxRows; i++) {
    int py = startY + i * rowH;
    tft.setTextDatum(TL_DATUM);
    tft.setTextColor(T.text, T.card);
    tft.drawString(topProcs[i].name, x + (full ? 8 : 4), py);
    char buf[10];
    tft.setTextDatum(TR_DATUM);
    tft.setTextColor(topProcs[i].cpu > 20 ? T.red : T.muted, T.card);
    snprintf(buf, sizeof(buf), "%.0f%%", topProcs[i].cpu);
    tft.drawString(buf, x + w - (full ? 60 : 34), py);
    tft.setTextColor(topProcs[i].mem > 3 ? T.yellow : T.muted, T.card);
    snprintf(buf, sizeof(buf), "%.1f%%", topProcs[i].mem);
    tft.drawString(buf, x + w - (full ? 8 : 4), py);
  }
}

void drawStatusBar() {
  int W = tft.width(), H = tft.height();
  int sy = H - 18;
  tft.drawFastHLine(0, sy, W, T.border);
  tft.setTextFont(1);
  // View dots
  for (int i = 0; i < NUM_VIEWS; i++)
    tft.fillCircle(W / 2 - 20 + i * 10, sy + 8, 3, i == currentView ? T.accent : T.border);
  // Status
  tft.setTextPadding(50);
  tft.setTextDatum(TL_DATUM);
  tft.setTextColor(dataOk ? T.green : T.red, T.bg);
  tft.drawString(dataOk ? "ONLINE " : "OFFLINE", 4, sy + 4);
  // Server name + IP
  tft.setTextPadding(180);
  tft.setTextDatum(TR_DATUM);
  tft.setTextColor(T.muted, T.bg);
  char buf[48];
  if (numServers > 1)
    snprintf(buf, sizeof(buf), "%s [%d/%d] %ddBm", serverHostname, activeServer + 1, numServers, WiFi.RSSI());
  else
    snprintf(buf, sizeof(buf), "%s %ddBm", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  tft.drawString(buf, W - 4, sy + 4);
  tft.setTextPadding(0);
}

// ============== VIEWS ==============
void drawDashboard() {
  int W = tft.width();
  if (viewChanged) {
    tft.fillScreen(T.bg);
    tft.setTextDatum(TL_DATUM);
    tft.setTextFont(4);
    tft.setTextColor(T.text, T.bg);
    tft.drawString(numServers > 0 ? serverHostname : "CHEMMONITOR", 8, 4);
    tft.drawFastHLine(0, 28, W, T.border);
    tft.drawFastHLine(0, 115, W, T.border);
  }
  drawGauge(55, 72, 32, cpuTotal, gaugeColor(cpuTotal), "CPU", false);
  drawGauge(155, 72, 32, ramPct, ramPct > 70 ? T.yellow : T.cyan, "RAM", false);
  drawGauge(255, 72, 32, diskPct, diskPct > 70 ? T.yellow : T.yellow, "DISCO", false);

  char buf[32], buf2[16], line[48];
  tft.setTextFont(2);
  // RAM
  fmtBytes(ramUsed, buf, 32); fmtBytes(ramTotal, buf2, 16);
  tft.setTextColor(T.text, T.bg); tft.setTextDatum(TR_DATUM); tft.setTextPadding(100);
  snprintf(line, sizeof(line), "%s / %s", buf, buf2);
  tft.drawString(line, W - 6, 34);
  drawBar(320, 52, W - 326, 8, ramPct, T.cyan);
  // Disk
  fmtBytes(diskUsed, buf, 32); fmtBytes(diskTotal, buf2, 16);
  snprintf(line, sizeof(line), "%s / %s", buf, buf2);
  tft.drawString(line, W - 6, 64);
  tft.setTextPadding(0);
  drawBar(320, 82, W - 326, 8, diskPct, T.yellow);
  // Extra info
  tft.setTextDatum(TL_DATUM); tft.setTextPadding(76);
  tft.setTextFont(1);
  // Temp + Ping + Uptime
  tft.setTextColor(T.muted, T.bg);
  char extra[60];
  char uptBuf[16]; fmtUptime(uptimeSec, uptBuf, 16);
  snprintf(extra, sizeof(extra), "Temp:%.0fC Ping:%.0fms Up:%s", cpuTemp > 0 ? cpuTemp : 0, pingMs > 0 ? pingMs : 0, uptBuf);
  tft.drawString(extra, 320, 95);
  // Net
  tft.setTextColor(T.green, T.bg);
  fmtBytes(netSent, buf, 32); snprintf(line, sizeof(line), "TX %s", buf);
  tft.drawString(line, 320, 105);
  tft.setTextColor(T.yellow, T.bg);
  fmtBytes(netRecv, buf, 32); snprintf(line, sizeof(line), "RX %s", buf);
  tft.drawString(line, 400, 105);
  tft.setTextPadding(0);

  drawGraph(2, 118, W / 2 - 3, 80, cpuHist, T.accent, "CPU", false);
  drawGraph(W / 2 + 1, 118, W / 2 - 3, 80, ramHist, T.cyan, "RAM", false);
  drawCores(2, 202, W / 2 - 3, 80, false);
  drawProcesses(W / 2 + 1, 202, W / 2 - 3, 80, false);
}

void drawCPUView() {
  int W = tft.width(), H = tft.height();
  if (viewChanged) tft.fillScreen(T.bg);
  drawGauge(80, 80, 55, cpuTotal, gaugeColor(cpuTotal), "CPU", true);
  tft.setTextFont(4); tft.setTextDatum(TL_DATUM); tft.setTextPadding(100);
  tft.setTextColor(T.text, T.bg);
  char buf[32]; snprintf(buf, sizeof(buf), "%d cores", numCores);
  tft.drawString(buf, 180, 40);
  tft.setTextFont(2); tft.setTextColor(T.muted, T.bg);
  snprintf(buf, sizeof(buf), "Temp: %.0fC", cpuTemp > 0 ? cpuTemp : 0);
  tft.drawString(buf, 180, 70);
  tft.setTextPadding(0);
  if (numCores > 0) {
    int bw = min(20, (W - 200) / numCores - 2);
    for (int i = 0; i < numCores && i < 12; i++) {
      int bx = 180 + i * (bw + 2); int by = 95, bh = 30;
      uint16_t c = cpuCores[i] > 90 ? T.red : cpuCores[i] > 60 ? T.yellow : T.accent;
      tft.fillRect(bx, by, bw, bh, T.border);
      int fh = (int)(bh * cpuCores[i] / 100.0);
      if (fh > 0) tft.fillRect(bx, by + bh - fh, bw, fh, c);
    }
  }
  drawGraph(4, 138, W - 8, H - 160, cpuHist, T.accent, "CPU HISTORY", true);
}

void drawRAMView() {
  int W = tft.width(), H = tft.height();
  if (viewChanged) tft.fillScreen(T.bg);
  drawGauge(80, 80, 55, ramPct, ramPct > 70 ? T.yellow : T.cyan, "RAM", true);
  char buf[32], buf2[16], line[48];
  tft.setTextFont(4); tft.setTextDatum(TL_DATUM); tft.setTextPadding(140);
  fmtBytes(ramUsed, buf, 32); fmtBytes(ramTotal, buf2, 16);
  tft.setTextColor(T.text, T.bg);
  snprintf(line, sizeof(line), "%s / %s", buf, buf2);
  tft.drawString(line, 180, 40);
  tft.setTextFont(2); tft.setTextColor(T.muted, T.bg);
  snprintf(line, sizeof(line), "Swap: %.0f%%  Ping: %.0fms", swapPct, pingMs > 0 ? pingMs : 0);
  tft.drawString(line, 180, 70);
  fmtBytes(diskUsed, buf, 32); fmtBytes(diskTotal, buf2, 16);
  tft.setTextColor(T.yellow, T.bg);
  snprintf(line, sizeof(line), "Disco: %s/%s %.0f%%", buf, buf2, diskPct);
  tft.drawString(line, 180, 92);
  tft.setTextPadding(0);
  drawBar(180, 112, W - 190, 10, diskPct, T.yellow);
  drawGraph(4, 138, W - 8, H - 160, ramHist, T.cyan, "RAM HISTORY", true);
}

void drawCoresView() {
  int W = tft.width(), H = tft.height();
  if (viewChanged) tft.fillScreen(T.bg);
  drawCores(4, 4, W - 8, H - 24, true);
}

void drawProcessesView() {
  int W = tft.width(), H = tft.height();
  if (viewChanged) tft.fillScreen(T.bg);
  drawProcesses(4, 4, W - 8, H - 24, true);
}

void drawCurrentView() {
  switch (currentView) {
    case 0: drawDashboard(); break;
    case 1: drawCPUView(); break;
    case 2: drawRAMView(); break;
    case 3: drawCoresView(); break;
    case 4: drawProcessesView(); break;
  }
  drawStatusBar();
  viewChanged = false;
}

// ============== SETUP MODE SCREEN ==============
void drawSetupScreen() {
  tft.fillScreen(T.bg);
  tft.setTextDatum(MC_DATUM);
  tft.setTextFont(4);
  tft.setTextColor(T.accent, T.bg);
  tft.drawString("CHEMMONITOR", tft.width() / 2, 40);

  tft.setTextFont(2);
  tft.setTextColor(T.text, T.bg);
  tft.drawString("Modo Configuracion", tft.width() / 2, 80);

  tft.setTextColor(T.cyan, T.bg);
  tft.drawString("1. Conectate al WiFi:", tft.width() / 2, 120);
  tft.setTextFont(4);
  tft.setTextColor(T.green, T.bg);
  tft.drawString(AP_SSID, tft.width() / 2, 150);
  tft.setTextFont(2);
  tft.setTextColor(T.muted, T.bg);
  tft.drawString("Pass: " AP_PASS, tft.width() / 2, 180);

  tft.setTextColor(T.cyan, T.bg);
  tft.drawString("2. Abre en el navegador:", tft.width() / 2, 210);
  tft.setTextFont(4);
  tft.setTextColor(T.green, T.bg);
  tft.drawString("192.168.4.1", tft.width() / 2, 240);

  // Reset button
  tft.fillRoundRect(tft.width() / 2 - 80, 270, 160, 28, 6, T.red);
  tft.setTextFont(2);
  tft.setTextColor(T.text, T.red);
  tft.drawString("RESETEAR CONFIG", tft.width() / 2, 278);

  tft.setTextFont(1);
  tft.setTextColor(T.muted, T.bg);
  tft.drawString("ChemMonitor ESP32 - by ChemaDev & ClaudeCode", tft.width() / 2, 308);
}

// ============== FETCH TASK (Core 0) ==============
void fetchTask(void* param) {
  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(FETCH_INTERVAL));

    // Process kill request from Core 1
    if (killPid >= 0 && numServers > 0 && WiFi.status() == WL_CONNECTED) {
      HTTPClient http;
      String url = String(servers[activeServer].url) + "/api/kill/" + String(killPid);
      http.begin(url);
      http.setTimeout(2000);
      http.sendRequest("POST");
      http.end();
      killPid = -1;
    }

    if (numServers == 0 || WiFi.status() != WL_CONNECTED) {
      failCount++;
      if (failCount >= 5) dataOk = false;
      continue;
    }

    // Fetch hostname once
    if (serverHostname[0] == 0) {
      HTTPClient http;
      String url = String(servers[activeServer].url) + "/api/config";
      http.begin(url);
      http.setTimeout(2000);
      if (http.GET() == 200) {
        JsonDocument doc;
        if (!deserializeJson(doc, http.getString())) {
          strlcpy(serverHostname, doc["hostname"] | "Server", sizeof(serverHostname));
        }
      }
      http.end();
    }

    // Fetch stats
    HTTPClient http;
    String url = String(servers[activeServer].url) + "/api/stats";
    http.begin(url);
    http.setTimeout(2000);
    int code = http.GET();
    if (code == 200) {
      xSemaphoreTake(dataMutex, portMAX_DELAY);
      dataOk = true;
      failCount = 0;
      String payload = http.getString();
      JsonDocument doc;
      if (!deserializeJson(doc, payload)) {
        cpuTotal = doc["cpu"]["total"] | 0.0f;
        JsonArray cores = doc["cpu"]["cores"];
        numCores = min((int)cores.size(), 16);
        for (int i = 0; i < numCores; i++) cpuCores[i] = cores[i] | 0.0f;
        ramPct = doc["memory"]["percent"] | 0.0f;
        ramUsed = doc["memory"]["used"] | 0.0f;
        ramTotal = doc["memory"]["total"] | 0.0f;
        diskPct = doc["disk"]["percent"] | 0.0f;
        diskUsed = doc["disk"]["used"] | 0.0f;
        diskTotal = doc["disk"]["total"] | 0.0f;
        swapPct = doc["swap"]["percent"] | 0.0f;
        swapUsed = doc["swap"]["used"] | 0.0f;
        swapTotal = doc["swap"]["total"] | 0.0f;
        cpuTemp = doc["temperature"]["cpu"] | -1.0f;
        gpuTemp = doc["temperature"]["gpu"] | -1.0f;
        pingMs = doc["ping"] | -1.0f;
        uptimeSec = doc["uptime"] | 0.0f;
        netSent = doc["network"]["bytes_sent"] | 0.0f;
        netRecv = doc["network"]["bytes_recv"] | 0.0f;
        procCount = doc["process_count"] | 0;
        JsonArray procs = doc["processes"];
        int newNumProcs = min((int)procs.size(), 20);
        // Clear old entries if fewer results this time
        if (newNumProcs < numProcs) {
          memset(topProcs + newNumProcs, 0, (numProcs - newNumProcs) * sizeof(ProcInfo));
        }
        numProcs = newNumProcs;
        for (int i = 0; i < numProcs; i++) {
          strlcpy(topProcs[i].name, procs[i]["name"] | "?", sizeof(topProcs[i].name));
          topProcs[i].cpu = procs[i]["cpu_percent"] | 0.0f;
          topProcs[i].mem = procs[i]["memory_percent"] | 0.0f;
          topProcs[i].pid = procs[i]["pid"] | 0;
        }
        cpuHist[histIdx] = cpuTotal;
        ramHist[histIdx] = ramPct;
        histIdx = (histIdx + 1) % HIST_LEN;
      }
      fetchDone = true;
      xSemaphoreGive(dataMutex);
    } else {
      failCount++;
      if (failCount >= 5) dataOk = false;
    }
    http.end();
  }
}

// ============== SETUP ==============
void setup() {
  Serial.begin(115200);
  ledcAttach(27, 5000, 8);
  ledcWrite(27, brightness);
  initMatrix();

  tft.init();
  tft.setRotation(3);
  tft.fillScreen(T.bg);
  uint16_t calData[5] = {300, 3600, 300, 3600, 7};
  tft.setTouch(calData);

  // Init SPIFFS
  if (!SPIFFS.begin(true)) {
    Serial.println("SPIFFS mount failed");
  }

  // Load saved config
  loadConfig();

  bool hasConfig = (wifiSSID[0] != 0 && numServers > 0);

  if (!hasConfig) {
    // === SETUP MODE ===
    setupMode = true;
    Serial.println("No config found - Starting AP setup mode");

    WiFi.mode(WIFI_AP_STA);
    WiFi.softAP(AP_SSID, AP_PASS);
    // Also try connecting to saved WiFi if SSID exists (for test endpoint)
    if (wifiSSID[0] != 0) WiFi.begin(wifiSSID, wifiPass);

    dnsServer.start(53, "*", IPAddress(192, 168, 4, 1));
    setupWebServer();
    drawSetupScreen();

    Serial.printf("AP Started: %s (192.168.4.1)\n", AP_SSID);
  } else {
    // === MONITOR MODE ===
    setupMode = false;

    tft.setTextDatum(MC_DATUM);
    tft.setTextFont(4);
    tft.setTextColor(T.accent, T.bg);
    tft.drawString("CHEMMONITOR", tft.width() / 2, tft.height() / 2 - 30);
    tft.setTextFont(2);
    tft.setTextColor(T.muted, T.bg);
    tft.drawString("Conectando WiFi...", tft.width() / 2, tft.height() / 2);

    WiFi.mode(WIFI_AP_STA);
    WiFi.softAP(AP_SSID, AP_PASS);
    WiFi.setAutoReconnect(true);
    WiFi.persistent(true);
    WiFi.begin(wifiSSID, wifiPass);

    int att = 0;
    while (WiFi.status() != WL_CONNECTED && att < 40) { delay(500); att++; }

    if (WiFi.status() == WL_CONNECTED) {
      Serial.printf("WiFi OK: %s\n", WiFi.localIP().toString().c_str());

      // Verify WiFi is really stable
      delay(2000);
      if (WiFi.status() != WL_CONNECTED) {
        Serial.println("WiFi dropped after connect, retrying...");
        WiFi.disconnect();
        WiFi.begin(wifiSSID, wifiPass);
        int att2 = 0;
        while (WiFi.status() != WL_CONNECTED && att2 < 20) { delay(500); att2++; }
      }
      Serial.printf("WiFi RSSI: %d dBm\n", WiFi.RSSI());
      Serial.printf("WiFi status: %d\n", WiFi.status());
      Serial.printf("IP: %s\n", WiFi.localIP().toString().c_str());
      Serial.printf("Gateway: %s\n", WiFi.gatewayIP().toString().c_str());
      Serial.printf("MAC: %s\n", WiFi.macAddress().c_str());

      // Start web server + captive portal on AP
      dnsServer.start(53, "*", IPAddress(192, 168, 4, 1));
      setupWebServer();
      Serial.println("Web server started on port 80");
      Serial.printf("Access at: http://%s/ or http://192.168.4.1/\n", WiFi.localIP().toString().c_str());

      // Show waiting screen with IP
      tft.fillScreen(T.bg);
      tft.setTextDatum(MC_DATUM);
      tft.setTextFont(4);
      tft.setTextColor(T.accent, T.bg);
      tft.drawString("CHEMMONITOR", tft.width() / 2, 30);

      tft.setTextFont(2);
      tft.setTextColor(T.green, T.bg);
      tft.drawString("WiFi conectado!", tft.width() / 2, 70);

      // Show IPs big
      tft.setTextFont(4);
      tft.setTextColor(T.cyan, T.bg);
      tft.drawString(WiFi.localIP().toString(), tft.width() / 2, 110);

      tft.setTextFont(2);
      tft.setTextColor(T.muted, T.bg);
      tft.drawString("Config: WiFi \"" AP_SSID "\" > 192.168.4.1", tft.width() / 2, 145);

      // Show server target
      tft.drawFastHLine(40, 170, tft.width() - 80, T.border);
      tft.setTextColor(T.yellow, T.bg);
      String srvUrl = String(servers[activeServer].url) + "/api/stats";
      Serial.printf("Server URL: %s\n", srvUrl.c_str());
      Serial.printf("Active server: %d/%d, name: %s\n", activeServer, numServers, servers[activeServer].name);
      tft.drawString("Esperando servidor Python:", tft.width() / 2, 185);
      tft.setTextColor(T.text, T.bg);
      tft.drawString(srvUrl, tft.width() / 2, 205);

      // Non-blocking wait: try server but keep web server alive
      bool srvOk = false;
      for (int retry = 0; retry < 30 && !srvOk; retry++) {
        // Keep web server + captive portal responsive during wait!
        for (int w = 0; w < 20; w++) {
          dnsServer.processNextRequest();
          webServer.handleClient();
          delay(100);
        }

        HTTPClient http;
        http.begin(srvUrl);
        http.setTimeout(2000);
        int code = http.GET();
        http.end();
        Serial.printf("Retry %d: HTTP %d\n", retry + 1, code);

        tft.setTextPadding(300);
        tft.setTextDatum(MC_DATUM);
        if (code == 200) {
          srvOk = true;
          tft.setTextColor(T.green, T.bg);
          tft.drawString("Servidor conectado!", tft.width() / 2, 240);
        } else {
          tft.setTextColor(T.muted, T.bg);
          char msg[48]; snprintf(msg, sizeof(msg), "Reintentando... (%d/30)  codigo:%d", retry + 1, code);
          tft.drawString(msg, tft.width() / 2, 240);
        }
        tft.setTextPadding(0);
      }

      tft.setTextFont(1);
      tft.setTextColor(T.muted, T.bg);
      tft.drawString("ChemMonitor ESP32 - by ChemaDev & ClaudeCode", tft.width() / 2, 300);

      // AP stays on always for config access via 192.168.4.1
      delay(srvOk ? 1000 : 2000);
    } else {
      tft.setTextColor(T.red, T.bg);
      tft.drawString("WiFi Error - Modo setup", tft.width() / 2, tft.height() / 2 + 20);
      setupMode = true;
      WiFi.softAP(AP_SSID, AP_PASS);
      dnsServer.start(53, "*", IPAddress(192, 168, 4, 1));
      setupWebServer();
      drawSetupScreen();
    }

    if (!setupMode) {
      dataMutex = xSemaphoreCreateMutex();
      xTaskCreatePinnedToCore(fetchTask, "fetch", 8192, NULL, 1, NULL, 0);
      touch.lastActivity = millis();
      viewChanged = true;
    }
  }

}

// ============== LOOP ==============
void loop() {
  if (setupMode) {
    dnsServer.processNextRequest();
    webServer.handleClient();
    checkTouch();
    delay(10);
    return;
  }

  // WiFi reconnect
  if (WiFi.status() != WL_CONNECTED && millis() - lastWifiCheck > 5000) {
    lastWifiCheck = millis();
    WiFi.disconnect();
    WiFi.begin(wifiSSID, wifiPass);
  }

  dnsServer.processNextRequest();
  webServer.handleClient();
  checkTouch();

  // Screensaver
  if (!screensaverActive && millis() - touch.lastActivity > SCREENSAVER_TIMEOUT) {
    screensaverActive = true;
    tft.fillScreen(T.bg);
  }
  if (screensaverActive) {
    drawScreensaver();
    delay(50);
    return;
  }

  // Animated values (ease toward target)
  dispCpu += (cpuTotal - dispCpu) * 0.3;
  dispRam += (ramPct - dispRam) * 0.3;
  dispDisk += (diskPct - dispDisk) * 0.3;

  if (viewChanged) drawCurrentView();
  if (fetchDone) { fetchDone = false; drawCurrentView(); }
  if (brightnessOverlay) drawBrightnessOverlay();

  delay(20);
}
