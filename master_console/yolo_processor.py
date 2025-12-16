"""
YOLO処理モジュール
統合されたフレームに対して人物検出を行う
"""
import cv2
import numpy as np
from datetime import datetime, timedelta, timezone
import queue
import threading
import json
import os
import config

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False
    print("警告: ultralyticsがインストールされていません。YOLO機能は使用できません。")
    print("インストール: pip install ultralytics")


def _resolve_local_timezone():
    tz_name = os.environ.get("APP_TIMEZONE", "Asia/Tokyo")
    if ZoneInfo:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    offset_hours = int(os.environ.get("APP_TIMEZONE_OFFSET", "9"))
    return timezone(timedelta(hours=offset_hours))


LOCAL_TZ = _resolve_local_timezone()


def now_local():
    return datetime.now(LOCAL_TZ)


def format_local_iso(dt=None):
    dt = dt or now_local()
    if dt.tzinfo:
        dt = dt.astimezone(LOCAL_TZ)
    else:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.replace(tzinfo=None).isoformat()


def parse_to_local_datetime(timestamp_str):
    if not timestamp_str:
        return None
    try:
        dt = datetime.fromisoformat(timestamp_str)
    except ValueError:
        return None
    if dt.tzinfo:
        return dt.astimezone(LOCAL_TZ)
    return dt.replace(tzinfo=LOCAL_TZ)

