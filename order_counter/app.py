from flask import Flask, jsonify, request
import json, os, signal, subprocess, pathlib, threading, unicodedata, time
from datetime import datetime
from typing import Optional

BASE_DIR = pathlib.Path(__file__).resolve().parent
ROOT = BASE_DIR.parent
STATIC_DIR = BASE_DIR / "static"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")

PYTHON = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv" / "bin" / "python").exists() else "python3"

# ==== 起動コマンド（必要なら環境変数で上書きOK）====
CAMERA_ID = int(os.environ.get("CAMERA_ID", "0"))
CAMERA_PORT = int(os.environ.get("CAMERA_PORT", "5001"))
MASTER_PORT = int(os.environ.get("MASTER_PORT", "5050"))
PREDICT_PORT = int(os.environ.get("PREDICT_PORT", "5100"))
TAKOYAKI_UNIT_PRICE = int(os.environ.get("TAKOYAKI_UNIT_PRICE", "50"))


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip().lower()


TAKOYAKI_MENU_COUNTS = {
    "four": 4,
    "six": 6,
    "eight": 8,
    "ten": 10,
    "fourteen": 14,
    "takosen": 2,
    "topping": 0,
}
TAKOYAKI_NAME_COUNTS = {
    _normalize_text("4"): 4,
    _normalize_text("6"): 6,
    _normalize_text("8"): 8,
    _normalize_text("10"): 10,
    _normalize_text("14"): 14,
    _normalize_text("four"): 4,
    _normalize_text("six"): 6,
    _normalize_text("eight"): 8,
    _normalize_text("ten"): 10,
    _normalize_text("fourteen"): 14,
    _normalize_text("たこせん"): 2,
    _normalize_text("タコセン"): 2,
    _normalize_text("takosen"): 2,
    _normalize_text("tako sen"): 2,
    _normalize_text("トッピング"): 0,
    _normalize_text("topping"): 0,
}

STREAM_CMD = [PYTHON, str(ROOT / "camera_server.py"), str(CAMERA_ID), str(CAMERA_PORT)]
MASTER_CMD = [PYTHON, str(ROOT / "master_console" / "app.py")]

STREAM_PID = f"/tmp/peopleflow_stream_{CAMERA_ID}_{CAMERA_PORT}.pid"
MASTER_PID = f"/tmp/peopleflow_master_{MASTER_PORT}.pid"
ORDERS_FILE = ROOT / "predictor" / "data" / "orders.jsonl"

_order_lock = threading.Lock()
_orders_loaded = False
_known_order_ids: dict[str, str] = {}


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid(pid_file: str):
    if not os.path.exists(pid_file):
        return None
    try:
        return int(open(pid_file).read().strip())
    except Exception:
        return None


def _service_log_path(name: str | None) -> Optional[pathlib.Path]:
    if not name:
        return None
    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in name)
    return log_dir / f"{safe}.log"


def _tail_log(path: pathlib.Path | None, lines: int = 40) -> list[str]:
    if not path or not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        data = handle.readlines()
    return [line.rstrip("\n") for line in data[-lines:]]


def _start(cmd, pid_file: str, env: dict | None = None, *, log_name: str | None = None):
    pid = _read_pid(pid_file)
    if pid and _is_running(pid):
        return pid, "already running", []
    if pid and not _is_running(pid):
        try:
            os.remove(pid_file)
        except Exception:
            pass

    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    log_path = _service_log_path(log_name)
    log_handle = None
    if log_path:
        log_handle = open(log_path, "a", encoding="utf-8")
        log_handle.write(f"\n[{datetime.now().isoformat()}] ==== start {cmd} ====\n")
        log_handle.flush()
    proc = subprocess.Popen(
        cmd,
        start_new_session=True,
        env=merged_env,
        stdout=log_handle if log_handle else None,
        stderr=subprocess.STDOUT if log_handle else None,
    )
    if log_handle:
        log_handle.flush()
        log_handle.close()

    time.sleep(1.0)
    if proc.poll() is not None:
        if os.path.exists(pid_file):
            try:
                os.remove(pid_file)
            except Exception:
                pass
        log_tail = _tail_log(log_path)
        return None, f"failed (exit {proc.returncode}). log_path={log_path}", log_tail

    with open(pid_file, "w") as f:
        f.write(str(proc.pid))
    return proc.pid, "started", []


