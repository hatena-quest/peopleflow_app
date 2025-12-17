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
from flask import Flask, Response, jsonify
import cv2
import numpy as np
import threading
import time
import sys
import os
import signal
import socket

app = Flask(__name__)

# グローバル変数
camera = None
running = True
latest_frame = None
frame_lock = threading.Lock()
camera_thread = None

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
    
    frame_count = 0
    
    try:
        while running:
            ret, frame = camera.read()
            if not ret:
                print(f"[カメラサーバー] フレーム読み込みに失敗しました")
                time.sleep(0.1)
                continue
            
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

