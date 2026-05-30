import json
import urllib.request
import urllib.error

from .config import CART_ID, MACHINE_WEIGH_URL


def push_weigh_result(name: str, weight_g: float) -> bool:
    """
    POST a single weighed item to the WeChat cloud machineWeigh HTTP trigger.

    The cloud function looks up the product's unit price in the 'products'
    collection, computes the line total, and upserts the cart document so the
    mini-program's polling loop picks it up within 3 seconds.

    Returns True on success, False on any network or server-side error.
    """
    if not MACHINE_WEIGH_URL:
        print("[Cloud] MACHINE_WEIGH_URL is not configured, skipping push")
        return False

    if weight_g <= 0:
        print(f"[Cloud] Skipping {name!r}: weight {weight_g:.2f}g is not positive")
        return False

    payload = json.dumps({
        "cartId": CART_ID,
        "items": [{"name": name, "weight": round(weight_g, 1)}],
    }).encode("utf-8")

    req = urllib.request.Request(
        MACHINE_WEIGH_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"[Cloud] HTTP {e.code}: {e.reason}")
        return False
    except urllib.error.URLError as e:
        print(f"[Cloud] Network error: {e.reason}")
        return False
    except Exception as e:
        print(f"[Cloud] Unexpected error: {e}")
        return False

    if body.get("ok"):
        print(f"[Cloud] Pushed: {name!r}  {weight_g:.1f}g → cart {CART_ID}")
        return True

    code = body.get("code", "UNKNOWN")
    msg  = body.get("message", "")
    print(f"[Cloud] Server rejected: [{code}] {msg}")
    return False
