/**
 * ============================================================
 *  Delta Robot Controller — ESP32 + MG996R × 3  (FreeRTOS)
 * ============================================================
 *  底座半径 (f): 57.74 mm   主动臂 (rf): 100 mm
 *  从动臂 (re): 259 mm      动平台 (e):   20 mm
 *
 *  舵机: 臂0→D14(0°)  臂1→D12(120°)  臂2→D13(240°)
 *  坐标系: 原点底座中心，Z 向下为正
 *  驱动: LEDC   网络: WiFi STA + TCP :8266  协议: 纯文本 \n
 *
 *  任务布局:
 *    Core 1: MotionTask(5)  CommandTask(3)  WeighTask(3)  ButtonTask(2)
 *    Core 0: NetworkTask(3) SerialTask(2)
 * ============================================================
 */

#include <Arduino.h>
#include <math.h>
#include <WiFi.h>
#include <Preferences.h>
#include "HX711.h"

/* ── WiFi & TCP ── */
static const int TCP_PORT = 8266;
// SSID and password are stored in NVS via Preferences (namespace "wifi").
// On first boot (or after "setwifi <ssid> <pass>" serial command) they are
// written to flash and never appear in source code.
static char WIFI_SSID[64]     = "";
static char WIFI_PASSWORD[64] = "";

static WiFiServer tcpServer(TCP_PORT);
static WiFiClient tcpClient;

/* ── 机器人几何参数 (mm) ── */
static const float F  = 57.74f;
static const float E  = 20.0f;
static const float RF = 100.0f;
static const float RE = 259.0f;

/* ── 舵机 & LEDC ── */
static const int   SERVO_PIN[3]          = {14, 12, 13};
static const int   LEDC_FREQ             = 50;
static const int   LEDC_RESOLUTION       = 16;
static const int   LEDC_CHANNEL[3]       = {0, 1, 2};
static const int   PULSE_MIN_US          = 500;
static const int   PULSE_MAX_US          = 2500;
static const float MAX_SPEED_DEG_PER_SEC = 45.0f;
static const int   MOTION_INTERVAL_MS    = 10;
static const float SERVO_MIN_DEG         = 30.0f;
static const float SERVO_MAX_DEG         = 150.0f;
static const float SERVO_HOME_DEG        = 90.0f;

/* ── 气泵继电器 & 按钮 ── */
static const int   RELAY_PIN  = 26;
static const int   BUTTON_PIN = 27;

/* ── 称重台预设位置 ── */
static const float WEIGH_X = 195.0f;
static const float WEIGH_Y =   0.0f;
static const float WEIGH_Z = 150.0f;

/* ── HX711 ── */
static const int      HX711_DOUT        = 33;
static const int      HX711_SCK         = 32;
static const float    HX711_CALIBRATION = 2280.0f;  // 需按实物校准
static const uint32_t WEIGH_SETTLE_MS   = 1000;
static HX711 scale;

/* ── 工作空间安全限制 (mm) ── */
static const float WS_X_MIN = -100.0f, WS_X_MAX = 100.0f;
static const float WS_Y_MIN = -100.0f, WS_Y_MAX = 100.0f;
static const float WS_Z_MIN =   50.0f, WS_Z_MAX = 280.0f;

/* ── 几何常数 ── */
static const float ARM_ANGLE_DEG[3] = {0.0f, 120.0f, 240.0f};
static float cosA[3], sinA[3];

/* ── 共享状态（受 xStateMutex 保护）── */
static float currentAngle[3] = {90.0f, 90.0f, 90.0f};
static float currentX = -96.0f, currentY = 100.0f, currentZ = 264.0f;
static bool  pumpOn   = false;

/* ── RTOS 句柄 ── */
#define CMD_LEN 64
static QueueHandle_t     xCmdQueue;     // char[CMD_LEN]，深度 8，入站命令
static QueueHandle_t     xMotionQueue;  // float[3]，深度 1，目标舵机角度
static QueueHandle_t     xWeighQueue;   // uint8_t，深度 1，称重指令
static SemaphoreHandle_t xStateMutex;   // 保护 currentAngle/X/Y/Z/pumpOn
static SemaphoreHandle_t xClientMutex;  // 保护 tcpClient

#define WEIGH_CMD_MEASURE 0
#define WEIGH_CMD_TARE    1

