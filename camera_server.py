"""
子機（Raspberry Pi）用カメラサーバー
本番環境で使用するストリーミングサーバー

【使用方法】
各子機で以下のように起動します：

カメラ1（ポート5001）:
    python camera_server.py 0 5001

カメラ2（ポート5002）:
    python camera_server.py 1 5002

カメラ3（ポート5003）:
    python camera_server.py 2 5003

カメラ4（ポート5004）:
    python camera_server.py 3 5004

または環境変数で設定:
    export CAMERA_ID=0
    export CAMERA_PORT=5001
    python camera_server.py
"""
from flask import Flask, Response, jsonify, request
import cv2
import numpy as np
import threading
import time
import sys
import os
import signal
import socket
import json
import shutil
import subprocess

app = Flask(__name__)

# グローバル変数
camera = None
running = True
latest_frame = None
frame_lock = threading.Lock()
camera_thread = None
camera_control_lock = threading.Lock()

# 露出・画質調整（ハードウェア設定 + ソフトウェア補正）
# - ハードウェア（CAP_PROP_*）はカメラ/ドライバ依存で効かない場合があります
# - ソフトウェア補正は「見た目」を変えるだけで白飛び復元はできません
camera_controls = {
    "auto_exposure": True,
    "exposure": None,      # manual exposure（機種依存の値）
    "software_ev": 0.0,    # -2.0..+2.0（2^EV でスケール）
}
last_control_result = {
    "applied": {},
    "errors": [],
}

def _v4l2_available() -> bool:
    return sys.platform.startswith('linux') and shutil.which('v4l2-ctl') is not None

def _v4l2_device_path() -> str:
    # 通常は /dev/video{CAMERA_DEVICE_ID} だが、環境依存の場合は env で上書き可能
    return os.environ.get('CAMERA_DEVICE_PATH', f"/dev/video{app.config.get('CAMERA_DEVICE_ID', 0)}")

def _v4l2_get_ctrls(ctrl_names: list[str]) -> dict:
    if not _v4l2_available():
        return {"ok": False, "stderr": "v4l2-ctl not available", "values": {}}
    device = _v4l2_device_path()
    if not os.path.exists(device):
        return {"ok": False, "stderr": f"device not found: {device}", "values": {}}
    args = ["v4l2-ctl", "-d", device]
    for name in ctrl_names:
        args.extend(["-C", name])
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=2.0, check=False)
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        values = {}
        for line in out.splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip()
            try:
                values[k] = int(v)
            except Exception:
                values[k] = v
        return {"ok": proc.returncode == 0, "stdout": out, "stderr": err, "values": values, "device": device}
    except Exception as e:
        return {"ok": False, "stderr": str(e), "values": {}, "device": device}

def _v4l2_readback_exposure() -> dict:
    """
    カメラが持つコントロール名は機種依存なので、読めるものを採用して返す
    - auto_exposure / exposure_time_absolute (UVCでよくある)
    - exposure_auto / exposure_absolute (別名パターン)
    """
    candidates = [
        ["auto_exposure", "exposure_time_absolute"],
        ["exposure_auto", "exposure_absolute"],
        ["auto_exposure"],
        ["exposure_auto"],
    ]
    last = None
    for names in candidates:
        last = _v4l2_get_ctrls(names)
        if last.get("ok"):
            return last
    return last or {"ok": False, "values": {}}