def _stop(pid_file: str):
    pid = _read_pid(pid_file)
    if not pid:
        return "not running"

    try:
        os.killpg(pid, signal.SIGTERM)
    except Exception:
        pass

    try:
        os.remove(pid_file)
    except Exception:
        pass

    return "stopped"


def _load_existing_orders():
    global _orders_loaded
    if _orders_loaded:
        return
    if not ORDERS_FILE.exists():
        return
    with ORDERS_FILE.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            order_id = row.get("order_id")
            timestamp = row.get("timestamp")
            if order_id and timestamp:
                _known_order_ids[order_id] = timestamp
    _orders_loaded = True


def _price_to_takoyaki_count(total_price: Optional[float]) -> Optional[int]:
    if total_price is None or total_price <= 0:
        return None
    return max(1, int(round(total_price / TAKOYAKI_UNIT_PRICE)))


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _lookup_item_units(item: dict) -> Optional[int]:
    if not isinstance(item, dict):
        return None
    menu_id = item.get("menuId") or item.get("menu_id")
    if isinstance(menu_id, str):
        menu_key = _normalize_text(menu_id)
        if menu_key in TAKOYAKI_MENU_COUNTS:
            return TAKOYAKI_MENU_COUNTS[menu_key]
    name = item.get("name")
    if isinstance(name, str):
        normalized = _normalize_text(name)
        if normalized in TAKOYAKI_NAME_COUNTS:
            return TAKOYAKI_NAME_COUNTS[normalized]
        digits = "".join(ch for ch in unicodedata.normalize("NFKC", name) if ch.isdigit())
        if digits:
            try:
                return int(digits)
            except ValueError:
                pass
    return None


def _takoyaki_units_from_items(items) -> Optional[int]:
    if not isinstance(items, list) or not items:
        return None
    total = 0
    matched = False
    for item in items:
        units = _lookup_item_units(item)
        if units is None:
            continue
        qty = max(0, _safe_int(item.get("quantity"), default=1))
        total += units * qty
        matched = True
    return total if matched else None


def _fallback_quantity_total(items) -> Optional[int]:
    if not isinstance(items, list) or not items:
        return None
    subtotal = 0
    for item in items:
        subtotal += max(0, _safe_int(item.get("quantity"), default=1))
    return subtotal


def _extract_total_price(payload: dict) -> Optional[float]:
    for key in ("total", "total_price", "amount", "price"):
        raw = payload.get(key)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    items = payload.get("items")
    if isinstance(items, list):
        subtotal = 0.0
        has_value = False
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                price_value = float(item.get("price", 0))
                qty_value = int(item.get("quantity", 1))
            except (TypeError, ValueError):
                continue
            if price_value <= 0 or qty_value <= 0:
                continue
            subtotal += price_value * qty_value
            has_value = True
        if has_value:
            return subtotal
    return None


def _record_order_count(
    order_id: str | None, order_count: int, event_time: datetime, total_price: Optional[float] = None
) -> tuple[str, bool]:
    order_count = max(0, int(order_count))
    event_time = event_time.replace(microsecond=0)
    event_ts = event_time.strftime("%Y-%m-%dT%H:%M:%S")
    with _order_lock:
        _load_existing_orders()
        if order_id:
            existing_ts = _known_order_ids.get(order_id)
            if existing_ts:
                return existing_ts, False
        payload = {
            "timestamp": event_ts,
            "order_occurred": True,
            "order_count": order_count,
        }
        if total_price is not None:
            payload["total_price"] = round(float(total_price), 2)
            payload["unit_price_for_count"] = TAKOYAKI_UNIT_PRICE
        payload["takoyaki_count"] = order_count
        if order_id:
            payload["order_id"] = order_id
        ORDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with ORDERS_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        if order_id:
            _known_order_ids[order_id] = event_ts
    return event_ts, True


@app.get("/")
def index():
    return app.send_static_file("index.html")