class YOLOProcessor:
    """
    YOLOによる人物検出とトラッキング処理
    """
    def __init__(self, model_path=None, confidence_threshold=0.5):
        """
        初期化
        
        Args:
            model_path: YOLOモデルのパス（Noneの場合はデフォルトモデルを使用）
            confidence_threshold: 検出の信頼度閾値
        """
        self.confidence_threshold = confidence_threshold
        self.detection_queue = queue.Queue()  # 検出結果を保存するキュー
        self.data_dir = "data"
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
        
        # YOLOモデルの読み込み
        self.model = None
        self.tracker = {}  # トラッキング用（簡易実装）
        self.previous_positions = {}  # 前フレームの位置（方向判定用）
        
        # 統計用データ
        self.detection_history = []  # 検出履歴
        
        # 1分ごとの集計処理用
        self.aggregation_running = False
        self.aggregation_thread = None
        self.last_aggregation_time = None
        self.last_cleanup_time = None  # 最後にクリーンアップを実行した時刻
        
        # モデルを読み込む
        if YOLO_AVAILABLE:
            self.load_model(model_path)
        else:
            print("YOLOは使用できません（ultralyticsがインストールされていません）")
        
        # 1分ごとの集計処理を開始
        self.start_aggregation_thread()
        
    def load_model(self, model_path=None):
        """
        YOLOモデルを読み込む
        """
        if not YOLO_AVAILABLE:
            print("YOLOモデルを読み込めません（ultralyticsがインストールされていません）")
            return
        
        try:
            # デフォルトはYOLOv8n（nano、最も軽量）
            model_name = model_path or 'yolov8n.pt'
            print(f"YOLOモデルを読み込み中: {model_name}")
            self.model = YOLO(model_name)
            print(f"✓ YOLOモデルを読み込みました: {model_name}")
        except Exception as e:
            print(f"✗ YOLOモデルの読み込みに失敗しました: {e}")
            self.model = None
    
    def process_frame(self, frame, camera_id=None):
        """
        フレームに対して人物検出を実行
        
        Args:
            frame: 入力フレーム（BGR形式）
            camera_id: カメラID（統合フレームの場合はNone）
        
        Returns:
            processed_frame: 検出結果を描画したフレーム
            detections: 検出結果のリスト
        """
        if self.model is None:
            # モデルが読み込まれていない場合は元のフレームを返す
            return frame, []
        
        # YOLO検出処理を実行
        try:
            # YOLOで検出（personクラス = 0）
            results = self.model(frame, conf=self.confidence_threshold, classes=[0], verbose=False)
            
            # 検出結果をパース
            detections = self.parse_detections(results[0], frame)
            
            # 検出結果をフレームに描画
            processed_frame = self.draw_detections(frame, detections)
            
            # 検出結果をキューに追加
            if detections:
                self.save_detection_data(detections, camera_id)
            
            return processed_frame, detections
            
        except Exception as e:
            print(f"YOLO処理エラー: {e}")
            import traceback
            traceback.print_exc()
            return frame, []
    
    def parse_detections(self, yolo_results, frame):
        """
        YOLOの検出結果をパース
        
        Args:
            yolo_results: YOLOモデルの出力（Resultsオブジェクト）
            frame: 現在のフレーム（位置計算用）
        
        Returns:
            detections: 検出結果のリスト
        """
        detections = []
        
        if yolo_results.boxes is None or len(yolo_results.boxes) == 0:
            return detections
        
        frame_height, frame_width = frame.shape[:2]
        
        for i, box in enumerate(yolo_results.boxes):
            # バウンディングボックスの座標を取得
            x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
            confidence = float(box.conf[0].cpu().numpy())
            class_id = int(box.cls[0].cpu().numpy())
            
            # 人物（class_id = 0）のみを処理
            if class_id != 0:
                continue
            
            # 中心座標を計算
            center_x = (x1 + x2) / 2
            center_y = (y1 + y2) / 2
            
            # 統合フレームからカメラIDを判定
            camera_id = self.determine_camera_id_from_position(center_x, center_y, frame_width, frame_height)
            
            # トラッキングID（カメラIDを含める）
            track_id = f"camera{camera_id}_person_{i}"
            
            # 移動方向を判定（カメラごとにトラッキング）
            direction = None
            if track_id in self.previous_positions:
                prev_x = self.previous_positions[track_id][0]
                direction = self.determine_direction(track_id, (center_x, center_y), (prev_x, center_y))
            
            # 位置を更新
            self.previous_positions[track_id] = (center_x, center_y)
            
            detection = {
                'track_id': track_id,
                'camera_id': camera_id,
                'bbox': [float(x1), float(y1), float(x2), float(y2)],
                'center': [float(center_x), float(center_y)],
                'confidence': confidence,
                'direction': direction
            }
            detections.append(detection)
        
        return detections
    
    def draw_detections(self, frame, detections):
        """
        検出結果をフレームに描画
        
        Args:
            frame: 入力フレーム
            detections: 検出結果のリスト
        
        Returns:
            processed_frame: 検出結果を描画したフレーム
        """
        processed_frame = frame.copy()
        
        for detection in detections:
            x1, y1, x2, y2 = [int(v) for v in detection['bbox']]
            confidence = detection['confidence']
            direction = detection.get('direction', 'unknown')
            track_id = detection.get('track_id', 'unknown')
            
            # バウンディングボックスを描画
            color = (0, 255, 0) if direction == 'right' else (255, 0, 0) if direction == 'left' else (0, 255, 255)
            cv2.rectangle(processed_frame, (x1, y1), (x2, y2), color, 2)
            
            # ラベルを描画
            label = f"Person {confidence:.2f} {direction}"
            label_size, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)
            cv2.rectangle(processed_frame, (x1, y1 - label_size[1] - 10), 
                         (x1 + label_size[0], y1), color, -1)
            cv2.putText(processed_frame, label, (x1, y1 - 5), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
        
        return processed_frame
    
    def track_objects(self, detections, frame):
        """
        検出したオブジェクトをトラッキング
        
        Args:
            detections: 検出結果
            frame: 現在のフレーム
        
        Returns:
            tracked_objects: トラッキング結果
        """
        # TODO: DeepSORT等のトラッキング処理を実装
        tracked_objects = []
        return tracked_objects
    
    def determine_camera_id_from_position(self, center_x, center_y, frame_width, frame_height):
        """
        統合フレーム内の位置からカメラIDを判定
        
        統合フレームの構造（2x2グリッド）:
        - 左上（カメラ0）: (0, 0) から (FRAME_WIDTH, FRAME_HEIGHT)
        - 右上（カメラ1）: (FRAME_WIDTH, 0) から (2*FRAME_WIDTH, FRAME_HEIGHT)
        - 左下（カメラ2）: (0, FRAME_HEIGHT) から (FRAME_WIDTH, 2*FRAME_HEIGHT)
        - 右下（カメラ3）: (FRAME_WIDTH, FRAME_HEIGHT) から (2*FRAME_WIDTH, 2*FRAME_HEIGHT)
        
        Args:
            center_x: 検出された人物の中心X座標
            center_y: 検出された人物の中心Y座標
            frame_width: 統合フレームの幅
            frame_height: 統合フレームの高さ
        
        Returns:
            camera_id: カメラID (0-3)
        """
        # 各カメラのサイズ
        cam_width = config.FRAME_WIDTH
        cam_height = config.FRAME_HEIGHT
        
        # どのカメラ領域にいるかを判定
        if center_x < cam_width:
            # 左側
            if center_y < cam_height:
                return 0  # 左上（カメラ0）
            else:
                return 2  # 左下（カメラ2）
        else:
            # 右側
            if center_y < cam_height:
                return 1  # 右上（カメラ1）
            else:
                return 3  # 右下（カメラ3）
    
    def determine_direction(self, track_id, current_position, previous_position):
        """
        移動方向を判定（右/左）
        
        Args:
            track_id: トラッキングID
            current_position: 現在の位置
            previous_position: 前フレームの位置
        
        Returns:
            direction: 'right' または 'left'
        """
        if previous_position is None:
            return None
        
        # X座標の変化で方向を判定
        dx = current_position[0] - previous_position[0]
        
        if dx > 5:  # 閾値
            return 'right'
        elif dx < -5:
            return 'left'
        else:
            return None  # 移動が少ない
    
    def save_detection_data(self, detections, camera_id_param=None):
        """
        検出データをJSONL形式で保存
        
        Args:
            detections: 検出結果（各detectionにcamera_idが含まれる）
            camera_id_param: パラメータとして渡されたカメラID（統合フレームの場合はNone）
        """
        timestamp = format_local_iso()
        jsonl_file = os.path.join(self.data_dir, "detections.jsonl")
        
        for detection in detections:
            # 検出結果からcamera_idを取得（統合フレームの場合はdetectionに含まれる）
            detection_camera_id = detection.get("camera_id")
            if detection_camera_id is None:
                detection_camera_id = camera_id_param  # フォールバック
            
            data = {
                "timestamp": timestamp,
                "camera_id": detection_camera_id,
                "direction": detection.get("direction"),
                "person_count": 1,
                "detection_id": detection.get("track_id", "unknown")
            }
            
            # JSONL形式で追記
            with open(jsonl_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(data, ensure_ascii=False) + '\n')
            
            # キューにも追加（リアルタイム処理用）
            try:
                self.detection_queue.put_nowait(data)
            except queue.Full:
                pass
    
    def get_latest_detections(self, max_count=10):
        """
        最新の検出結果を取得
        
        Args:
            max_count: 取得する最大件数
        
        Returns:
            detections: 検出結果のリスト
        """
        detections = []
        while len(detections) < max_count and not self.detection_queue.empty():
            try:
                detections.append(self.detection_queue.get_nowait())
            except queue.Empty:
                break
        return detections
    
    def start_aggregation_thread(self):
        """
        1分ごとの集計処理スレッドを開始
        """
        if self.aggregation_running:
            return
        
        self.aggregation_running = True
        self.aggregation_thread = threading.Thread(target=self._aggregation_worker, daemon=True)
        self.aggregation_thread.start()
        print("[集計処理] 1分ごとの集計処理を開始しました")
    
    def stop_aggregation_thread(self):
        """
        集計処理スレッドを停止
        """
        self.aggregation_running = False
        if self.aggregation_thread and self.aggregation_thread.is_alive():
            self.aggregation_thread.join(timeout=2)
        print("[集計処理] 1分ごとの集計処理を停止しました")
    
    def _aggregation_worker(self):
        """
        1分ごとの集計処理を実行するワーカースレッド
        """
        import time
        
        while self.aggregation_running:
            try:
                # 現在時刻を取得
                now = now_local()
                # 1分前の時刻を計算（分単位で切り捨て）
                minute_start = now.replace(second=0, microsecond=0)
                
                # 初回実行時、または1分経過した場合
                if self.last_aggregation_time is None or minute_start > self.last_aggregation_time:
                    if self.last_aggregation_time is not None:
                        # 前回の集計期間のデータを集計
                        self._aggregate_detections(self.last_aggregation_time, minute_start)
                    
                    self.last_aggregation_time = minute_start
                
                # 5分ごとに古いデータをクリーンアップ（30分より古いデータを削除）
                if (self.last_cleanup_time is None or 
                    (now - self.last_cleanup_time).total_seconds() >= 300):  # 5分 = 300秒
                    self._cleanup_old_data()
                    self.last_cleanup_time = now
                
                # 次の分の開始時刻まで待機
                next_minute = minute_start + timedelta(minutes=1)
                sleep_seconds = (next_minute - now_local()).total_seconds()
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
                else:
                    time.sleep(1)  # 最小1秒待機
                    
            except Exception as e:
                print(f"[集計処理] エラー: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(60)  # エラー時は60秒待機
    
    def _aggregate_detections(self, start_time, end_time):
        """
        指定期間の検出データを集計
        
        Args:
            start_time: 集計開始時刻（datetime）
            end_time: 集計終了時刻（datetime）
        """
        jsonl_file = os.path.join(self.data_dir, "detections.jsonl")
        minutely_file = os.path.join(self.data_dir, "detections_minutely.jsonl")
        
        if not os.path.exists(jsonl_file):
            return
        
        # 期間内のデータを読み込んで集計
        aggregated_data = {}  # {(camera_id, direction): count}
        unique_detections = {}  # {camera_id: set of detection_ids}
        
        try:
            with open(jsonl_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                        timestamp_str = data.get('timestamp', '')
                        detection_time = parse_to_local_datetime(timestamp_str)
                        if detection_time is None:
                            continue
                        
                        # 集計期間内かチェック
                        if start_time <= detection_time < end_time:
                            camera_id = data.get('camera_id')
                            direction = data.get('direction', 'unknown')
                            detection_id = data.get('detection_id', '')
                            
                            if camera_id is not None:
                                # カメラIDと方向ごとに集計
                                key = (camera_id, direction)
                                aggregated_data[key] = aggregated_data.get(key, 0) + 1
                                
                                # ユニークな検出IDを記録
                                if camera_id not in unique_detections:
                                    unique_detections[camera_id] = set()
                                if detection_id:
                                    unique_detections[camera_id].add(detection_id)
                    except (json.JSONDecodeError, ValueError, KeyError) as e:
                        continue  # 不正な行はスキップ
            
            # 集計結果をカメラごとにまとめる
            for camera_id in range(4):  # 0-3のカメラID
                right_count = aggregated_data.get((camera_id, 'right'), 0)
                left_count = aggregated_data.get((camera_id, 'left'), 0)
                unknown_count = aggregated_data.get((camera_id, None), 0) + aggregated_data.get((camera_id, 'unknown'), 0)
                total_count = right_count + left_count + unknown_count
                unique_count = len(unique_detections.get(camera_id, set()))
                
                if total_count > 0:  # データがある場合のみ保存
                    aggregated_result = {
                        'timestamp': format_local_iso(start_time),
                        'camera_id': camera_id,
                        'right_count': right_count,
                        'left_count': left_count,
                        'unknown_count': unknown_count,
                        'total_count': total_count,
                        'unique_detections': unique_count
                    }
                    
                    # 1分ごとの集計データを保存
                    with open(minutely_file, 'a', encoding='utf-8') as f:
                        f.write(json.dumps(aggregated_result, ensure_ascii=False) + '\n')
                    
                    print(f"[集計処理] {start_time.strftime('%Y-%m-%d %H:%M')} - カメラ{camera_id}: "
                          f"右={right_count}, 左={left_count}, 合計={total_count}, ユニーク={unique_count}")
        
        except Exception as e:
            print(f"[集計処理] 集計中にエラー: {e}")
            import traceback
            traceback.print_exc()
    
    def _cleanup_old_data(self):
        """
        30分より古いデータを削除
        """
        jsonl_file = os.path.join(self.data_dir, "detections.jsonl")
        
        if not os.path.exists(jsonl_file):
            return
        
        # 30分前の時刻を計算
        cutoff_time = now_local() - timedelta(minutes=30)
        
        try:
            # ファイルを読み込んで、30分以内のデータのみを保持
            kept_lines = []
            removed_count = 0
            total_count = 0
            
            with open(jsonl_file, 'r', encoding='utf-8') as f:
                for line in f:
                    total_count += 1
                    line = line.strip()
                    if not line:
                        continue
                    
                    try:
                        data = json.loads(line)
                        timestamp_str = data.get('timestamp', '')
                        detection_time = parse_to_local_datetime(timestamp_str)
                        if detection_time is None:
                            kept_lines.append(line)
                            continue
                        
                        # 30分以内のデータのみを保持
                        if detection_time >= cutoff_time:
                            kept_lines.append(line)
                        else:
                            removed_count += 1
                    except json.JSONDecodeError:
                        # JSONパースエラーは削除
                        removed_count += 1
                        continue
            
            # ファイルを上書き（30分以内のデータのみ）
            if removed_count > 0:
                with open(jsonl_file, 'w', encoding='utf-8') as f:
                    for line in kept_lines:
                        f.write(line + '\n')
                
                print(f"[クリーンアップ] {removed_count}件の古いデータを削除しました "
                      f"(保持: {len(kept_lines)}件, 合計: {total_count}件)")
            else:
                print(f"[クリーンアップ] 削除する古いデータはありませんでした (合計: {total_count}件)")
        
        except Exception as e:
            print(f"[クリーンアップ] エラー: {e}")
            import traceback
            traceback.print_exc()
