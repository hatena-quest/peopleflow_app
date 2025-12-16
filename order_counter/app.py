from flask import Flask, jsonify
import os, signal, subprocess, pathlib

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

STREAM_CMD = [PYTHON, str(ROOT / "camera_server.py"), str(CAMERA_ID), str(CAMERA_PORT)]
MASTER_CMD = [PYTHON, str(ROOT / "master_console" / "app.py")]

STREAM_PID = f"/tmp/peopleflow_stream_{CAMERA_ID}_{CAMERA_PORT}.pid"
MASTER_PID = f"/tmp/peopleflow_master_{MASTER_PORT}.pid"


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


def _start(cmd, pid_file: str, env: dict | None = None):
    pid = _read_pid(pid_file)
    if pid and _is_running(pid):
        return pid, "already running"
    if pid and not _is_running(pid):
        try:
            os.remove(pid_file)
        except Exception:
            pass

    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    proc = subprocess.Popen(cmd, start_new_session=True, env=merged_env)
    with open(pid_file, "w") as f:
        f.write(str(proc.pid))
    return proc.pid, "started"


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
    pid, msg = _start(STREAM_CMD, STREAM_PID)
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
    pid, msg = _start(MASTER_CMD, MASTER_PID, env=env)
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