def _apply_controls_to_v4l2(controls_snapshot: dict) -> dict:
    """
    v4l2-ctl が利用できる環境では、露出制御をV4L2側で設定する（OpenCVより効きやすい）
    """
    result = {"requested": {}, "stdout": "", "stderr": "", "ok": False, "device": _v4l2_device_path()}
    if not _v4l2_available():
        result["stderr"] = "v4l2-ctl not available"
        return result

    device = result["device"]
    if not os.path.exists(device):
        result["stderr"] = f"device not found: {device}"
        return result

    auto_exposure = bool(controls_snapshot.get("auto_exposure", True))
    exposure_value = controls_snapshot.get("exposure")

    # このプロジェクトでは「exposure」は V4L2 の exposure_time_absolute/exposure_absolute を想定
    exposure_abs = None
    if exposure_value is not None:
        try:
            exposure_abs = int(float(exposure_value))
        except Exception:
            exposure_abs = None

    # V4L2: カメラによりコントロール名が異なるので複数候補を試す
    attempts: list[list[str]] = []
    if auto_exposure:
        # UVC系: auto_exposure=3 が「Aperture Priority(自動)」, =1 が手動
        attempts.append(["auto_exposure=3"])
        # 別名パターン
        attempts.append(["exposure_auto=3"])
        attempts.append(["exposure_auto=0"])
        result["requested"]["auto_exposure_candidates"] = [3]
        result["requested"]["exposure_auto_candidates"] = [3, 0]
    else:
        # まずUVC系の名前で試す
        controls = ["auto_exposure=1"]
        if exposure_abs is not None and exposure_abs > 0:
            controls.append(f"exposure_time_absolute={exposure_abs}")
            result["requested"]["exposure_time_absolute"] = exposure_abs
        attempts.append(controls)

        # 次に別名パターン
        controls2 = ["exposure_auto=1"]
        if exposure_abs is not None and exposure_abs > 0:
            controls2.append(f"exposure_absolute={exposure_abs}")
            result["requested"]["exposure_absolute"] = exposure_abs
        attempts.append(controls2)

    try:
        for controls in attempts:
            proc = subprocess.run(
                ["v4l2-ctl", "-d", device, "-c", ",".join(controls)],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            result["stdout"] = (proc.stdout or "").strip()
            result["stderr"] = (proc.stderr or "").strip()
            if proc.returncode == 0:
                result["ok"] = True
                result["requested"]["applied_controls"] = controls
                break
        # readback（取れる範囲で）
        result["readback"] = _v4l2_readback_exposure()
        return result
    except Exception as e:
        result["stderr"] = str(e)
        return result

def _controls_file_path() -> str:
    template = os.environ.get('CAMERA_CONTROLS_PATH', 'data/camera_controls_{camera_id}.json')
    camera_id = app.config.get('CAMERA_ID', 0)
    port = app.config.get('PORT', 0)
    path = template.format(camera_id=camera_id, port=port)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    return path

def _load_controls_from_disk() -> None:
    path = _controls_file_path()
    if not os.path.exists(path):
        return
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f) or {}
        with camera_control_lock:
            if "auto_exposure" in data:
                camera_controls["auto_exposure"] = bool(data["auto_exposure"])
            if "exposure" in data:
                camera_controls["exposure"] = data["exposure"]
            if "software_ev" in data:
                camera_controls["software_ev"] = float(data["software_ev"])
    except Exception as e:
        print(f"[カメラサーバー] controls読み込みに失敗しました: {e} ({path})")

def _save_controls_to_disk() -> None:
    path = _controls_file_path()
    try:
        with camera_control_lock:
            data = dict(camera_controls)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[カメラサーバー] controls保存に失敗しました: {e} ({path})")

def _apply_controls_to_camera() -> dict:
    """
    現在のcamera_controlsをハードウェア側に適用（可能な範囲で）
    戻り値は適用結果（set/get）を含む辞書
    """
    global camera, last_control_result
    result = {"applied": {}, "errors": []}

    if camera is None or not camera.isOpened():
        result["errors"].append("camera_not_open")
        last_control_result = result
        return result

    with camera_control_lock:
        controls_snapshot = dict(camera_controls)

    def try_set(prop, value, label):
        try:
            ok = camera.set(prop, float(value))
            got = None
            try:
                got = camera.get(prop)
            except Exception:
                got = None
            result["applied"][label] = {"requested": value, "set_ok": bool(ok), "got": got}
        except Exception as e:
            result["errors"].append(f"{label}:{e}")

    # Auto exposure: OpenCV/V4L2の慣習で 0.75=auto, 0.25=manual の場合がある
    if controls_snapshot.get("auto_exposure") is True:
        try_set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75, "auto_exposure")
        if not result["applied"]["auto_exposure"]["set_ok"]:
            try_set(cv2.CAP_PROP_AUTO_EXPOSURE, 1.0, "auto_exposure_alt")
    else:
        try_set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25, "manual_exposure")
        if not result["applied"]["manual_exposure"]["set_ok"]:
            try_set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.0, "manual_exposure_alt")

        # manual exposure value（機種依存）
        exposure_value = controls_snapshot.get("exposure")
        if exposure_value is not None:
            try_set(cv2.CAP_PROP_EXPOSURE, exposure_value, "exposure")

    # v4l2-ctl があれば併用（OpenCV set が効かない環境のフォールバック）
    v4l2_result = _apply_controls_to_v4l2(controls_snapshot)
    result["applied"]["v4l2"] = v4l2_result
    if _v4l2_available() and not v4l2_result.get("ok", False):
        result["errors"].append(f"v4l2_failed:{v4l2_result.get('stderr') or 'unknown'}")

    last_control_result = result
    return result

def camera_capture_loop(camera_device_id=0):
    """
    バックグラウンドでカメラからフレームを取得し続けるループ
    サーバー起動時に自動的に開始される
    """
    global camera, running, latest_frame, frame_lock
    
    print(f"[カメラサーバー] カメラデバイス {camera_device_id} を開きます...")
    camera = cv2.VideoCapture(camera_device_id)
    
    if not camera.isOpened():
        error_msg = f"カメラデバイス {camera_device_id} を開けませんでした"
        print(f"✗ {error_msg}")
        return
    
    print(f"✓ カメラデバイス {camera_device_id} を開きました")
    
    # カメラ設定
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    camera.set(cv2.CAP_PROP_FPS, 8)
    # 初期の露出設定を適用（可能な範囲で）
    _apply_controls_to_camera()
    
    frame_count = 0
    
    try:
        while running:
            ret, frame = camera.read()
            if not ret:
                print(f"[カメラサーバー] フレーム読み込みに失敗しました")
                time.sleep(0.1)
                continue

            # ソフトウェア補正（露出相当）
            with camera_control_lock:
                software_ev = float(camera_controls.get("software_ev", 0.0))
            if software_ev != 0.0:
                alpha = float(2.0 ** software_ev)
                frame = cv2.convertScaleAbs(frame, alpha=alpha, beta=0)
            
            # 最新フレームを更新（スレッドセーフ）
            with frame_lock:
                # JPEG形式にエンコード
                ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if ret:
                    latest_frame = buffer.tobytes()
                    frame_count += 1
                    
                    if frame_count % 300 == 0:  # 300フレームごとにログ出力（約10秒）
                        print(f"[カメラサーバー] {frame_count}フレームを生成しました")
            
            time.sleep(0.125)  # 約8fps
    except Exception as e:
        print(f"[カメラサーバー] エラーが発生しました: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if camera is not None:
            camera.release()
            print(f"[カメラサーバー] カメラをリリースしました")

def generate_frames():
    """
    ストリーミング用のフレーム生成ジェネレータ
    バックグラウンドで生成された最新フレームを返す
    """
    global running, latest_frame, frame_lock
    
    while running:
        with frame_lock:
            if latest_frame is not None:
                frame_bytes = latest_frame
            else:
                # フレームがまだ生成されていない場合は待機
                time.sleep(0.1)
                continue
        
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        
        time.sleep(0.125)  # 約8fps

@app.route('/stream')
def video_feed():
    """
    ストリーミングエンドポイント
    バックグラウンドで生成されたフレームをストリーミング
    """
    camera_id = app.config.get('CAMERA_ID', 0)
    
    print(f"[カメラサーバー] /stream へのリクエストを受信しました (カメラID: {camera_id})")
    
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame',
                    headers={
                        'Cache-Control': 'no-cache, no-store, must-revalidate',
                        'Pragma': 'no-cache',
                        'Expires': '0'
                    })

@app.route('/')
def index():
    """ステータスページ"""
    camera_id = app.config.get('CAMERA_ID', 0)
    port = app.config.get('PORT', 5001)
    return f"""
    <h1>カメラサーバー - カメラ {camera_id}</h1>
    <p>ポート: {port}</p>
    <p>ストリームURL: <a href="/stream">/stream</a></p>
    <p>ステータス: 稼働中</p>
    """

def get_local_ip():
    """ローカルネットワークのIPアドレスを取得"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            hostname = socket.gethostname()
            ip = socket.gethostbyname(hostname)
            return ip
        except Exception:
            return "unknown"

@app.route('/info')
def info():
    """
    子機情報を返すエンドポイント（母艦側の検出用）
    """
    camera_id = app.config.get('CAMERA_ID', 0)
    port = app.config.get('PORT', 5001)
    camera_device_id = app.config.get('CAMERA_DEVICE_ID', 0)
    ip_address = get_local_ip()
    
    return jsonify({
        'camera_id': camera_id,
        'port': port,
        'ip_address': ip_address,
        'stream_url': f"http://{ip_address}:{port}/stream",
        'status': 'running',
        'camera_device_id': camera_device_id
    })

@app.route('/controls', methods=['GET'])
def get_controls():
    """現在の露出/補正設定を取得"""
    with camera_control_lock:
        controls_snapshot = dict(camera_controls)
        last_snapshot = dict(last_control_result)
    return jsonify({
        "controls": controls_snapshot,
        "last_result": last_snapshot,
        "camera_open": bool(camera is not None and camera.isOpened()),
        "v4l2_available": _v4l2_available(),
        "device_path": _v4l2_device_path(),
        "v4l2_readback": _v4l2_readback_exposure(),
    })

@app.route('/controls', methods=['POST'])
def set_controls():
    """露出/補正設定を更新（可能ならハードウェアにも適用）"""
    payload = request.get_json(silent=True) or {}
    allowed_keys = {"auto_exposure", "exposure", "software_ev"}

    with camera_control_lock:
        for key in allowed_keys:
            if key in payload:
                camera_controls[key] = payload[key]
        # 値の正規化
        if camera_controls.get("software_ev") is None:
            camera_controls["software_ev"] = 0.0
        try:
            camera_controls["software_ev"] = float(camera_controls.get("software_ev", 0.0))
        except Exception:
            camera_controls["software_ev"] = 0.0

    _save_controls_to_disk()
    result = _apply_controls_to_camera()
    with camera_control_lock:
        controls_snapshot = dict(camera_controls)

    return jsonify({
        "ok": len(result.get("errors", [])) == 0,
        "controls": controls_snapshot,
        "result": result,
    })

def signal_handler(sig, frame):
    """シグナルハンドラ（Ctrl+C処理）"""
    global running, camera, camera_thread
    print("\n\nカメラサーバーを終了しています...")
    running = False
    
    # カメラスレッドの終了を待つ
    if camera_thread is not None and camera_thread.is_alive():
        camera_thread.join(timeout=2.0)
    
    if camera is not None:
        camera.release()
    
    print("カメラサーバーを終了しました")
    sys.exit(0)

if __name__ == '__main__':
    # シグナルハンドラを登録
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # コマンドライン引数または環境変数から設定を取得
    if len(sys.argv) >= 2:
        camera_id = int(sys.argv[1])
    else:
        camera_id = int(os.environ.get('CAMERA_ID', 0))
    
    if len(sys.argv) >= 3:
        port = int(sys.argv[2])
    else:
        port = int(os.environ.get('CAMERA_PORT', 5001 + camera_id))
    
    # カメラデバイスID（通常は0、USBカメラの場合は1など）
    camera_device_id = int(os.environ.get('CAMERA_DEVICE_ID', 0))
    
    app.config['CAMERA_ID'] = camera_id
    app.config['PORT'] = port
    app.config['CAMERA_DEVICE_ID'] = camera_device_id

    # 以前の調整値があれば読み込む（子機再起動後も維持）
    _load_controls_from_disk()
    
    print("="*60)
    print(f"カメラサーバーを起動します...")
    print(f"カメラID: {camera_id}")
    print(f"ポート: {port}")
    print(f"カメラデバイスID: {camera_device_id}")
    print("="*60)
    
    # バックグラウンドでカメラキャプチャを開始
    camera_thread = threading.Thread(
        target=camera_capture_loop,
        args=(camera_device_id,),
        daemon=True
    )
    camera_thread.start()
    
    # カメラが開かれるまで少し待つ
    time.sleep(1.0)
    
    # ローカルIPアドレスを取得して表示
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        print(f"✓ カメラサーバーが起動しました")
        print(f"  ストリームURL: http://{local_ip}:{port}/stream")
        print(f"  情報API: http://{local_ip}:{port}/info")
        print(f"  ステータス: http://{local_ip}:{port}/")
    except:
        print(f"✓ カメラサーバーが起動しました")
        print(f"  ストリームURL: http://0.0.0.0:{port}/stream")
    
    print("="*60)
    print("サーバーはスタンドアロンモードで動作中です。")
    print("ブラウザ操作は不要です。母艦から自動的に検出されます。")
    print("終了するには Ctrl+C を押してください\n")
    
    try:
        app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
    except KeyboardInterrupt:
        signal_handler(None, None)
