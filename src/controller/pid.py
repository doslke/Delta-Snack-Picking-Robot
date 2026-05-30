import numpy as np

from .config import SERVO_MAX_STEP_MM


class PIDController:
    """
    2D image PID controller (X/Y axes computed independently, shared gains).

    Input:  pixel error (err_x, err_y) = target pixel coords - ArUco current pixel coords
    Output: displacement increment in robot frame (dx_mm, dy_mm)
    """
    def __init__(self, kp: float, ki: float, kd: float,
                 max_output: float = SERVO_MAX_STEP_MM):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.max_out = max_output
        self._integral = np.zeros(2, dtype=float)
        self._last_err  = np.zeros(2, dtype=float)
        self._first     = True

    def reset(self):
        self._integral[:] = 0
        self._last_err[:]  = 0
        self._first = True

    def step(self, err: np.ndarray) -> np.ndarray:
        """err — shape (2,): [err_x_px, err_y_px]; returns [dx_mm, dy_mm] (clamped)"""
        if self._first:
            self._last_err = err.copy()
            self._first = False

        self._integral += err
        self._integral = np.clip(self._integral, -200, 200)

        derivative = err - self._last_err
        self._last_err = err.copy()

        output = (self.kp * err
                  + self.ki * self._integral
                  + self.kd * derivative)

        norm = np.linalg.norm(output)
        if norm > self.max_out:
            output = output * (self.max_out / norm)

        return output
