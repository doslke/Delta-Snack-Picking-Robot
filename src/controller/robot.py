import socket
import threading
import time


class Robot:
    """
    Lightweight TCP client wrapping the firmware protocol.
    Send format : "x.xx,y.yy,z.zz\n"
    Firmware response : "[OK] x,y,z\n"  /  "[ERR] ...\n"  /  "[POS] x,y,z\n"
    Note: firmware moveTo is non-blocking; [OK] only means the command was accepted,
          not that motion is complete — the host must wait for the settle time.

    Auto-reconnect: if the TCP connection drops, the next command will attempt
    to reconnect once before failing.
    """
    def __init__(self, host: str, port: int, timeout: float = 8.0):
        self._host    = host
        self._port    = port
        self._timeout = timeout
        self._sock    = None
        self._buf     = ""
        self._lock    = threading.Lock()
        self._pump_triggered = False
        self._connect()

    def _connect(self):
        """Open (or reopen) the TCP socket and consume the [HELLO] banner."""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self._timeout)
        self._sock.connect((self._host, self._port))
        self._sock.settimeout(0.5)
        self._buf = ""
        self._drain(1.5)
        print(f"[Robot] Connected to {self._host}:{self._port}")

    def _ensure_connected(self):
        """Attempt one reconnect if the socket appears dead."""
        try:
            self._sock.getpeername()
        except OSError:
            print("[Robot] Connection lost, reconnecting...")
            try:
                self._connect()
            except OSError as e:
                print(f"[Robot] Reconnect failed: {e}")
                raise

    def _drain(self, secs: float):
        deadline = time.time() + secs
        while time.time() < deadline:
            try:
                self._buf += self._sock.recv(256).decode(errors="replace")
            except socket.timeout:
                break

    def _readline(self, timeout: float = 5.0) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                return line.strip()
            try:
                chunk = self._sock.recv(256).decode(errors="replace")
                self._buf += chunk
            except socket.timeout:
                pass
        return ""

    def _send(self, cmd: str):
        self._ensure_connected()
        self._sock.sendall(cmd.encode())

    def move_to(self, x: float, y: float, z: float) -> bool:
        with self._lock:
            cmd = f"{x:.2f},{y:.2f},{z:.2f}\n"
            self._send(cmd)
            print(f"[Robot] >> {cmd.strip()}")
            deadline = time.time() + 8
            while time.time() < deadline:
                line = self._readline(1.0)
                if not line:
                    continue
                print(f"[Robot] << {line}")
                if "[PUMP] ON" in line:
                    self._pump_triggered = True
                    print("[Robot] Pump button triggered (captured during move_to)")
                if line.startswith("[OK]"):
                    return True
                if line.startswith("[ERR]"):
                    return False
            print("[Robot] move_to timed out")
            return False

    def get_pos(self) -> tuple[float, float, float] | None:
        with self._lock:
            self._send("pos\n")
            deadline = time.time() + 3
            while time.time() < deadline:
                line = self._readline(1.0)
                if line.startswith("[POS]"):
                    try:
                        parts = line[5:].strip().split(",")
                        return float(parts[0]), float(parts[1]), float(parts[2])
                    except Exception:
                        return None
            return None

    def ping(self):
        try:
            with self._lock:
                self._send("ping\n")
                self._drain(0.1)
        except OSError:
            pass

    def home(self):
        with self._lock:
            self._send("home\n")
            deadline = time.time() + 10
            while time.time() < deadline:
                line = self._readline(1.0)
                if line.startswith("[OK]"):
                    print("[Robot] Homed")
                    return
                if line.startswith("[ERR]"):
                    print(f"[Robot] Home failed: {line}")
                    return

    def pump_on(self):
        with self._lock:
            self._send("pump_on\n")
            self._drain(0.5)

    def pump_off(self):
        with self._lock:
            self._send("pump_off\n")
            self._drain(0.5)

    def check_and_clear_pump_trigger(self) -> bool:
        with self._lock:
            try:
                chunk = self._sock.recv(256).decode(errors="replace")
                self._buf += chunk
            except socket.timeout:
                pass
            if "[PUMP] ON" in self._buf:
                self._pump_triggered = True
        triggered = self._pump_triggered
        self._pump_triggered = False
        return triggered

    def start_weigh(self):
        """Send the weight command (non-blocking, does not wait for a response).
        The firmware immediately samples 'before', samples 'after' 1 s later, then pushes a [WEIGHT] response.
        The caller should execute pump_off during this interval, then call read_weight() to retrieve the result.
        """
        with self._lock:
            self._send("weight\n")
            print("[Robot] >> weight")

    def read_weight(self, timeout: float = 8.0) -> "float | None":
        """Wait for and parse a [WEIGHT] X.XX g response; returns grams or None (timeout/failure)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                line = self._readline(0.3)
            if not line:
                continue
            print(f"[Robot] << {line}")
            if line.startswith("[WEIGHT]"):
                try:
                    return float(line.split()[1])
                except (IndexError, ValueError):
                    return None
            if line.startswith("[ERR]"):
                print(f"[Robot] Weighing error: {line}")
                return None
        print("[Robot] Weighing timed out")
        return None

    def tare(self):
        """Zero the load-cell sensor."""
        with self._lock:
            self._send("tare\n")
            deadline = time.time() + 3.0
            while time.time() < deadline:
                line = self._readline(1.0)
                if "[TARE]" in line:
                    print("[Robot] Tare complete")
                    return
        print("[Robot] Tare timed out")

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass
