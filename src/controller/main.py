import argparse
import json
import os
import socket
import sys
import tempfile
import threading
import time

import cv2

from .camera import CameraStream, build_undistort_maps, load_camera_params, undistort
from .cloud import push_weigh_result
from .config import (
    ARUCO_TARGET_ID,
    CENTER_X_MM, CENTER_Y_MM, CENTER_Z_MM,
    INTRINSICS_FILE, INVENTORY_FILE,
    INVENTORY_SERVER_PORT,
    PICK_MAX_RETRIES,
    ROBOT_IP, ROBOT_PORT,
    SERVO_KD, SERVO_KI, SERVO_KP, SERVO_MAX_STEP_MM, SERVO_Z_MM,
    WEIGH_X_MM, WEIGH_Y_MM, WEIGH_Z_MM,
)
from .pid import PIDController
from .robot import Robot
from .servo import descend_and_pick, servo_to_target
from .vision import draw_aruco, draw_detections, put_text_cn, query_vl


def main():
    parser = argparse.ArgumentParser(description="VLM snack detection + ArUco PID servo")
    parser.add_argument("--camera",     type=int, default=0,         help="camera index")
    parser.add_argument("--intrinsics", default=INTRINSICS_FILE,     help="intrinsics file path")
    parser.add_argument("--ip",         default=ROBOT_IP,            help="robot IP")
    parser.add_argument("--port",       type=int, default=ROBOT_PORT, help="robot port")
    parser.add_argument("--no-robot",   action="store_true",         help="run without connecting to robot")
    parser.add_argument("--save",       action="store_true",         help="save detection result as PNG")
    args = parser.parse_args()

    inventory: list[str] | None = None
    inventory_lock = threading.Lock()

    try:
        with open(INVENTORY_FILE, encoding="utf-8") as f:
            inventory = json.load(f)
        print(f"[INFO] Inventory loaded: {len(inventory)} items: {inventory}")
    except FileNotFoundError:
        print(f"[INFO] Inventory file {INVENTORY_FILE!r} not found, no product filter applied")
    except Exception as e:
        print(f"[WARN] Failed to load inventory file ({e}), no product filter applied")

    def _inventory_server():
        """Listen on INVENTORY_SERVER_PORT for JSON name-list pushes from the configer tool."""
        nonlocal inventory
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", INVENTORY_SERVER_PORT))
        srv.listen(5)
        print(f"[Inventory] Listening on :{INVENTORY_SERVER_PORT}")
        while True:
            try:
                conn, addr = srv.accept()
                conn.settimeout(5.0)
                buf = b""
                try:
                    while True:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                except socket.timeout:
                    pass
                finally:
                    conn.close()

                raw = buf.decode("utf-8", errors="replace").strip()
                if not raw:
                    continue

                data = json.loads(raw)
                # Accept either a plain name list or the full goods list from configer
                if isinstance(data, list) and all(isinstance(i, str) for i in data):
                    names = data
                elif isinstance(data, list) and all(isinstance(i, dict) for i in data):
                    names = [item["name"] for item in data if "name" in item]
                else:
                    print(f"[Inventory] Unrecognised payload from {addr}, ignored")
                    continue

                with inventory_lock:
                    inventory = names
                with open(INVENTORY_FILE, "w", encoding="utf-8") as f:
                    json.dump(names, f, ensure_ascii=False, indent=2)
                print(f"[Inventory] Updated from {addr[0]}: {len(names)} items: {names}")

            except json.JSONDecodeError as e:
                print(f"[Inventory] JSON parse error from {addr}: {e}")
            except Exception as e:
                print(f"[Inventory] Error: {e}")

    threading.Thread(target=_inventory_server, daemon=True).start()

    K, dist, map1, map2 = load_camera_params(args.intrinsics)

    from .vision import make_aruco_detector
    detect_aruco = make_aruco_detector()
    print(f"[INFO] ArUco detector ready  ID={ARUCO_TARGET_ID}  dict=DICT_4X4_50")

    robot = None
    if not args.no_robot:
        try:
            robot = Robot(args.ip, args.port)
        except Exception as e:
            print(f"[WARN] Cannot connect to robot: {e}, running in no-robot mode")

    pid = PIDController(kp=SERVO_KP, ki=SERVO_KI, kd=SERVO_KD,
                        max_output=SERVO_MAX_STEP_MM)

    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_V4L2
    _raw_cap = cv2.VideoCapture(args.camera, backend)
    if not _raw_cap.isOpened():
        raise RuntimeError(f"Cannot open camera {args.camera}")
    _raw_cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1920)
    _raw_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    cap = CameraStream(_raw_cap)

    if robot is not None:
        print(f"[INIT] Moving to weighing station ({WEIGH_X_MM}, {WEIGH_Y_MM}, {WEIGH_Z_MM})")
        robot.move_to(WEIGH_X_MM, WEIGH_Y_MM, WEIGH_Z_MM)
        time.sleep(2.0)

    maps_built  = False
    auto_scan   = False
    next_scan_t = 0.0
    last_ping_t = time.time()
    print("[INFO] Press 's' to start first scan, then auto-loop; press 'q' to quit")

    def do_scan_and_servo() -> bool:
        nonlocal map1, map2, maps_built

        ret2, raw_snap = cap.read()
        if not ret2:
            print("[WARN] Frame capture failed")
            return False
        snap = undistort(raw_snap, map1, map2)

        _, aruco_now = draw_aruco(snap, detect_aruco)
        if aruco_now is None:
            print("[WARN] ArUco not detected in current frame, cannot servo")
            vis = put_text_cn(snap.copy(), "ArUco not detected",
                              (12, 12), font_size=32, color=(0, 0, 255))
            cv2.imshow("Detection result",
                       cv2.resize(vis, (min(snap.shape[1], 1280),
                                       min(snap.shape[0], 720))))
            return False

        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        tmp_path = tmp.name
        tmp.close()
        cv2.imwrite(tmp_path, snap)
        print("[INFO] Calling VLM for detection...")
        with inventory_lock:
            current_inventory = inventory
        items = query_vl(tmp_path, current_inventory)
        os.unlink(tmp_path)

        vis, _ = draw_aruco(snap, detect_aruco)

        if not items:
            print("[INFO] No snacks detected")
            vis = put_text_cn(vis, "No snacks detected",
                              (12, 12), font_size=32, color=(0, 0, 255))
            cv2.imshow("Detection result",
                       cv2.resize(vis, (min(snap.shape[1], 1280),
                                       min(snap.shape[0], 720))))
            return False

        vis, centers = draw_detections(vis, items)
        vis = put_text_cn(vis, f"Detected {len(items)} snack(s), picking in order",
                          (12, 8), font_size=26, color=(0, 255, 128))
        cv2.imshow("Detection result",
                   cv2.resize(vis, (min(snap.shape[1], 1280),
                                   min(snap.shape[0], 720))))
        cv2.waitKey(1)

        if args.save:
            ts = time.strftime("%Y%m%d_%H%M%S")
            save_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                f"detect_{ts}.png")
            cv2.imwrite(save_path, vis)
            print(f"[INFO] Saved: {save_path}")

        for idx, (target_name, target_cx, target_cy) in enumerate(centers):
            print(f"\n[INFO] Picking {idx+1}/{len(centers)}: "
                  f"{target_name}  pixel_center=({target_cx},{target_cy})")

            if robot is not None:
                print(f"[INFO] Moving to centre ({CENTER_X_MM}, {CENTER_Y_MM}, {CENTER_Z_MM})")
                robot.move_to(CENTER_X_MM, CENTER_Y_MM, CENTER_Z_MM)
                time.sleep(1.5)

            for attempt in range(1, PICK_MAX_RETRIES + 1):
                if attempt > 1:
                    print(f"[INFO] Re-pick attempt {attempt}/{PICK_MAX_RETRIES} for {target_name}, "
                          f"moving back to centre")
                    if robot is not None:
                        robot.move_to(CENTER_X_MM, CENTER_Y_MM, CENTER_Z_MM)
                        time.sleep(1.5)

                print(f"\n{'='*50}")
                print(f"[Servo] Starting servo → {target_name}  (attempt {attempt}/{PICK_MAX_RETRIES})")
                print(f"{'='*50}")
                converged = servo_to_target(
                    target_px=(target_cx, target_cy),
                    cap=cap,
                    map1=map1, map2=map2,
                    detect_aruco=detect_aruco,
                    robot=robot,
                    pid=pid,
                )
                if not converged:
                    print(f"[WARN] Servo did not converge, skipping {target_name}")
                    break

                print(f"[INFO] Platform reached above {target_name}  Z={SERVO_Z_MM}mm")
                weight = descend_and_pick(robot)
                if weight:
                    print(f"[INFO] {target_name} weight result: {weight:.2f} g")
                    push_weigh_result(target_name, weight)
                    break
                if attempt < PICK_MAX_RETRIES:
                    print(f"[WARN] No weight reading for {target_name}, retrying pick...")
                else:
                    print(f"[WARN] No weight reading after {PICK_MAX_RETRIES} attempts, "
                          f"skipping {target_name}")

        return True

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                continue

            if K is not None and not maps_built:
                h, w = frame.shape[:2]
                map1, map2 = build_undistort_maps(K, dist, (w, h))
                maps_built = True

            frame = undistort(frame, map1, map2)
            display, aruco_center = draw_aruco(frame, detect_aruco)

            h, w = display.shape[:2]
            if not auto_scan:
                status = "[s]=start detection+servo  [q]=quit"
            elif time.time() < next_scan_t:
                wait = int(next_scan_t - time.time()) + 1
                status = f"[auto] No target found, retrying in {wait}s  [q]=quit"
            else:
                status = "[auto] Scanning...  [q]=quit"
            if aruco_center:
                status += f"  ArUco#{ARUCO_TARGET_ID}=({aruco_center[0]},{aruco_center[1]})"
            else:
                status += f"  ArUco#{ARUCO_TARGET_ID}=not detected"
            display = put_text_cn(display, status, (12, 8),
                                  font_size=24, color=(0, 255, 128))
            cv2.imshow("Snack Detection — Delta Robot",
                       cv2.resize(display, (min(w, 1280), min(h, 720))))

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break

            if key == ord('s') and not auto_scan:
                auto_scan   = True
                next_scan_t = 0.0
                print("[INFO] Auto-scan started")

            if auto_scan and time.time() >= next_scan_t:
                found = do_scan_and_servo()
                if not found:
                    next_scan_t = time.time() + 10.0
                    print("[INFO] No target found, retrying in 10s")
                else:
                    next_scan_t = 0.0

            if robot is not None and time.time() - last_ping_t > 10.0:
                robot.ping()
                last_ping_t = time.time()

    finally:
        cap.release()
        cv2.destroyAllWindows()
        if robot:
            robot.close()


if __name__ == "__main__":
    main()
