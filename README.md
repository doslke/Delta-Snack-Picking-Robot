# Delta Robot — VLM Snack Detection + ArUco PID Visual Servo


An ESP32-driven 3-axis Delta robot that uses the Qwen-VL vision-language model to identify snacks on a surface, closes the control loop with an ArUco fiducial marker mounted on the end-effector via iterative PID visual servoing, and automatically picks each item and places it on a load cell for weighing.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Hardware](#hardware)
- [Software Dependencies](#software-dependencies)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [Runtime Controls](#runtime-controls)
- [Pipeline](#pipeline)
- [Firmware TCP Protocol](#firmware-tcp-protocol)
- [PID Tuning](#pid-tuning)
- [Camera Offset Calibration](#camera-offset-calibration)
- [Camera Intrinsics Calibration](#camera-intrinsics-calibration)
- [Inventory File](#inventory-file)
- [Security Notes](#security-notes)

---

## How It Works

1. A fixed overhead USB camera continuously streams 1920×1080 video. Lens distortion is corrected using pre-calibrated intrinsics.
2. An ArUco marker (ID 4, DICT_4X4_50) is glued to the Delta robot's moving platform. The system detects this marker in every frame to know where the end-effector currently is in pixel space.
3. When the user presses **`s`**, the system captures a frame, sends it to the **Qwen-VL** multimodal API, and receives bounding boxes for every snack visible in the scene. If an `inventory.json` file is present, the model is instructed to only return names from that list.
4. For each detected snack, a **PID visual servo loop** runs: it computes the pixel-space error between the ArUco centre and the snack's bounding-box centre, converts that error to a millimetre displacement command via the PID controller, and sends the command to the robot over TCP. This repeats until the error falls below a configurable threshold (default 15 px) or the iteration limit is reached.
5. Once converged, the robot descends slowly until the vacuum pump's contact button triggers (indicating the suction cup has touched the item), then lifts the item to a safe height and carries it to the weighing station.
6. At the weighing station the firmware samples the load cell before and after the item is released, and reports the difference as the item's weight in grams.
7. The system immediately re-scans for the next item and loops until the scene is clear.

---

## Hardware

| Component | Details |
|-----------|---------|
| Controller | ESP32 (dual-core, FreeRTOS) |
| Servos | MG996R × 3 — arm 0 → GPIO 14, arm 1 → GPIO 12, arm 2 → GPIO 13 |
| Pump relay | GPIO 26 — drives the vacuum pump via a relay module |
| Pump contact button | GPIO 27 (INPUT_PULLUP) — pressed when suction cup touches an object |
| Load cell + ADC | HX711 — DOUT → GPIO 33, SCK → GPIO 32 |
| Camera | USB webcam, 1920×1080, mounted overhead and fixed |
| ArUco marker | DICT_4X4_50, ID = 4, 20 mm side length, glued to end-effector |

**Robot geometry (all dimensions in mm)**

```
Base triangle circumradius   f  = 57.74
Upper (active) arm length    rf = 100
Lower (passive) arm length   re = 259
End-effector triangle radius e  = 20

Coordinate frame:
  Origin — centre of the base triangle
  X / Y  — horizontal plane
  Z      — positive downward (towards the workspace)
```

The workspace is software-limited to X ∈ [−100, 100], Y ∈ [−100, 100], Z ∈ [50, 280] mm. The weighing station at (195, 0, 150) is explicitly whitelisted outside this box.

---

## Software Dependencies

**Python (≥ 3.10)**

```bash
pip install opencv-python numpy pillow dashscope python-dotenv
```

| Package | Purpose |
|---------|---------|
| `opencv-python` | Camera capture, ArUco detection, image display |
| `numpy` | Matrix operations for PID and undistortion |
| `pillow` | Rendering CJK text on frames (OpenCV cannot draw Unicode) |
| `dashscope` | Alibaba Cloud SDK for the Qwen-VL API |
| `python-dotenv` | Loads `DASHSCOPE_API_KEY` from `.env` without hardcoding it |

**CJK font** (Linux only — required for on-screen Chinese labels):

```bash
# Ubuntu / Debian
sudo apt install fonts-noto-cjk
# or a lighter alternative
sudo apt install fonts-wqy-microhei
```

On Windows the system font `C:\Windows\Fonts\simhei.ttf` is used automatically.

**Arduino IDE** (for firmware):
- Board: `ESP32 Dev Module`
- Libraries: `HX711` by Bogdan Necula (install via Library Manager)

---

## Project Structure

```
submit/
├── PHOTO.jpg
├── README.md
└── src/
    ├── .env.example             # API key template — copy to .env, never commit .env
    ├── .gitignore               # Excludes .env, __pycache__, saved PNGs
    ├── firmware/
    │   └── delta_robot.ino      # ESP32 firmware (Arduino + FreeRTOS, 6 tasks)
    └── controller/
        ├── __init__.py          # Package marker
        ├── config.py            # All tunable constants (PID gains, positions, offsets)
        ├── robot.py             # TCP client with auto-reconnect
        ├── camera.py            # Background capture thread + lens undistortion
        ├── pid.py               # Stateful 2-axis PID controller
        ├── vision.py            # ArUco detection, Qwen-VL inference, frame annotation
        ├── servo.py             # Visual servo loop + descend-and-pick sequence
        ├── main.py              # Argument parsing, main loop, scan-and-servo orchestration
        ├── dot25.npz            # Pre-calibrated camera intrinsics (mtx + dist)
        └── inventory.json       # Optional: list of product names to constrain VLM output
```

---

## Quick Start

### 1. Flash the firmware

Open `firmware/delta_robot.ino` in Arduino IDE, select **ESP32 Dev Module**, and flash.

WiFi credentials are **never stored in source code**. After the first boot, open the serial monitor at 115200 baud and run:

```
setwifi YourNetworkSSID YourPassword
```

The credentials are written to the ESP32's NVS (non-volatile storage) flash partition and persist across reboots. This command is intentionally only accepted over the physical serial port, not over TCP, to prevent remote credential changes.

Once connected, the ESP32 prints its IP address:

```
[WiFi] IP: 192.168.1.xxx  TCP:8266
```

Note this address for the next step.

### 2. Configure the API key

Copy the template and fill in your [DashScope API key](https://dashscope.aliyun.com/):

```bash
cd src
cp .env.example .env
```

Edit `.env`:

```
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
```

This file is listed in `.gitignore` and will never be committed accidentally.

### 3. Set the robot IP

Open `controller/config.py` and update:

```python
ROBOT_IP = "192.168.1.xxx"   # IP printed by the ESP32 on boot
```

### 4. Set up the inventory (optional)

Edit `controller/inventory.json` to list every product that may appear in your scene:

```json
["Lays", "Oreo", "Want Want Rice Crackers"]
```

When this file is present, the VLM prompt instructs the model to match detections to the closest name in the list, preventing hallucinated or inconsistent product names. If the file is absent, the model identifies snacks freely.

### 5. Run

```bash
cd src
python -m controller.main
```

**Available arguments:**

| Argument | Default | Description |
|----------|---------|-------------|
| `--camera N` | `0` | OS camera index (try `1` or `2` if the wrong camera opens) |
| `--intrinsics path` | `dot25.npz` | Path to the `.npz` intrinsics file |
| `--ip x.x.x.x` | `192.168.1.100` | Robot IP address |
| `--port N` | `8266` | Robot TCP port |
| `--no-robot` | off | Disable TCP connection; run vision pipeline only (useful for testing VLM without hardware) |
| `--save` | off | Write each annotated detection frame to a timestamped PNG in the `controller/` directory |

---

## WeChat Mini-Program Integration

The mini-program (`wxapp/`) lets customers scan a QR code, watch their cart fill up in real time as items are weighed, and pay — all from their phone. The machine and the mini-program communicate exclusively through WeChat Cloud (no direct network connection between them is needed).

### Architecture

```
Physical machine (Python)
        │
        │  HTTP POST  (machineWeigh cloud function, URL化)
        ▼
  WeChat Cloud DB  ──  carts collection
        ▲
        │  wx.cloud.callFunction  (getCartList, every 3 s)
        │
  Customer's phone (mini-program)
```

### Step 1 — Set up WeChat Cloud

1. Create a WeChat Mini-Program account at [mp.weixin.qq.com](https://mp.weixin.qq.com) if you don't have one.
2. Open the project in WeChat DevTools, go to **Cloud Development** and create a cloud environment. Note the **environment ID** (looks like `prod-xxxxxx`).
3. Open `wxapp/miniprogram/app.ts` and replace the placeholder:

```ts
wx.cloud.init({ env: 'YOUR_ENV_ID', traceUser: true })
```

4. Deploy all four cloud functions by right-clicking each folder under `wxapp/cloudfunctions/` in DevTools and selecting **Upload and Deploy**.

### Step 2 — Enable HTTP trigger on machineWeigh

1. In the WeChat Cloud Console, go to **Cloud Functions → machineWeigh → HTTP 触发**.
2. Enable URL化 and copy the generated HTTPS URL (e.g. `https://<env-id>.service.tcloudbase.com/machineWeigh`).
3. Paste it into `controller/config.py`:

```python
MACHINE_WEIGH_URL = "https://<env-id>.service.tcloudbase.com/machineWeigh"
```

### Step 3 — Populate the products database

Every product name that the VLM may return must exist in the `products` collection, otherwise `machineWeigh` returns `PRODUCT_NOT_FOUND` and the push is rejected.

In WeChat DevTools → Cloud Development → Database → `products`, add one document per product:

```json
{
  "name":      "乐事薯片",
  "unitPrice": 15.80,
  "image":     "cloud://your-env.xxxx/images/lays.jpg"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Must exactly match the name in `inventory.json` |
| `unitPrice` | number | Price per 500 g in yuan (e.g. `15.80`) |
| `image` | string | WeChat cloud storage file ID or any HTTPS image URL |

The `totalPrice` per item is computed as `weight × unitPrice / 500`.

### Step 4 — Set the cart ID

Each physical machine is identified by a cart ID. The customer scans a QR code that encodes this ID. Set it in `controller/config.py`:

```python
CART_ID = "CART001"   # 3-20 alphanumeric chars, underscores, or hyphens
```

Generate a QR code for this string (any online QR generator works) and attach it to the machine. The mini-program's scan button reads the QR code and calls `validateCart` with this value.

### Step 5 — Verify end-to-end

1. Start the Python controller: `python -m controller.main`
2. Open the mini-program in WeChat DevTools (or on a real device).
3. Tap **扫码绑定购物车** and scan the QR code for `CART001`.
4. Place a snack on the scene. Press **`s`** on the controller.
5. After the robot picks and weighs the item, the mini-program should show the item within 3 seconds.
6. Tap **立即支付** to complete the order (currently simulated; replace `setTimeout` in `index.ts → onPayTap` with `wx.requestPayment` for production).

### Data flow summary

| Step | Actor | Action |
|------|-------|--------|
| 1 | Machine (Python) | VLM detects snack name, robot picks and weighs |
| 2 | `cloud.py` | HTTP POST `{ cartId, items: [{ name, weight }] }` to `machineWeigh` |
| 3 | `machineWeigh` | Looks up `unitPrice` in `products`, writes `snackList` to `carts` |
| 4 | Mini-program | `getCartList` polls every 3 s, renders updated list |
| 5 | Customer | Reviews items, taps pay |
| 6 | `completeOrder` | Writes to `orders`, deletes from `carts` |

---

## Runtime Controls

Two OpenCV windows are shown:

- **"Snack Detection — Delta Robot"** — live camera feed with ArUco overlay and status bar
- **"Detection Result"** — snapshot with VLM bounding boxes, shown after each scan

| Key | Action |
|-----|--------|
| `s` | Trigger the first VLM scan and start the automatic pick loop |
| `q` | Quit cleanly (releases camera, closes TCP connection) |

The status bar at the top of the live window shows:
- Current mode (waiting / scanning / retrying)
- ArUco marker pixel coordinates, or "not detected" if the marker is out of frame

If no snacks are found in a scan, the system waits 10 seconds before retrying automatically.

---

## Pipeline

```
┌─────────────────────────────────────────────────────────┐
│  Live camera preview (畸变校正 applied every frame)       │
│  ArUco marker tracked continuously                       │
└───────────────────────┬─────────────────────────────────┘
                        │ [s] pressed
                        ▼
              Capture & undistort frame
                        │
                        ▼
           Check ArUco visible? ──No──→ warn, retry in 10s
                        │ Yes
                        ▼
         Save frame to temp .jpg
                        │
                        ▼
      Qwen-VL API call (inventory-constrained)
      Returns: [[id, name, [x1,y1,x2,y2]], ...]
                        │
              No items? ─Yes─→ warn, retry in 10s
                        │
                        ▼
         Draw bounding boxes + centres
                        │
          ┌─────────────┘
          │  For each detected item (in order):
          ▼
   Move to workspace centre (0, 0, 200 mm)
          │
          ▼
   ┌── PID visual servo loop ──────────────────────────┐
   │  1. Wait SERVO_SETTLE_S for robot to stabilise    │
   │  2. Capture frame, detect ArUco                   │
   │  3. err = target_px − aruco_px                    │
   │  4. |err| < SERVO_TOL_PX? → converged, exit loop  │
   │  5. PID step → (dx_mm, dy_mm)                     │
   │  6. Clip to workspace, send move_to command        │
   │  7. Repeat up to SERVO_MAX_ITER times              │
   └───────────────────────────────────────────────────┘
          │ converged
          ▼
   Descend in DESCEND_STEP_MM increments
   until pump button triggers or Z_MAX reached
          │
          ▼
   Nudge up 15 mm (compensate over-descent)
          │
          ▼
   Lift to safe height (Z = 200 mm)
          │
          ▼
   Move to weighing station (195, 0, 150 mm)
          │
          ▼
   start_weigh() → sleep 200 ms → pump_off()
   read_weight() waits for [WEIGHT] response
          │
          ▼
   Log weight, continue to next item
          │
          ▼ (all items done)
   Re-scan immediately
```

---

## Firmware TCP Protocol

Connect to `<ROBOT_IP>:8266`. All messages are plain UTF-8 text terminated with `\n`.

On connection the firmware sends:
```
[HELLO] Delta Robot ready.
```

**Commands and responses:**

| Command | Response | Notes |
|---------|----------|-------|
| `x.xx,y.yy,z.zz` | `[OK] x,y,z` or `[ERR] ...` | Move end-effector to (x, y, z) mm. `[OK]` means the motion has been *queued*, not completed. The host must wait `SERVO_SETTLE_S` seconds for the robot to physically arrive. |
| `pos` | `[POS] x,y,z` then `[ANG] a0,a1,a2` | Query current position (mm) and servo angles (degrees). |
| `home` | `[OK]` | Move to the firmware home position (−96, 100, 264 mm). |
| `pump_on` | `[PUMP] ON` | Energise the relay to start the vacuum pump. |
| `pump_off` | `[PUMP] OFF` | De-energise the relay to stop the pump. |
| `pump` | `[PUMP] ON` or `[PUMP] OFF` | Query current pump state without changing it. |
| `weight` | `[WEIGHT] x.xx g` | Trigger a differential weighing cycle. The firmware samples the load cell immediately (before = tare reference), waits `WEIGH_SETTLE_MS` (1000 ms), then samples again (after). The response is `after − before` in grams. |
| `tare` | `[TARE] Done.` | Zero the load cell. Run this once at startup with nothing on the scale. |
| `ping` | `[PONG]` | Keepalive. The firmware disconnects any client that is silent for 30 seconds; the Python side sends a ping every 10 seconds. |
| `setwifi <ssid> <pass>` | `[OK]` | Write WiFi credentials to NVS flash. **Serial port only** — not accepted over TCP. Reboot to apply. |

**Asynchronous push messages** (sent by firmware without a request):

| Message | Meaning |
|---------|---------|
| `[PUMP] ON (button)` | The physical pump button was pressed and the pump toggled on. |
| `[PUMP] OFF (button)` | The physical pump button was pressed and the pump toggled off. |

The Python `Robot` class captures `[PUMP] ON` messages that arrive during `move_to` calls and exposes them via `check_and_clear_pump_trigger()`, which is polled during the descent loop to detect contact.

---

## PID Tuning

All servo parameters live in `controller/config.py` so they can be adjusted without touching logic code.

```python
SERVO_KP          = 0.30   # Proportional gain (mm per pixel of error)
SERVO_KI          = 0.0    # Integral gain — leave at 0 until Kp/Kd are stable
SERVO_KD          = 0.1    # Derivative gain — damps oscillation
SERVO_TOL_PX      = 15     # Convergence threshold in pixels
SERVO_MAX_STEP_MM = 50.0   # Hard clamp on single-step displacement (mm)
SERVO_MAX_ITER    = 25     # Give up after this many iterations
SERVO_SETTLE_S    = 0.8    # Seconds to wait after each move_to before reading ArUco
```

**Tuning procedure:**

1. Start with `SERVO_KI = 0`, `SERVO_KD = 0`. Increase `SERVO_KP` until the robot converges without oscillating.
2. If the robot overshoots and oscillates, reduce `SERVO_KP` and add a small `SERVO_KD` (e.g. 0.05–0.15).
3. Only add `SERVO_KI` if there is a persistent steady-state offset that `SERVO_KP` alone cannot eliminate. Keep it small (< 0.01) and watch for integral windup.
4. If the robot is slow to converge, reduce `SERVO_SETTLE_S` cautiously — too short and the camera captures a blurred frame mid-motion.

**Axis mapping** (depends on physical camera orientation):

```
Image +u (rightward) → Robot −Y
Image +v (downward)  → Robot −X
```

If your camera is mounted at a different rotation, adjust the sign or swap the axes of `dx_mm` / `dy_mm` in `servo.py:servo_to_target`.

---

## Camera Offset Calibration

The camera is not perfectly centred above the robot origin, so there is a fixed pixel offset between where the ArUco marker appears and where it actually is relative to the robot frame. This is compensated by two constants in `config.py`:

```python
CAMERA_OFFSET_U = -70   # Horizontal correction (pixels)
CAMERA_OFFSET_V = -40   # Vertical correction (pixels)
```

**To recalibrate after moving the camera:**

1. Run with `--no-robot` and observe the ArUco centre coordinates printed in the terminal.
2. Command the robot to (0, 0, 200) and note the ArUco pixel position `(ax, ay)`.
3. The image centre is approximately `(960, 540)` for a 1920×1080 frame.
4. Set `CAMERA_OFFSET_U = image_centre_x − ax` and `CAMERA_OFFSET_V = image_centre_y − ay`.

---

## Camera Intrinsics Calibration

The file `dot25.npz` contains the camera's intrinsic matrix `mtx` (3×3) and distortion coefficients `dist` (1×5), used to correct lens barrel/pincushion distortion before processing.

To recalibrate with a new camera or lens:

1. Print a checkerboard pattern (e.g. 9×6 inner corners).
2. Capture 20–30 images of the board at different angles using the same camera and resolution.
3. Run OpenCV calibration:

```python
import cv2, numpy as np, glob

images = glob.glob("calib/*.jpg")
objp = np.zeros((6*9, 3), np.float32)
objp[:, :2] = np.mgrid[0:9, 0:6].T.reshape(-1, 2)

obj_pts, img_pts = [], []
for fname in images:
    gray = cv2.cvtColor(cv2.imread(fname), cv2.COLOR_BGR2GRAY)
    ret, corners = cv2.findChessboardCorners(gray, (9, 6))
    if ret:
        obj_pts.append(objp)
        img_pts.append(corners)

_, K, dist, _, _ = cv2.calibrateCamera(obj_pts, img_pts, gray.shape[::-1], None, None)
np.savez("dot25.npz", mtx=K, dist=dist)
```

Replace `dot25.npz` with the new file. If the file is missing at runtime, undistortion is silently skipped and the system continues with a distorted image (accuracy will be reduced).

---

## Inventory File

`controller/inventory.json` is a plain JSON array of product name strings:

```json
["Lays Original", "Oreo", "Want Want Rice Crackers", "Pocky Chocolate"]
```

**How it works:** when the file is present, the VLM prompt is augmented with the list and the model is instructed to match each detected item to the closest name in the list. This prevents the model from returning inconsistent spellings, brand variants, or hallucinated names across different scans — which matters when the name is used downstream (e.g. for logging or inventory deduction).

**To update the inventory**, simply edit the JSON file and restart the program. No code changes are needed.

---

## Security Notes

| Risk | Mitigation |
|------|-----------|
| WiFi password in firmware source | Credentials stored in ESP32 NVS via `Preferences`; set once with `setwifi` over serial, never appear in code |
| API key in Python source | Loaded from `.env` via `python-dotenv`; `.env` is git-ignored via `.gitignore` |
| Accidental `.env` commit | `.gitignore` excludes `.env`; `.env.example` (no real key) is committed as a template |