/* ============================================================
   LEDC 工具函数
============================================================ */
static uint32_t pulseToCounts(uint32_t pulse_us) {
    uint32_t period_us = 1000000UL / LEDC_FREQ;
    uint32_t maxCnt    = (1UL << LEDC_RESOLUTION) - 1;
    return (uint32_t)((uint64_t)pulse_us * maxCnt / period_us);
}

static void servoWriteDeg(int ch, float deg) {
    if (deg < SERVO_MIN_DEG) deg = SERVO_MIN_DEG;
    if (deg > SERVO_MAX_DEG) deg = SERVO_MAX_DEG;
    float    frac     = deg / 180.0f;
    uint32_t pulse_us = (uint32_t)(PULSE_MIN_US + frac * (PULSE_MAX_US - PULSE_MIN_US));
    ledcWrite(LEDC_CHANNEL[ch], pulseToCounts(pulse_us));
}

static void servosInit() {
    for (int i = 0; i < 3; i++) {
        ledcSetup(LEDC_CHANNEL[i], LEDC_FREQ, LEDC_RESOLUTION);
        ledcAttachPin(SERVO_PIN[i], LEDC_CHANNEL[i]);
        servoWriteDeg(i, SERVO_HOME_DEG);
        currentAngle[i] = SERVO_HOME_DEG;
    }
}

/* ── 上电慢速归位（仅 setup 调用，调度器启动前）── */
static void servoMoveToBlocking(const float tgt[3], int steps = 30, int stepMs = 12) {
    float start[3];
    for (int i = 0; i < 3; i++) start[i] = currentAngle[i];
    for (int s = 1; s <= steps; s++) {
        float t = (float)s / steps;
        for (int i = 0; i < 3; i++) {
            float a = start[i] + t * (tgt[i] - start[i]);
            servoWriteDeg(i, a);
            currentAngle[i] = a;
        }
        delay(stepMs);
    }
}

/* ============================================================
   逆运动学
============================================================ */
static bool ikSingleArm(float xp, float yp, float zp, int armIdx, float &angleDeg) {
    float theta = ARM_ANGLE_DEG[armIdx] * (float)M_PI / 180.0f;
    float xRot  = xp * cosf(theta) + yp * sinf(theta);
    float delta = F - E;
    float x1    = xRot - delta;
    float z1    = zp;

    float A = -2.0f * RF * x1;
    float B = -2.0f * RF * z1;
    float C = RE * RE - RF * RF - x1 * x1 - z1 * z1;

    float R = sqrtf(A * A + B * B);
    if (R < 1e-6f) return false;
    float ratio = C / R;
    if (fabsf(ratio) > 1.0f) return false;

    float phi    = atan2f(B, A);
    float alpha  = acosf(ratio);
    float servo1 = 90.0f + (phi + alpha) * 180.0f / (float)M_PI;
    float servo2 = 90.0f + (phi - alpha) * 180.0f / (float)M_PI;
    bool  v1     = (servo1 >= SERVO_MIN_DEG && servo1 <= SERVO_MAX_DEG);
    bool  v2     = (servo2 >= SERVO_MIN_DEG && servo2 <= SERVO_MAX_DEG);

    if (!v1 && !v2) return false;
    float d1 = fabsf(servo1 - 90.0f), d2 = fabsf(servo2 - 90.0f);
    if (v1 && v2) angleDeg = (d1 <= d2) ? servo1 : servo2;
    else          angleDeg = v1 ? servo1 : servo2;
    return true;
}

static bool deltaIK(float x, float y, float z, float out[3]) {
    for (int i = 0; i < 3; i++)
        if (!ikSingleArm(x, y, z, i, out[i])) return false;
    return true;
}

/* ============================================================
   工作空间安全检查
============================================================ */
static bool checkWorkspace(float x, float y, float z) {
    if (x == WEIGH_X && y == WEIGH_Y && z == WEIGH_Z) return true;
    return (x >= WS_X_MIN && x <= WS_X_MAX &&
            y >= WS_Y_MIN && y <= WS_Y_MAX &&
            z >= WS_Z_MIN && z <= WS_Z_MAX);
}

