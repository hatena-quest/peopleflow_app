"""
Flaskアプリケーション: 4カメラストリーミング受信・統合・YOLO処理
"""
import cv2
import numpy as np
from flask import Flask, render_template, Response
from flask_socketio import SocketIO, emit
import threading
import queue
import time
from datetime import datetime
import json
import os
import signal
import sys
import requests
from yolo_processor import YOLOProcessor
import config
from camera_discovery import discover_cameras_fast, discover_cameras, discover_cameras_by_info, get_local_ip

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
# Python 3.13対応: eventletの代わりにthreadingモードを使用
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

@app.after_request
def add_no_cache_headers(response):
    """ブラウザキャッシュを防ぐ（Safari等の強いキャッシュ対策）"""
    response.headers.setdefault('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
    response.headers.setdefault('Pragma', 'no-cache')
    response.headers.setdefault('Expires', '0')
    return response

# YOLOプロセッサの初期化
yolo_processor = YOLOProcessor(
    model_path=config.YOLO_MODEL_PATH,
    confidence_threshold=config.YOLO_CONFIDENCE_THRESHOLD
)

# グローバル変数
camera_streams = {}  # カメラストリームの管理
stream_queues = {}  # 各カメラのフレームキュー
merged_frame = None  # 統合されたフレーム
merged_frame_lock = threading.Lock()  # 統合フレームのロック
running = True  # アプリケーションの実行状態
camera_threads = []  # カメラスレッドのリスト
camera_running = {}  # 各カメラの実行状態（カメラIDをキー、True/Falseを値）
camera_caps = {}  # 各カメラのVideoCaptureオブジェクト（停止時にリリースするため）
camera_targets = {}  # 各カメラの制御先（子機のbase_url/port/ip）

# カメラ設定（config.pyから読み込み）
MAX_CAMERAS = config.MAX_CAMERAS
CAMERA_PORTS = config.CAMERA_PORTS
# カメラベースURLは検出時に動的に決定（起動時は使用しない）

# データ保存用ディレクトリ
DATA_DIR = config.DATA_DIR
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# read_camera_stream関数は削除（read_camera_stream_with_urlに統合）

def update_merged_frame(camera_id, frame):
    """
    4つのカメラフレームを1つの画像に統合
    接続されているカメラだけでマージし、未接続のカメラは空白を開ける
    """
    global merged_frame
    
    # カメラフレームを保存
    camera_streams[camera_id] = frame.copy()
    
    # 接続されているカメラの数に関わらず、常にマージ画像を生成
    frames = []
    for i in range(MAX_CAMERAS):
        if i in camera_streams:
            # 接続されているカメラ: フレームをリサイズ（アスペクト比を保持）
            original_frame = camera_streams[i]
            original_h, original_w = original_frame.shape[:2]
            original_aspect = original_w / original_h
            
            # 目標サイズのアスペクト比
            target_aspect = config.FRAME_WIDTH / config.FRAME_HEIGHT
            
            # アスペクト比を保持してリサイズ
            if original_aspect > target_aspect:
                # 幅基準でリサイズ
                new_w = config.FRAME_WIDTH
                new_h = int(config.FRAME_WIDTH / original_aspect)
            else:
                # 高さ基準でリサイズ
                new_h = config.FRAME_HEIGHT
                new_w = int(config.FRAME_HEIGHT * original_aspect)
            
            resized = cv2.resize(original_frame, (new_w, new_h))
            
            # 目標サイズに合わせてパディング（中央配置）
            pad_h = (config.FRAME_HEIGHT - new_h) // 2
            pad_w = (config.FRAME_WIDTH - new_w) // 2
            
            padded = np.zeros((config.FRAME_HEIGHT, config.FRAME_WIDTH, 3), dtype=np.uint8)
            padded[pad_h:pad_h+new_h, pad_w:pad_w+new_w] = resized
            
            frames.append(padded)
        else:
            # 接続されていないカメラは黒画像（空白を開ける）
            blank_frame = np.zeros((config.FRAME_HEIGHT, config.FRAME_WIDTH, 3), dtype=np.uint8)
            cv2.putText(blank_frame, f'Camera {i} - No Signal', 
                       (10, config.FRAME_HEIGHT // 2), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (128, 128, 128), 1)
            frames.append(blank_frame)
    
    # 2x2グリッドに配置
    top_row = np.hstack([frames[0], frames[1]])
    bottom_row = np.hstack([frames[2], frames[3]])
    merged = np.vstack([top_row, bottom_row])
    
    with merged_frame_lock:
        merged_frame = merged

def get_latest_frame(camera_id):
    """
    指定カメラの最新フレームを取得
    """
    # camera_streamsから直接取得（最新のフレーム）
    if camera_id in camera_streams:
        return camera_streams[camera_id].copy()
    
    # キューから取得を試みる（フォールバック）
    if camera_id in stream_queues:
        try:
            frame = stream_queues[camera_id].get_nowait()
            # 取得したフレームをcamera_streamsにも保存
            camera_streams[camera_id] = frame.copy()
            return frame
        except queue.Empty:
            pass
    
    return None

def generate_frames(camera_id):
    """
    カメラストリーム用のジェネレータ（MJPEG形式）
    """
    print(f"[generate_frames] カメラ {camera_id} のストリーム生成を開始")
    last_frame = None
    frame_sent_count = 0
    
    while True:
        try:
            frame = get_latest_frame(camera_id)
            if frame is None:
                # フレームがない場合は最後のフレームを使用、または黒画像
                if last_frame is not None:
                    frame = last_frame
                else:
                    frame = np.zeros((480, 640, 3), dtype=np.uint8)
                    cv2.putText(frame, f'Camera {camera_id} - No Signal', 
                               (50, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            else:
                last_frame = frame.copy()
                frame_sent_count += 1
                if frame_sent_count == 1:
                    print(f"[generate_frames] カメラ {camera_id}: 最初のフレームを送信")
                if frame_sent_count % 30 == 0:
                    print(f"[generate_frames] カメラ {camera_id}: {frame_sent_count}フレーム送信")
            
            # JPEG形式にエンコード
            ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ret:
                print(f"[generate_frames] カメラ {camera_id}: JPEGエンコード失敗")
                time.sleep(0.033)  # 約30fps
                continue
            
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            
            # フレームレート制御（約30fps）
            time.sleep(0.033)
        except Exception as e:
            print(f"[generate_frames] カメラ {camera_id} エラー: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(0.1)
            continue

def generate_merged_frame():
    """
    統合フレーム用のジェネレータ（YOLO処理用）
    YOLO処理を実行して検出結果を描画したフレームを返す
    """
    while True:
        try:
            with merged_frame_lock:
                if merged_frame is not None:
                    frame = merged_frame.copy()
                else:
                    frame = np.zeros((480, 640, 3), dtype=np.uint8)
                    cv2.putText(frame, 'Merged Frame - No Signal', 
                               (50, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            
            # YOLO処理を実行（統合フレームのためcamera_idはNone）
            processed_frame, detections = yolo_processor.process_frame(frame, camera_id=None)
            
            # 検出結果をSocketIOで送信（リアルタイム更新用）
            if detections:
                try:
                    socketio.emit('yolo_detections', {
                        'detections': detections,
                        'timestamp': datetime.now().isoformat()
                    })
                except:
                    pass  # SocketIOエラーは無視
            
            ret, buffer = cv2.imencode('.jpg', processed_frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if not ret:
                time.sleep(0.033)
                continue
            
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            
            # フレームレート制御（約30fps）
            time.sleep(0.033)
        except Exception as e:
            print(f"generate_merged_frame error: {e}")
            time.sleep(0.1)
            continue

@app.route('/')
def index():
    """メインページ"""
    # カメラポート情報をテンプレートに渡す
    return render_template('index.html', camera_ports=CAMERA_PORTS)

@app.route('/video_feed/<int:camera_id>')
def video_feed(camera_id):
    """
    個別カメラのストリーミング（フロントエンド表示用）
    """
    if camera_id < 0 or camera_id >= MAX_CAMERAS:
        return "Invalid camera ID", 400
    
    return Response(generate_frames(camera_id),
                    mimetype='multipart/x-mixed-replace; boundary=frame',
                    headers={
                        'Cache-Control': 'no-cache, no-store, must-revalidate',
                        'Pragma': 'no-cache',
                        'Expires': '0'
                    })

@app.route('/merged_feed')
def merged_feed():
    """
    統合フレームのストリーミング（YOLO処理用）
    """
    return Response(generate_merged_frame(),
                    mimetype='multipart/x-mixed-replace; boundary=frame',
                    headers={
                        'Cache-Control': 'no-cache, no-store, must-revalidate',
                        'Pragma': 'no-cache',
                        'Expires': '0'
                    })

@socketio.on('connect')
def handle_connect():
    """クライアント接続時の処理"""
    print('クライアントが接続しました')
    emit('connected', {'message': 'サーバーに接続しました'})

@socketio.on('disconnect')
def handle_disconnect():
    """クライアント切断時の処理"""
    print('クライアントが切断しました')

# start_cameraイベントは削除（discover_and_connect_camerasに統合）

@socketio.on('stop_camera')
def handle_stop_camera(data):
    """カメラストリームの停止"""
    global camera_running, camera_caps, camera_targets
    camera_id = data.get('camera_id')
    
    print(f"[停止] カメラ {camera_id} の停止をリクエストしました")
    
    # カメラの実行状態をFalseに設定（スレッドを停止）
    if camera_id in camera_running:
        camera_running[camera_id] = False
        print(f"[停止] カメラ {camera_id} の実行フラグをFalseに設定しました")
    
    # スレッドが停止するまで少し待つ（cap.read()が実行中の場合があるため）
    time.sleep(0.2)
    
    # VideoCaptureをリリース（スレッドが停止した後）
    if camera_id in camera_caps:
        cap = camera_caps[camera_id]
        if cap is not None:
            try:
                if cap.isOpened():
                    cap.release()
                    print(f"[停止] カメラ {camera_id} のVideoCaptureをリリースしました")
            except Exception as e:
                print(f"[停止] カメラ {camera_id} のVideoCaptureリリース中にエラー: {e}")
            finally:
                camera_caps.pop(camera_id, None)
    
    # データ構造から削除
    if camera_id in camera_streams:
        camera_streams.pop(camera_id)
    if camera_id in stream_queues:
        stream_queues.pop(camera_id)
    camera_targets.pop(camera_id, None)
    
    emit('camera_stopped', {'camera_id': camera_id})
    print(f"✓ カメラ {camera_id} を停止しました")

@socketio.on('get_status')
def handle_get_status():
    """システムステータスの取得"""
    status = {
        'cameras': {},
        'merged_frame_available': merged_frame is not None,
        'timestamp': datetime.now().isoformat()
    }
    for i in range(MAX_CAMERAS):
        is_connected = i in camera_streams
        is_running = camera_running.get(i, False)
        queue_size = stream_queues[i].qsize() if i in stream_queues else 0
        has_cap = i in camera_caps and camera_caps[i] is not None
        
        status['cameras'][i] = {
            'connected': is_connected,
            'running': is_running,
            'queue_size': queue_size,
            'has_capture': has_cap,
            'port': CAMERA_PORTS[i] if i < len(CAMERA_PORTS) else None
        }
    
    print(f"[ステータス] ステータス情報を送信しました: {len([c for c in status['cameras'].values() if c['connected']])}台接続中")
    emit('status', status)

@socketio.on('set_camera_controls')
def handle_set_camera_controls(data):
    """
    子機カメラの露出/補正設定を更新（master_consoleが代理でHTTPリクエスト）
    data: {camera_id, auto_exposure?, exposure?, software_ev?}
    """
    camera_id = data.get('camera_id')
    if camera_id is None:
        emit('camera_controls_applied', {'ok': False, 'message': 'camera_id がありません'})
        return
    try:
        camera_id = int(camera_id)
    except Exception:
        emit('camera_controls_applied', {'ok': False, 'message': 'camera_id が不正です'})
        return

    target = camera_targets.get(camera_id)
    if not target:
        emit('camera_controls_applied', {'ok': False, 'camera_id': camera_id, 'message': 'カメラ制御先が未検出です（接続を更新してください）'})
        return

    url = f"{target['base_url']}:{target['port']}/controls"
    payload = {}
    for key in ("auto_exposure", "exposure", "software_ev"):
        if key in data:
            payload[key] = data[key]

    try:
        resp = requests.post(url, json=payload, timeout=1.5)
        body = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {"text": resp.text}
        ok = bool(resp.ok and body.get('ok', True))
        message = '適用しました'
        if not ok:
            errors = (body.get('result') or {}).get('errors') if isinstance(body, dict) else None
            if errors:
                message = f"適用に失敗しました: {', '.join(map(str, errors))}"
            else:
                message = f'適用に失敗しました: HTTP {resp.status_code}'

        emit('camera_controls_applied', {
            'ok': bool(ok),
            'camera_id': camera_id,
            'target': target,
            'response': body,
            'message': message,
        })
    except Exception as e:
        emit('camera_controls_applied', {
            'ok': False,
            'camera_id': camera_id,
            'target': target,
            'message': f'子機へのリクエストに失敗しました: {e}',
        })

@socketio.on('get_camera_controls')
def handle_get_camera_controls(data):
    """子機カメラの現在設定を取得"""
    camera_id = data.get('camera_id')
    if camera_id is None:
        emit('camera_controls', {'ok': False, 'message': 'camera_id がありません'})
        return
    try:
        camera_id = int(camera_id)
    except Exception:
        emit('camera_controls', {'ok': False, 'message': 'camera_id が不正です'})
        return

    target = camera_targets.get(camera_id)
    if not target:
        emit('camera_controls', {'ok': False, 'camera_id': camera_id, 'message': 'カメラ制御先が未検出です'})
        return

    url = f"{target['base_url']}:{target['port']}/controls"
    try:
        resp = requests.get(url, timeout=1.5)
        body = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {"text": resp.text}
        ok = bool(resp.ok)
        emit('camera_controls', {
            'ok': bool(ok),
            'camera_id': camera_id,
            'target': target,
            'response': body,
        })
    except Exception as e:
        emit('camera_controls', {
            'ok': False,
            'camera_id': camera_id,
            'target': target,
            'message': f'子機へのリクエストに失敗しました: {e}',
        })

def signal_handler(sig, frame):
    """シグナルハンドラ（Ctrl+C処理）"""
    global running
    print("\n\nアプリケーションを終了しています...")
    running = False
    
    # 集計スレッドを停止
    yolo_processor.stop_aggregation_thread()
    
    # カメラスレッドの終了を待つ
    for thread in camera_threads:
        if thread.is_alive():
            thread.join(timeout=2)
    
    print("アプリケーションを終了しました")
    sys.exit(0)

@socketio.on('discover_and_connect_cameras')
def handle_discover_cameras():
    """
    カメラを検出して接続（ブラウザからの要求）
    見つかったカメラは即座に接続し、探索は並行で続ける
    """
    print("\nカメラ検出・接続を開始します...")
    
    # 既に接続されたカメラを追跡（重複接続を防ぐ）
    connected_ports = set()
    discovered_cameras = {}
    
    def connect_camera_immediately(port, ip):
        """見つかったカメラを即座に接続する関数"""
        if port in connected_ports:
            return  # 既に接続済み
        
        # ポートからカメラIDを取得
        try:
            camera_id = CAMERA_PORTS.index(port)
        except ValueError:
            print(f"[警告] ポート {port} が設定にありません。スキップします。")
            return
        
        connected_ports.add(port)
        discovered_cameras[port] = ip
        print(f"\n[即座接続] カメラ {camera_id} (ポート {port}, IP: {ip}) を接続します...")
        
        # 既に接続されている場合は、一度停止してから再接続
        if camera_id in camera_streams:
            print(f"[再接続] カメラ {camera_id} は既に接続されています。停止してから再接続します...")
            # 停止処理を実行
            if camera_id in camera_running:
                camera_running[camera_id] = False
            time.sleep(0.3)  # スレッドが停止するまで待つ
            # VideoCaptureをリリース
            if camera_id in camera_caps:
                cap = camera_caps[camera_id]
                if cap is not None:
                    try:
                        if cap.isOpened():
                            cap.release()
                    except Exception as e:
                        print(f"[再接続] カメラ {camera_id} のVideoCaptureリリース中にエラー: {e}")
                    finally:
                        camera_caps.pop(camera_id, None)
            # データ構造から削除
            camera_streams.pop(camera_id, None)
            stream_queues.pop(camera_id, None)
            camera_running.pop(camera_id, None)
        
        # キューを作成
        if camera_id not in stream_queues:
            stream_queues[camera_id] = queue.Queue(maxsize=config.STREAM_QUEUE_SIZE)
        
        # ストリーム読み込みスレッドを開始
        base_url = f"http://{ip}"
        camera_targets[camera_id] = {"ip": ip, "port": port, "base_url": base_url}
        thread = threading.Thread(target=read_camera_stream_with_url, 
                                 args=(camera_id, port, base_url), daemon=True)
        camera_threads.append(thread)
        thread.start()
        print(f"[即座接続] カメラ {camera_id} (ポート {port}, IP: {ip}) の接続スレッドを開始しました")
        
        # フロントエンドに即座に通知（ストリーミング開始を促す）
        socketio.emit('camera_connected', {
            'camera_id': camera_id,
            'port': port,
            'ip': ip,
            'status': 'connecting'
        })
    
    # まず高速モードで検出（localhostと現在のホスト）
    fast_scan_results = discover_cameras_fast(ports=CAMERA_PORTS)
    # 見つかったカメラを即座に接続
    for port, ip in fast_scan_results.items():
        connect_camera_immediately(port, ip)
    
    # 4台すべて見つからない場合は、HTTPベースの検出を試行（/infoエンドポイント使用）
    if len(connected_ports) < MAX_CAMERAS:
        print(f"高速モードで {len(connected_ports)} 台接続。HTTPベースの検出を実行します...")
        # 既知の子機IPアドレスがあればデバッグモードでスキャン（環境変数から取得可能）
        debug_ips = os.getenv('DEBUG_CAMERA_IPS', '').split(',')
        debug_ips = [ip.strip() for ip in debug_ips if ip.strip()]
        # コールバック関数を渡して、見つかったカメラを即座に接続
        http_scan_results = discover_cameras_by_info(
            ports=CAMERA_PORTS, 
            timeout=1.0, 
            debug_ips=debug_ips,
            on_camera_found=connect_camera_immediately
        )
        # 結果をマージ（念のため）
        for port, ip in http_scan_results.items():
            if port not in discovered_cameras:
                discovered_cameras[port] = ip
    
    # それでも見つからない場合は、ポートスキャンで検出（フォールバック）
    if len(connected_ports) < MAX_CAMERAS:
        print(f"HTTP検出で {len(connected_ports)} 台接続。ポートスキャンを実行します...")
        port_scan_results = discover_cameras(ports=CAMERA_PORTS, timeout=0.3, scan_localhost=False)
        # 結果をマージして接続
        for port, ip in port_scan_results.items():
            if port not in discovered_cameras:
                discovered_cameras[port] = ip
            if port not in connected_ports:
                connect_camera_immediately(port, ip)
    
    if not connected_ports:
        emit('camera_discovery_result', {
            'found': 0,
            'message': 'カメラが見つかりませんでした'
        })
        print("カメラが見つかりませんでした")
        return
    
    emit('camera_discovery_result', {
        'found': len(discovered_cameras),
        'connected': len(connected_ports),
        'message': f'{len(connected_ports)}台のカメラに接続しました（探索は継続中）'
    })
    print(f"{len(connected_ports)}台のカメラに接続しました（探索は継続中）")

def read_camera_stream_with_url(camera_id, port, base_url):
    """
    カメラストリームを読み込む（URL指定版）
    """
    global running, camera_running, camera_caps
    url = f"{base_url}:{port}/stream"
    print(f"\n[カメラ {camera_id}] 接続を試みます: {url}")
    
    # カメラの実行状態をTrueに設定
    camera_running[camera_id] = True
    
    # OpenCVのVideoCaptureでMJPEGストリームを読み込む
    cap = cv2.VideoCapture(url)
    camera_caps[camera_id] = cap  # 後でリリースするために保存
    
    # タイムアウト設定（重要）
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # バッファを最小化
    
    if not cap.isOpened():
        error_msg = f"カメラ {camera_id} (ポート {port}, URL: {url}) に接続できませんでした"
        print(f"✗ {error_msg}")
        camera_running.pop(camera_id, None)
        camera_caps.pop(camera_id, None)
        socketio.emit('camera_status', {
            'camera_id': camera_id,
            'status': 'disconnected',
            'message': f'ポート {port} に接続できません'
        })
        return
    
    success_msg = f"カメラ {camera_id} (ポート {port}) に接続しました"
    print(f"✓ {success_msg}")
    socketio.emit('camera_status', {
        'camera_id': camera_id,
        'status': 'connected',
        'message': f'ポート {port} に接続しました'
    })
    
    print(f"[カメラ {camera_id}] ストリーム読み込みを開始しました")
    
    frame_count = 0
    no_frame_count = 0
    while running and camera_running.get(camera_id, False):
        # capが有効かどうかをチェック
        if cap is None or not cap.isOpened():
            print(f"[カメラ {camera_id}] VideoCaptureが無効になりました。ループを終了します。")
            break
        
        try:
            ret, frame = cap.read()
        except Exception as e:
            print(f"[カメラ {camera_id}] cap.read()でエラーが発生しました: {e}")
            # capが無効になった可能性があるので、ループを終了
            break
        
        if not ret:
            no_frame_count += 1
            if not running or not camera_running.get(camera_id, False):
                break
            if no_frame_count % 10 == 0:  # 10回連続で失敗したらログ出力
                print(f"[カメラ {camera_id}] フレーム読み込み失敗 ({no_frame_count}回連続)")
            time.sleep(0.1)  # 短い待機時間
            continue
        
        # フレーム読み込み成功
        no_frame_count = 0
        frame_count += 1
        
        if frame_count == 1:
            print(f"[カメラ {camera_id}] 最初のフレームを受信しました！")
        if frame_count % 30 == 0:  # 30フレームごとにログ出力
            print(f"[カメラ {camera_id}] {frame_count}フレーム受信 (サイズ: {frame.shape})")
        
        # フレームをcamera_streamsに保存（統合フレーム用・個別表示用）
        camera_streams[camera_id] = frame.copy()
        
        # フレームをキューに追加（個別表示用のバックアップ）
        if camera_id in stream_queues:
            try:
                stream_queues[camera_id].put_nowait(frame)
            except queue.Full:
                # キューが満杯の場合は古いフレームを破棄
                try:
                    stream_queues[camera_id].get_nowait()
                    stream_queues[camera_id].put_nowait(frame)
                except queue.Empty:
                    pass
        
        # 統合フレームの更新
        if running and camera_running.get(camera_id, False):
            update_merged_frame(camera_id, frame)
    
    # クリーンアップ
    print(f"[カメラ {camera_id}] ストリームを停止しています...")
    
    # VideoCaptureを安全にリリース
    if cap is not None:
        try:
            if cap.isOpened():
                cap.release()
                print(f"[カメラ {camera_id}] VideoCaptureをリリースしました")
        except Exception as e:
            print(f"[カメラ {camera_id}] VideoCaptureリリース中にエラー: {e}")
        finally:
            cap = None
    
    # グローバル変数から削除
    camera_caps.pop(camera_id, None)
    camera_running.pop(camera_id, None)
    if camera_id in camera_streams:
        camera_streams.pop(camera_id)
    if camera_id in stream_queues:
        stream_queues.pop(camera_id)
    
    print(f"✓ カメラ {camera_id} のストリームを終了しました")

if __name__ == '__main__':
    # シグナルハンドラを登録
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    PORT = int(os.environ.get("MASTER_PORT", "5050"))
    print("\n" + "="*60)
    print("Flaskアプリケーションを起動します...")
    print(f"ブラウザで http://localhost:{PORT} にアクセスしてください")
    print("="*60)
    print("カメラは接続されていません。ブラウザで「接続を更新」ボタンを押してください。")
    print("終了するには Ctrl+C を押してください\n")
    
    try:
        # Flask-SocketIO のイベント/接続を有効にするため socketio.run を使用する
        # （threading モードでも socketio.run が必要）
        try:
            socketio.run(app, host="0.0.0.0", port=PORT, debug=False, allow_unsafe_werkzeug=True)
        except TypeError:
            # Flask-SocketIO のバージョン差異で allow_unsafe_werkzeug が無い場合
            socketio.run(app, host="0.0.0.0", port=PORT, debug=False)
    except KeyboardInterrupt:
        signal_handler(None, None)
