import time

import numpy as np

from .camera import CameraStream, undistort
from .config import (
    CAMERA_OFFSET_U, CAMERA_OFFSET_V,
    CENTER_Z_MM,
    DESCEND_SETTLE_S, DESCEND_STEP_MM, DESCEND_Z_MAX,
    SERVO_KD, SERVO_KI, SERVO_KP, SERVO_MAX_ITER, SERVO_MAX_STEP_MM,
    SERVO_SETTLE_S, SERVO_TOL_PX, SERVO_Z_MM,
    WEIGH_X_MM, WEIGH_Y_MM, WEIGH_Z_MM,
    WS,
)
from .pid import PIDController
from .robot import Robot
from .vision import draw_aruco


def servo_to_target(
    target_px:   tuple[int, int],
    cap:         CameraStream,
    map1, map2,
    detect_aruco,
    robot:       "Robot | None",
    pid:         PIDController,
) -> bool:
    """
    PID visual servo main loop.

    Uses the ArUco#ARUCO_TARGET_ID pixel centre as the platform's current position feedback,
    iteratively controls the robot X/Y to align the ArUco centre with the target pixel centre.
    Z axis is fixed at SERVO_Z_MM.

    Returns True = converged, False = exceeded max iterations or ArUco lost.
    """
    pid.reset()
    tx, ty = target_px
    tx += CAMERA_OFFSET_U
    ty += CAMERA_OFFSET_V

    if robot is not None:
        pos = robot.get_pos()
        if pos is None:
            print("[Servo] Cannot get current position, aborting")
            return False
        cur_x, cur_y, _ = pos
        ok = robot.move_to(cur_x, cur_y, SERVO_Z_MM)
        if not ok:
            print("[Servo] Height preset failed, aborting")
            return False
        time.sleep(SERVO_SETTLE_S)
        cur_x_mm, cur_y_mm = cur_x, cur_y
    else:
        cur_x_mm, cur_y_mm = 0.0, 0.0

    print(f"[Servo] Starting servo  target_px=({tx},{ty})  Z={SERVO_Z_MM}mm")
    print(f"        gains Kp={SERVO_KP} Ki={SERVO_KI} Kd={SERVO_KD}  "
          f"tol={SERVO_TOL_PX}px  max_step={SERVO_MAX_STEP_MM}mm")

    for iteration in range(1, SERVO_MAX_ITER + 1):
        time.sleep(SERVO_SETTLE_S)
        ret, frame = cap.read()
        if not ret:
            print("[Servo] Frame capture failed, skipping")
            continue
        frame = undistort(frame, map1, map2)

        _, aruco_center = draw_aruco(frame, detect_aruco)
        if aruco_center is None:
            print(f"[Servo] iter {iteration}: ArUco lost, skipping frame")
            if robot:
                robot.ping()
            continue

        ax, ay = aruco_center
        err_u = tx - ax
        err_v = ty - ay
        err_norm = (err_u ** 2 + err_v ** 2) ** 0.5

        print(f"[Servo] iter={iteration:02d}  "
              f"ArUco=({ax},{ay})  target=({tx},{ty})  "
              f"err=({err_u:+.0f},{err_v:+.0f})px  |err|={err_norm:.1f}px")

        if err_norm < SERVO_TOL_PX:
            print(f"[Servo] ✓ Converged! error {err_norm:.1f}px < {SERVO_TOL_PX}px  "
                  f"iterations={iteration}")
            return True

        err_vec = np.array([err_u, err_v], dtype=float)
        delta   = pid.step(err_vec)

        # Camera mounted rotated: image +u → robot -Y, image +v → robot -X
        dx_mm = -delta[1]
        dy_mm = -delta[0]

        new_x = float(np.clip(cur_x_mm + dx_mm, WS["x_min"], WS["x_max"]))
        new_y = float(np.clip(cur_y_mm + dy_mm, WS["y_min"], WS["y_max"]))

        print(f"         PID Δ=({dx_mm:+.2f},{dy_mm:+.2f})mm  "
              f"→ sending ({new_x:+.2f},{new_y:+.2f},{SERVO_Z_MM:.1f})")

        if robot is not None:
            ok = robot.move_to(new_x, new_y, SERVO_Z_MM)
            if not ok:
                print("[Servo] Command rejected (out of range or IK infeasible), aborting")
                return False
            time.sleep(SERVO_SETTLE_S)
        else:
            print(f"[Servo] (no-robot) simulated move ({new_x:+.2f},{new_y:+.2f},{SERVO_Z_MM:.1f})")
            time.sleep(0.1)

        cur_x_mm, cur_y_mm = new_x, new_y

    print(f"[Servo] ✗ Reached max iterations {SERVO_MAX_ITER}, did not converge")
    return False