/* ============================================================
   线程安全输出（Serial 本身线程安全；tcpClient 受 xClientMutex 保护）
============================================================ */
static void netPrint(const char* s) {
    Serial.print(s);
    xSemaphoreTake(xClientMutex, portMAX_DELAY);
    if (tcpClient && tcpClient.connected()) tcpClient.print(s);
    xSemaphoreGive(xClientMutex);
}
static void netPrintln(const char* s) {
    char buf[CMD_LEN + 2];
    snprintf(buf, sizeof(buf), "%s\n", s);
    netPrint(buf);
}
static void netPrintf(const char* fmt, ...) {
    char buf[128];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    netPrint(buf);
}

/* ============================================================
   moveTo — 由 CommandTask 调用
============================================================ */
static void moveTo(float x, float y, float z) {
    netPrintf("[CMD] Target: (%.2f, %.2f, %.2f)\n", x, y, z);

    if (!checkWorkspace(x, y, z)) {
        netPrintf("[ERR] Out of safe workspace! X[%.0f,%.0f] Y[%.0f,%.0f] Z[%.0f,%.0f]\n",
                  WS_X_MIN, WS_X_MAX, WS_Y_MIN, WS_Y_MAX, WS_Z_MIN, WS_Z_MAX);
        return;
    }

    float angles[3];
    if (!deltaIK(x, y, z, angles)) {
        netPrintln("[ERR] IK no solution! Point unreachable.");
        return;
    }

    for (int i = 0; i < 3; i++) {
        if (angles[i] < SERVO_MIN_DEG || angles[i] > SERVO_MAX_DEG) {
            netPrintf("[ERR] Arm %d angle %.1f out of range [%.0f,%.0f]!\n",
                      i, angles[i], SERVO_MIN_DEG, SERVO_MAX_DEG);
            return;
        }
    }

    xQueueOverwrite(xMotionQueue, angles);

    xSemaphoreTake(xStateMutex, portMAX_DELAY);
    currentX = x; currentY = y; currentZ = z;
    xSemaphoreGive(xStateMutex);

    netPrintf("[OK] %.2f,%.2f,%.2f\n", x, y, z);
}

/* ============================================================
   handleCommand — 由 CommandTask 调用
============================================================ */
static void handleCommand(const char* raw) {
    char cmd[CMD_LEN];
    strncpy(cmd, raw, CMD_LEN - 1);
    cmd[CMD_LEN - 1] = '\0';

    // trim trailing whitespace
    int len = strlen(cmd);
    while (len > 0 && (cmd[len-1] == ' ' || cmd[len-1] == '\r')) cmd[--len] = '\0';
    if (len == 0) return;

    // lowercase copy for comparison
    char lo[CMD_LEN];
    for (int i = 0; i <= len; i++) lo[i] = tolower((unsigned char)cmd[i]);

    if (strcmp(lo, "home") == 0) {
        netPrintln("[CMD] Homing...");
        moveTo(-96.0f, 100.0f, 264.0f);
    } else if (strcmp(lo, "pos") == 0) {
        xSemaphoreTake(xStateMutex, portMAX_DELAY);
        float x = currentX, y = currentY, z = currentZ;
        float a0 = currentAngle[0], a1 = currentAngle[1], a2 = currentAngle[2];
        xSemaphoreGive(xStateMutex);
        netPrintf("[POS] %.2f,%.2f,%.2f\n", x, y, z);
        netPrintf("[ANG] %.1f,%.1f,%.1f\n", a0, a1, a2);
    } else if (strcmp(lo, "ping") == 0) {
        netPrintln("[PONG]");
    } else if (strcmp(lo, "pump_on") == 0) {
        xSemaphoreTake(xStateMutex, portMAX_DELAY);
        pumpOn = true;
        xSemaphoreGive(xStateMutex);
        digitalWrite(RELAY_PIN, HIGH);
        netPrintln("[PUMP] ON");
    } else if (strcmp(lo, "pump_off") == 0) {
        xSemaphoreTake(xStateMutex, portMAX_DELAY);
        pumpOn = false;
        xSemaphoreGive(xStateMutex);
        digitalWrite(RELAY_PIN, LOW);
        netPrintln("[PUMP] OFF");
    } else if (strcmp(lo, "pump") == 0) {
        xSemaphoreTake(xStateMutex, portMAX_DELAY);
        bool on = pumpOn;
        xSemaphoreGive(xStateMutex);
        netPrintf("[PUMP] %s\n", on ? "ON" : "OFF");
    } else if (strcmp(lo, "weigh") == 0) {
        netPrintln("[CMD] Moving to weigh position...");
        moveTo(WEIGH_X, WEIGH_Y, WEIGH_Z);
    } else if (strcmp(lo, "weight") == 0) {
        uint8_t c = WEIGH_CMD_MEASURE;
        xQueueOverwrite(xWeighQueue, &c);
    } else if (strcmp(lo, "tare") == 0) {
        uint8_t c = WEIGH_CMD_TARE;
        xQueueOverwrite(xWeighQueue, &c);
    } else if (strncmp(lo, "setwifi ", 8) == 0) {
        // Usage (serial only): setwifi <ssid> <password>
        // Saves credentials to NVS flash; reboot to apply.
        char tmp[CMD_LEN];
        strncpy(tmp, cmd + 8, CMD_LEN - 1);
        tmp[CMD_LEN - 1] = '\0';
        char *sp = strchr(tmp, ' ');
        if (!sp) { netPrintln("[ERR] Usage: setwifi <ssid> <password>"); return; }
        *sp = '\0';
        Preferences prefs;
        prefs.begin("wifi", false);
        prefs.putString("ssid", tmp);
        prefs.putString("pass", sp + 1);
        prefs.end();
        netPrintln("[OK] WiFi credentials saved. Reboot to apply.");
    } else {
        // try "x,y,z"
        char *p1 = strchr(cmd, ',');
        char *p2 = p1 ? strrchr(cmd, ',') : nullptr;
        if (p1 && p2 && p2 != p1) {
            *p1 = '\0'; *p2 = '\0';
            moveTo(atof(cmd), atof(p1 + 1), atof(p2 + 1));
        } else {
            netPrintf("[ERR] Unknown command: %s\n", cmd);
        }
    }
}

