import os

import cv2
import dashscope
from dotenv import load_dotenv

load_dotenv()

# ── API ───────────────────────────────────────────────────────────────────────
dashscope.api_key = os.environ["DASHSCOPE_API_KEY"]
MODEL_NAME = "qwen3-vl-plus"

INTRINSICS_FILE = "dot25.npz"
INVENTORY_FILE  = "inventory.json"

# ── WeChat Mini-Program cloud integration ─────────────────────────────────────
# HTTP trigger URL for the machineWeigh cloud function.
# Steps to obtain:
#   1. Open WeChat Cloud Console → Cloud Functions → machineWeigh
#   2. Enable "HTTP 触发" (URL化), copy the generated URL
#   3. Paste it here (keep the trailing path, e.g. .../machineWeigh)
# Leave empty to disable cloud push (items will still be picked and weighed locally).
MACHINE_WEIGH_URL = ""

# Cart ID that identifies this physical machine's shopping cart.
# Must match the QR code the customer scans in the mini-program.
# Format: 3-20 alphanumeric characters, underscores, or hyphens (e.g. "CART001").
CART_ID = "CART001"

# ── Robot connection ──────────────────────────────────────────────────────────
ROBOT_IP   = "192.168.1.100"
ROBOT_PORT = 8266

# TCP port for receiving inventory updates from the configer tool
INVENTORY_SERVER_PORT = 8888

# Workspace (must match firmware)
WS = dict(x_min=-100, x_max=100, y_min=-100, y_max=100, z_min=50, z_max=280)

# ── PID servo parameters ──────────────────────────────────────────────────────
SERVO_Z_MM        = 200.0
SERVO_KP          = 0.30
SERVO_KI          = 0.0
SERVO_KD          = 0.1
SERVO_TOL_PX      = 15
SERVO_MAX_STEP_MM = 50.0
SERVO_MAX_ITER    = 25
SERVO_SETTLE_S    = 0.8
SERVO_FRAME_SKIP  = 3

# Pixel offset to compensate for camera mounting misalignment.
# Tune these whenever the camera is repositioned.
# CAMERA_OFFSET_U: horizontal shift (px), positive = target is to the right of true centre
# CAMERA_OFFSET_V: vertical shift (px),   positive = target is below true centre
CAMERA_OFFSET_U = -70
CAMERA_OFFSET_V = -40

# ── Descent & pick parameters ─────────────────────────────────────────────────
DESCEND_STEP_MM  = 5.0
DESCEND_SETTLE_S = 0.2
DESCEND_Z_MAX    = 270.0
PICK_MAX_RETRIES = 2  # max attempts per item if scale returns no reading

# ── Weighing station position ─────────────────────────────────────────────────
WEIGH_X_MM = 195.0
WEIGH_Y_MM = 0.0
WEIGH_Z_MM = 150.0

# ── Home / centre position ────────────────────────────────────────────────────
CENTER_X_MM = 0.0
CENTER_Y_MM = 0.0
CENTER_Z_MM = 200.0

# ── ArUco configuration ───────────────────────────────────────────────────────
ARUCO_DICT_ID   = cv2.aruco.DICT_4X4_50
ARUCO_TARGET_ID = 4
ARUCO_SIDE_MM   = 20.0
ARUCO_COLOR     = (0, 255, 80)

# ── Display configuration ─────────────────────────────────────────────────────
BOX_COLORS = [
    (0, 255, 0),
    (0, 128, 255),
    (255, 0, 128),
    (0, 255, 255),
    (255, 128, 0),
    (128, 255, 0),
]
CENTER_COLOR  = (0, 0, 255)
CENTER_RADIUS = 8
BOX_THICKNESS = 2
FONT_SIZE     = 22