# ===== 配信（子機） =====
@app.get("/api/stream/status")
def stream_status():
    pid = _read_pid(STREAM_PID)
    return jsonify({
        "running": bool(pid and _is_running(pid)),
        "pid": pid,
        "camera_id": CAMERA_ID,
        "camera_port": CAMERA_PORT
    })


@app.post("/api/stream/start")
def stream_start():
    pid, msg, tail = _start(
        STREAM_CMD,
        STREAM_PID,
        log_name=f"stream_{CAMERA_ID}_{CAMERA_PORT}",
    )
    if pid is None:
        return jsonify({"ok": False, "message": msg, "log_tail": tail}), 500
    return jsonify({"ok": True, "pid": pid, "message": msg})


@app.post("/api/stream/stop")
def stream_stop():
    msg = _stop(STREAM_PID)
    return jsonify({"ok": True, "message": msg})


# ===== 母艦コンソール =====
@app.get("/api/master/status")
def master_status():
    pid = _read_pid(MASTER_PID)
    return jsonify({
        "running": bool(pid and _is_running(pid)),
        "pid": pid,
        "master_port": MASTER_PORT
    })


@app.post("/api/master/start")
def master_start():
    # “同一ラズパイ内”で子機を見つけに行く想定（必要なら変更OK）
    env = {
        "MASTER_PORT": str(MASTER_PORT),
        "CAMERA_PORTS": os.environ.get("CAMERA_PORTS", str(CAMERA_PORT)),
        "KNOWN_CHILD_IPS": os.environ.get("KNOWN_CHILD_IPS", "127.0.0.1"),
    }
    if "YOLO_MODEL_PATH" not in os.environ:
        local_model = ROOT / "yolov8n.pt"
        if local_model.exists():
            env["YOLO_MODEL_PATH"] = str(local_model)
    pid, msg, tail = _start(
        MASTER_CMD,
        MASTER_PID,
        env=env,
        log_name=f"master_{MASTER_PORT}",
    )
    if pid is None:
        return jsonify({"ok": False, "message": msg, "log_tail": tail}), 500
    return jsonify({"ok": True, "pid": pid, "message": msg})


@app.post("/api/master/stop")
def master_stop():
    msg = _stop(MASTER_PID)
    return jsonify({"ok": True, "message": msg})


@app.get("/api/urls")
def urls():
    host = os.environ.get("PUBLIC_HOST", "")  # 空ならブラウザのhostをJS側で使う
    return jsonify({
        "stream_path": f"/stream",
        "stream_port": CAMERA_PORT,
        "master_port": MASTER_PORT,
        "predict_port": PREDICT_PORT,
        "camera_id": CAMERA_ID
    })


@app.route("/api/orders/log", methods=["POST", "OPTIONS"])
def record_order():
    if request.method == "OPTIONS":
        return ("", 204)
    payload = request.get_json(silent=True) or {}
    timestamp_raw = payload.get("time") or payload.get("timestamp")
    if timestamp_raw:
        try:
            event_time = datetime.fromisoformat(timestamp_raw.replace("Z", "+00:00"))
        except ValueError:
            event_time = datetime.now()
    else:
        event_time = datetime.now()

    items = payload.get("items")
    total_price = _extract_total_price(payload)
    takoyaki_units = _takoyaki_units_from_items(items)
    price_based_count = _price_to_takoyaki_count(total_price)

    reported_count = payload.get("order_count")
    order_count: Optional[int]
    try:
        order_count = int(reported_count)
    except (TypeError, ValueError):
        order_count = None

    if takoyaki_units is not None:
        order_count = takoyaki_units
    elif order_count is not None:
        order_count = max(0, order_count)
    elif price_based_count is not None:
        order_count = price_based_count
    else:
        fallback_qty = _fallback_quantity_total(items)
        order_count = fallback_qty if fallback_qty is not None else 0

    order_id = payload.get("id") or payload.get("order_id")
    event_ts, created = _record_order_count(order_id, order_count, event_time, total_price=total_price)
    return jsonify({
        "ok": True,
        "timestamp": event_ts,
        "order_count": order_count,
        "created": created
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