/* ============================================================
   taskMotion — Core 1, 优先级 5
   每 MOTION_INTERVAL_MS 推进一步插值，smoothstep ease-in-out
============================================================ */
static void taskMotion(void*) {
    float start[3], target[3];
    int   steps = 0, step = 0;
    bool  active = false;

    for (;;) {
        float newTgt[3];
        if (xQueueReceive(xMotionQueue, newTgt, 0) == pdTRUE) {
            xSemaphoreTake(xStateMutex, portMAX_DELAY);
            float maxDelta = 0.0f;
            for (int i = 0; i < 3; i++) {
                start[i] = currentAngle[i];
                float d = fabsf(newTgt[i] - currentAngle[i]);
                if (d > maxDelta) maxDelta = d;
            }
            xSemaphoreGive(xStateMutex);

            for (int i = 0; i < 3; i++) target[i] = newTgt[i];
            steps  = max(4, (int)(maxDelta / (MAX_SPEED_DEG_PER_SEC * MOTION_INTERVAL_MS * 0.001f)));
            step   = 0;
            active = true;
        }

        if (active) {
            step++;
            float t = (float)step / (float)steps;
            if (t > 1.0f) t = 1.0f;
            float s = t * t * (3.0f - 2.0f * t);  // smoothstep

            xSemaphoreTake(xStateMutex, portMAX_DELAY);
            for (int i = 0; i < 3; i++) {
                float a = start[i] + s * (target[i] - start[i]);
                servoWriteDeg(i, a);
                currentAngle[i] = a;
            }
            xSemaphoreGive(xStateMutex);

            if (t >= 1.0f) active = false;
        }

        vTaskDelay(pdMS_TO_TICKS(MOTION_INTERVAL_MS));
    }
}

/* ============================================================
   taskWeigh — Core 1, 优先级 3
   收到 MEASURE: 采 before → 等待稳定 → 采 after → 回复差值
   收到 TARE:    执行归零
============================================================ */
static void taskWeigh(void*) {
    for (;;) {
        uint8_t cmd;
        xQueueReceive(xWeighQueue, &cmd, portMAX_DELAY);

        if (cmd == WEIGH_CMD_TARE) {
            scale.tare();
            netPrintln("[TARE] Done.");
            continue;
        }

        if (!scale.is_ready()) { netPrintln("[ERR] HX711 not ready."); continue; }
        float before = scale.get_units(10);
        vTaskDelay(pdMS_TO_TICKS(WEIGH_SETTLE_MS));
        if (!scale.is_ready()) { netPrintln("[ERR] HX711 not ready."); continue; }
        float after = scale.get_units(10);
        netPrintf("[WEIGHT] %.2f g\n", after - before);
    }
}

