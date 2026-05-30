import ast
import json
import os
import re
import tempfile

import cv2
import numpy as np
from dashscope import MultiModalConversation
from PIL import Image, ImageDraw, ImageFont

from .config import (
    ARUCO_DICT_ID, ARUCO_TARGET_ID, ARUCO_COLOR,
    BOX_COLORS, BOX_THICKNESS, CENTER_COLOR, CENTER_RADIUS,
    FONT_SIZE, MODEL_NAME,
)


def put_text_cn(img, text, pos, font_size=24, color=(0, 255, 0)):
    """Render text on a BGR image using PIL (works around OpenCV's CJK rendering issues)."""
    img_pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(img_pil)
    font = None
    for path in (
        "simhei.ttf",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\msyh.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ):
        try:
            font = ImageFont.truetype(path, font_size)
            break
        except Exception:
            pass
    if font is None:
        font = ImageFont.load_default()
    draw.text(pos, text, font=font, fill=(color[2], color[1], color[0]))
    return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)


def make_aruco_detector():
    """Build an ArUco detector (compatible with OpenCV 4.x / 4.7+)."""
    aruco_dict = cv2.aruco.getPredefinedDictionary(ARUCO_DICT_ID)
    try:
        params = cv2.aruco.DetectorParameters()
        detector = cv2.aruco.ArucoDetector(aruco_dict, params)
        def _detect(gray):
            corners, ids, _ = detector.detectMarkers(gray)
            return corners, ids
    except AttributeError:
        params = cv2.aruco.DetectorParameters_create()
        def _detect(gray):
            corners, ids, _ = cv2.aruco.detectMarkers(gray, aruco_dict, parameters=params)
            return corners, ids
    return _detect