def descend_and_pick(robot: "Robot | None") -> "float | None":
    """
    The platform is already directly above the target (Z=SERVO_Z_MM); execute the full pick sequence:
      1. Slowly descend until the pump button triggers
      2. Lift to safe height, move to the weighing station
      3. Trigger weighing (firmware samples before≈0g), turn off pump to release the item
      4. Firmware waits 1s then samples after, pushes [WEIGHT] response

    Returns: item weight (g), None on failure, 0.0 in no-robot mode.
    """
    if robot is None:
        print("[Pick] no-robot mode, skipping pick sequence")
        return 0.0

    pos = robot.get_pos()
    if pos is None:
        print("[Pick] Cannot get current position, aborting")
        return None
    cur_x, cur_y, cur_z = pos
    print(f"\n[Pick] Starting descent  current_pos=({cur_x:.1f},{cur_y:.1f},{cur_z:.1f})")

    robot.check_and_clear_pump_trigger()

    pump_triggered = False
    while cur_z < DESCEND_Z_MAX:
        cur_z = min(cur_z + DESCEND_STEP_MM, DESCEND_Z_MAX)
        ok = robot.move_to(cur_x, cur_y, cur_z)
        if not ok:
            print(f"[Pick] Descent command rejected (Z={cur_z:.1f}mm), stopping")
            return None
        time.sleep(DESCEND_SETTLE_S)

        if robot.check_and_clear_pump_trigger():
            print(f"[Pick] Pump triggered! Stopping descent  Z={cur_z:.1f}mm")
            pump_triggered = True
            time.sleep(1)
            break

    if not pump_triggered:
        print("[Pick] No pump trigger detected, reached max depth, continuing")
        robot.pump_on()
        time.sleep(1)

    # Nudge upward to compensate for over-descent
    robot.move_to(cur_x, cur_y, cur_z - 15)
    time.sleep(2)

    print(f"[Pick] Lifting to safe height Z={CENTER_Z_MM}mm")
    ok = robot.move_to(cur_x, cur_y, CENTER_Z_MM)
    if ok:
        time.sleep(1.2)

    print(f"[Pick] Moving to weighing station ({WEIGH_X_MM}, {WEIGH_Y_MM}, {WEIGH_Z_MM})")
    ok = robot.move_to(WEIGH_X_MM, WEIGH_Y_MM, WEIGH_Z_MM)
    if ok:
        time.sleep(1.5)

    # Trigger weighing: firmware immediately samples 'before' (item still held by pump, scale≈0g),
    # then waits 1s; we turn off the pump to release the item during that window,
    # firmware then samples 'after' = item weight.
    # Sleep 200ms after start_weigh to ensure the firmware has sampled 'before'
    # before pump_off arrives, avoiding a race on high-latency TCP links.
    print("[Pick] Triggering weigh, turning off pump to release item...")
    robot.start_weigh()
    time.sleep(0.2)
    robot.pump_off()

    weight = robot.read_weight()
    if weight is not None:
        print(f"[Pick] Item weight: {weight:.2f} g")
    else:
        print("[Pick] Weighing failed (timeout or sensor error)")

    time.sleep(1.0)
    print("[Pick] Sequence complete, parked at weighing station")
    return weight