/* ============================================================
   taskButton — Core 1, 优先级 2
   软件防抖 50ms，按下沿 toggle 气泵
============================================================ */
static void taskButton(void*) {
    bool     lastRaw    = HIGH;
    bool     stable     = HIGH;
    uint32_t debounceMs = 0;

    for (;;) {
        bool raw = digitalRead(BUTTON_PIN);
        if (raw != lastRaw) { debounceMs = millis(); lastRaw = raw; }
        if (millis() - debounceMs > 50 && raw != stable) {
            stable = raw;
            if (stable == LOW) {
                xSemaphoreTake(xStateMutex, portMAX_DELAY);
                pumpOn = !pumpOn;
                bool on = pumpOn;
                xSemaphoreGive(xStateMutex);
                digitalWrite(RELAY_PIN, on ? HIGH : LOW);
                netPrintln(on ? "[PUMP] ON (button)" : "[PUMP] OFF (button)");
            }
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

/* ============================================================
   taskCommand — Core 1, 优先级 3
   从 xCmdQueue 取命令并分发
============================================================ */
static void taskCommand(void*) {
    char buf[CMD_LEN];
    for (;;) {
        if (xQueueReceive(xCmdQueue, buf, portMAX_DELAY) == pdTRUE)
            handleCommand(buf);
    }
}

/* ============================================================
   taskSerial — Core 0, 优先级 2
   读串口行，推入 xCmdQueue
============================================================ */
static void taskSerial(void*) {
    char    line[CMD_LEN];
    uint8_t pos = 0;

    for (;;) {
        while (Serial.available()) {
            char c = (char)Serial.read();
            if (c == '\n') {
                line[pos] = '\0';
                if (pos > 0) xQueueSend(xCmdQueue, line, 0);
                pos = 0;
            } else if (c != '\r' && pos < CMD_LEN - 1) {
                line[pos++] = c;
            }
        }
        vTaskDelay(pdMS_TO_TICKS(5));
    }
}

/* ============================================================
   taskNetwork — Core 0, 优先级 3
   WiFi 管理 + TCP accept/receive，推入 xCmdQueue
============================================================ */
static void taskNetwork(void*) {
    Serial.printf("[WiFi] Connecting to %s", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.persistent(false);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    for (int t = 0; t < 40 && WiFi.status() != WL_CONNECTED; t++) {
        vTaskDelay(pdMS_TO_TICKS(500));
        Serial.print('.');
    }
    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("\n[WiFi] IP: %s  TCP:%d\n",
                      WiFi.localIP().toString().c_str(), TCP_PORT);
        tcpServer.begin();
    } else {
        Serial.println("\n[WiFi] FAILED — serial-only mode.");
    }

    char    tcpBuf[CMD_LEN];
    uint8_t tcpPos    = 0;
    uint32_t lastPing = millis();

    for (;;) {
        /* 掉线重连 */
        if (WiFi.status() != WL_CONNECTED) {
            static uint32_t lastReconn = 0;
            if (millis() - lastReconn > 5000) {
                lastReconn = millis();
                Serial.println("[WiFi] Reconnecting...");
                WiFi.reconnect();
            }
            vTaskDelay(pdMS_TO_TICKS(200));
            continue;
        }

        /* 接受新连接 */
        xSemaphoreTake(xClientMutex, portMAX_DELAY);
        bool connected = tcpClient && tcpClient.connected();
        xSemaphoreGive(xClientMutex);

        if (!connected) {
            WiFiClient nc = tcpServer.available();
            if (nc) {
                xSemaphoreTake(xClientMutex, portMAX_DELAY);
                if (tcpClient) tcpClient.stop();
                tcpClient = nc;
                tcpClient.setNoDelay(true);
                xSemaphoreGive(xClientMutex);
                tcpPos  = 0;
                lastPing = millis();
                Serial.printf("[TCP] Client: %s\n", nc.remoteIP().toString().c_str());
                netPrintln("[HELLO] Delta Robot ready.");
            }
        }

        /* 接收数据 */
        xSemaphoreTake(xClientMutex, portMAX_DELAY);
        bool avail = tcpClient && tcpClient.connected() && tcpClient.available();
        xSemaphoreGive(xClientMutex);

        if (avail) {
            xSemaphoreTake(xClientMutex, portMAX_DELAY);
            while (tcpClient.available()) {
                char c = (char)tcpClient.read();
                if (c == '\n') {
                    tcpBuf[tcpPos] = '\0';
                    if (tcpPos > 0) {
                        xQueueSend(xCmdQueue, tcpBuf, 0);
                        lastPing = millis();
                    }
                    tcpPos = 0;
                } else if (c != '\r' && tcpPos < CMD_LEN - 1) {
                    tcpBuf[tcpPos++] = c;
                }
            }
            xSemaphoreGive(xClientMutex);
        }

        /* 超时断开 */
        if (millis() - lastPing > 30000) {
            xSemaphoreTake(xClientMutex, portMAX_DELAY);
            if (tcpClient) { Serial.println("[TCP] Timeout."); tcpClient.stop(); }
            xSemaphoreGive(xClientMutex);
            lastPing = millis();
        }

        vTaskDelay(pdMS_TO_TICKS(5));
    }
}

/* ============================================================
   Arduino 入口
============================================================ */
void setup() {
    Serial.begin(115200);
    delay(500);

    // Load WiFi credentials from NVS (written once via "setwifi <ssid> <pass>" serial command)
    {
        Preferences prefs;
        prefs.begin("wifi", true);
        String s = prefs.getString("ssid", "");
        String p = prefs.getString("pass", "");
        prefs.end();
        if (s.length() == 0) {
            Serial.println("[WARN] No WiFi credentials in NVS. Send: setwifi <ssid> <password>");
        }
        s.toCharArray(WIFI_SSID,     sizeof(WIFI_SSID));
        p.toCharArray(WIFI_PASSWORD, sizeof(WIFI_PASSWORD));
    }

    for (int i = 0; i < 3; i++) {
        float rad = ARM_ANGLE_DEG[i] * (float)M_PI / 180.0f;
        cosA[i] = cosf(rad);
        sinA[i] = sinf(rad);
    }

    Serial.println("\n======================================");
    Serial.println("  Delta Robot — ESP32 FreeRTOS v3.0");
    Serial.println("======================================");

    servosInit();
    Serial.println("[INIT] Servos homed (90 deg)");
    delay(800);

    pinMode(RELAY_PIN, OUTPUT);
    digitalWrite(RELAY_PIN, LOW);
    Serial.println("[INIT] Pump driver initialized (pump OFF)");

    pinMode(BUTTON_PIN, INPUT_PULLUP);
    Serial.println("[INIT] Button initialized");

    scale.begin(HX711_DOUT, HX711_SCK);
    scale.set_scale(HX711_CALIBRATION);
    scale.tare();
    Serial.println("[INIT] HX711 initialized, tared.");
    delay(800);

    xCmdQueue    = xQueueCreate(8, CMD_LEN);
    xMotionQueue = xQueueCreate(1, sizeof(float) * 3);
    xWeighQueue  = xQueueCreate(1, sizeof(uint8_t));
    xStateMutex  = xSemaphoreCreateMutex();
    xClientMutex = xSemaphoreCreateMutex();

    xTaskCreatePinnedToCore(taskMotion,  "Motion",  2048, nullptr, 5, nullptr, 1);
    xTaskCreatePinnedToCore(taskCommand, "Command", 4096, nullptr, 3, nullptr, 1);
    xTaskCreatePinnedToCore(taskWeigh,   "Weigh",   2048, nullptr, 3, nullptr, 1);
    xTaskCreatePinnedToCore(taskButton,  "Button",  1024, nullptr, 2, nullptr, 1);
    xTaskCreatePinnedToCore(taskSerial,  "Serial",  2048, nullptr, 2, nullptr, 0);
    xTaskCreatePinnedToCore(taskNetwork, "Network", 4096, nullptr, 3, nullptr, 0);

    Serial.println("[READY] All tasks started.");
}

void loop() {
    vTaskDelay(portMAX_DELAY);
}