def draw_aruco(frame, detect_fn):
    """
    Detect ArUco ID=ARUCO_TARGET_ID and draw corner box + centre point.
    Returns (vis, center) where center is (cx, cy) or None.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    corners, ids = detect_fn(gray)

    if ids is None:
        return frame, None

    vis = frame.copy()
    target_center = None

    for i, marker_id in enumerate(ids.ravel()):
        if marker_id != ARUCO_TARGET_ID:
            continue

        pts = corners[i][0].astype(int)
        cx = int(pts[:, 0].mean())
        cy = int(pts[:, 1].mean())
        target_center = (cx, cy)

        cv2.polylines(vis, [pts], isClosed=True, color=ARUCO_COLOR, thickness=2)
        for pt in pts:
            cv2.circle(vis, tuple(pt), 4, ARUCO_COLOR, -1)

        cv2.circle(vis, (cx, cy), CENTER_RADIUS + 2, (255, 255, 255), -1)
        cv2.circle(vis, (cx, cy), CENTER_RADIUS,     ARUCO_COLOR,    -1)

        cross_len = 20
        cv2.line(vis, (cx - cross_len, cy), (cx + cross_len, cy), ARUCO_COLOR, 1)
        cv2.line(vis, (cx, cy - cross_len), (cx, cy + cross_len), ARUCO_COLOR, 1)

        label = f"ArUco#{marker_id}  ({cx}, {cy})"
        vis = put_text_cn(vis, label, (pts[:, 0].min(), max(pts[:, 1].min() - 30, 4)),
                          font_size=FONT_SIZE, color=ARUCO_COLOR)

    return vis, target_center


def query_vl(image_path: str, inventory: "list[str] | None" = None) -> list:
    """
    Call Qwen-VL to identify all snacks in the image.
    inventory: list of product names in stock; when provided the model only identifies items from this list.
    Returns a list where each entry is (obj_id, name, bbox), bbox=[x1,y1,x2,y2] (normalised 0~1000).
    """
    if inventory:
        inventory_hint = (
            f"图中可能出现的商品仅限以下库存列表：{json.dumps(inventory, ensure_ascii=False)}。"
            "识别时请将商品名与列表中最接近的名称对应，name 必须从列表中选取，不得使用列表外的名称。"
        )
    else:
        inventory_hint = ""

    prompt = (
        "请识别图中所有零食。"
        f"{inventory_hint}"
        "对每个零食，给出商品名作为 name。"
        "返回 JSON 列表，格式：[[id, name, [x1, y1, x2, y2]], ...]。"
        "x1/y1/x2/y2 为归一化坐标（0~1000），x1=左，y1=上，x2=右，y2=下。"
        "如果没有零食，返回 []。只返回 JSON，不要其他文字。"
        "务必注意JSON返回的格式"
    )
    messages = [{"role": "user", "content": [
        {"image": f"file://{image_path}"},
        {"text": prompt},
    ]}]

    try:
        resp = MultiModalConversation.call(model=MODEL_NAME, messages=messages)
    except Exception as e:
        print(f"[VL] API error: {e}")
        return []

    if resp.status_code != 200:
        print(f"[VL] Call failed: {resp.code} - {resp.message}")
        return []

    raw = resp.output.choices[0].message.content[0]["text"]
    print(f"[VL] Raw response: {raw}")

    clean = raw.replace("```json", "").replace("```", "").strip()
    m = re.search(r"\[[\s\S]*\]", clean)
    if not m:
        print("[VL] No JSON list found")
        return []

    try:
        items = json.loads(m.group(0))
    except Exception:
        try:
            items = ast.literal_eval(m.group(0))
        except Exception:
            print("[VL] JSON parse failed")
            return []

    results = []
    for idx, item in enumerate(items):
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue

        nested  = [e for e in item if isinstance(e, (list, tuple))]
        strings = [e for e in item if isinstance(e, str)]
        numbers = [e for e in item if isinstance(e, (int, float)) and not isinstance(e, bool)]

        if nested:
            bbox = nested[-1]
            name = strings[0] if strings else f"obj{idx+1}"
        elif len(numbers) >= 4:
            bbox = [numbers[0], numbers[1], numbers[2], numbers[3]]
            name = strings[0] if strings else f"obj{idx+1}"
        else:
            print(f"[VL] Cannot parse item {idx+1}: {item}")
            continue

        if len(bbox) != 4:
            continue

        results.append((idx + 1, str(name), bbox))
        print(f"[VL] Parsed: #{idx+1} {name}  bbox={bbox}")

    return results


def draw_detections(image: np.ndarray, items: list) -> tuple[np.ndarray, list]:
    """
    Draw bounding boxes and centre points for all detected objects.
    Returns (vis, centers), where centers is [(name, cx_px, cy_px), ...].
    """
    vis = image.copy()
    h, w = vis.shape[:2]
    centers = []

    for idx, (obj_id, name, bbox) in enumerate(items):
        color = BOX_COLORS[idx % len(BOX_COLORS)]

        x1 = int(float(bbox[0]) * w / 1000)
        y1 = int(float(bbox[1]) * h / 1000)
        x2 = int(float(bbox[2]) * w / 1000)
        y2 = int(float(bbox[3]) * h / 1000)

        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        centers.append((name, cx, cy))

        cv2.rectangle(vis, (x1, y1), (x2, y2), color, BOX_THICKNESS)

        cv2.circle(vis, (cx, cy), CENTER_RADIUS + 2, (255, 255, 255), -1)
        cv2.circle(vis, (cx, cy), CENTER_RADIUS,     CENTER_COLOR,    -1)

        cross_len = 20
        cv2.line(vis, (cx - cross_len, cy), (cx + cross_len, cy), CENTER_COLOR, 1)
        cv2.line(vis, (cx, cy - cross_len), (cx, cy + cross_len), CENTER_COLOR, 1)

        label = f"#{obj_id} {name}  ({cx}, {cy})"
        vis = put_text_cn(vis, label, (x1, max(y1 - 28, 4)),
                          font_size=FONT_SIZE, color=color)

        print(f"  [{obj_id}] {name}: bbox=({x1},{y1},{x2},{y2})  center=({cx},{cy})")

    return vis, centers
