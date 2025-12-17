# YOLO Takoyaki 人流検出システム

## 概要

最大4台のカメラからストリーミング画像を取得し、YOLOで人物検出を行い、人流データを収集・分析するシステムです。

## プロジェクト構成 (2024-XX 整理後)

- `order_counter/`  
  - `app.py`: 日次注文カウンターと配信/母艦制御API (`python order_counter/app.py`)  
  - `static/`: カウンターUI (`index.html`, `app.js`, 画像など)
- `predictor/`  
  - `app.py`: たこ焼き注文数予測ダッシュボード (`PREDICT_PORT` 既定5100)  
  - `dummy.py`, `train_model.py`, `predict_orders.py`, `predict_realtime.py`  
  - `templates/`: 予測UIテンプレート  
  - `static/`: 忙しさアイコン等  
  - `data/`: `detections_minutely.jsonl`, `orders.jsonl`, `model.json`, `prediction_results.txt`
- `master_console/`: 4カメラ統合ビュー & YOLO カウント処理（従来どおり）
- `camera_server.py`: 各子機側ストリーミングサーバ
- `requirements.txt`: 予測ダッシュボード/日次カウンター共通の依存

## 必要なファイル

### 親機（母艦PC）
- `order_counter/app.py` - 日次注文カウンターと配信制御（5000番）
- `predictor/app.py` - 予測ダッシュボード（デフォルト5100番）
- `camera_discovery.py` - カメラ検出
- `config.py` - 設定
- `yolo_processor.py` - YOLO処理
- `templates/index.html` - フロントエンド
- `requirements.txt` - 依存関係

### 子機（Raspberry Pi）
- `camera_server.py` - カメラストリーミングサーバー
- `requirements_child.txt` - 依存関係

## セットアップ

### 親機

```bash
# 依存関係のインストール（推奨: venv）
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

# 日次注文カウンター（5000番）
python order_counter/app.py

# 予測ダッシュボード（5100番。PORT変更は PREDICT_PORT で）
PREDICT_PORT=5100 python predictor/app.py
```

ブラウザで `http://localhost:5000` にアクセスすると日次注文カウンター、`http://localhost:5100` にアクセスすると注文数予測ダッシュボードが表示されます。

### 子機

```bash
# 依存関係のインストール
pip install -r requirements_child.txt

# 起動（カメラID ポート番号）
python camera_server.py 0 5001  # カメラ0（ポート5001）
python camera_server.py 1 5002  # カメラ1（ポート5002）
python camera_server.py 2 5003  # カメラ2（ポート5003）
python camera_server.py 3 5004  # カメラ3（ポート5004）
```

## 依存関係の補足（母艦 / 子機）

簡単に依存関係を分けて記載します。子機（Raspberry Pi）にはカメラ取り込み用の OpenCV が必要です。母艦はネットワーク検出や SocketIO、YOLO 実行のために追加のパッケージが必要になります。

- 母艦（Windows/Linux） - 推奨インストール

```bash
python -m venv .venv
. .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

必須パッケージ（母艦）:
- `Flask`, `flask-cors`, `numpy`, `requests`, `flask-socketio`

YOLO 実行（母艦で行う場合・オプション）:
- `ultralytics`（`torch` が必要です。CPU/GPU によってインストール方法が変わるため、環境に応じた公式手順に従ってください）

- 子機（Raspberry Pi） - 推奨インストール

```bash
# システムパッケージ（Raspbian/Debian 系）
sudo apt update
sudo apt install -y ffmpeg v4l-utils libjpeg-dev libatlas-base-dev

# Python パッケージ
pip install -r requirements_child.txt
```

注意:
- `opencv-python` は Pi 環境でホイールが無い場合、`pip install` が失敗することがあります。その場合は `sudo apt install python3-opencv` を試すか、事前ビルド済みの wheel を利用してください。
- `ultralytics` / `torch` は子機では不要です（YOLO 処理は母艦で実行する設定のため）。

## 環境変数

### 親機

```bash
# 本番環境（子機が5001-5004を使用）
export CAMERA_PORTS="5001,5002,5003,5004"

# 既知の子機IPアドレスを指定（オプション）
export KNOWN_CHILD_IPS="192.168.0.131,192.168.0.132"
```

### 子機

```bash
export CAMERA_ID=0
export CAMERA_PORT=5001  # カメラ0はポート5001
export CAMERA_DEVICE_ID=0  # USBカメラのデバイスID
```

## トラブルシューティング

### カメラが見つからない

1. 子機が起動しているか確認
2. 同じWiFiネットワークに接続されているか確認
3. ファイアウォールでポートが開いているか確認（子機側）
   ```bash
   sudo ufw allow 5001/tcp
   sudo ufw allow 5002/tcp
   sudo ufw allow 5003/tcp
   sudo ufw allow 5004/tcp
   ```

### 接続できない

```bash
# 親機から子機への接続確認
ping 子機のIPアドレス
curl http://子機のIPアドレス:ポート/info
```

### カメラが開けない（子機）

```bash
# USBカメラのデバイスIDを確認
ls -l /dev/video*
```

## データファイル

- `data/detections.jsonl` - リアルタイム検出データ（30分で自動削除）
- `data/detections_minutely.jsonl` - 1分ごとの集計データ

---

# たこ焼き注文数予測システム

## 概要

このシステムは、4つのカメラからの人流検出データと注文データを使用して、**10分後の注文数を予測**する多変量解析システムです。予測結果に基づいて、たこ焼きを焼き始める数を決定できます。

## システムアーキテクチャ

```
┌─────────────────┐
│  カメラシステム  │ → detections_minutely.jsonl (人流データ)
└─────────────────┘
         │
         ▼
┌─────────────────┐
│  注文システム    │ → orders.jsonl (注文データ)
└─────────────────┘
         │
         ├─────────────────┐
         ▼                   ▼
┌─────────────────┐  ┌─────────────────┐
│ train_model.py  │  │  predict_orders.py│
│ (モデル学習)     │  │  (詳細分析)       │
└─────────────────┘  └─────────────────┘
         │
         ▼
┌─────────────────┐
│   model.json    │ (学習済みモデル)
└─────────────────┘
         │
         ▼
┌─────────────────┐
│    app.py       │ ← Flask Webアプリケーション
│  (リアルタイム予測)│
└─────────────────┘
         │
         ├─────────────────┐
         ▼                 ▼
┌─────────────────┐  ┌─────────────────┐
│ templates/      │  │   dummy.py      │
│ index.html      │  │  (テストデータ)  │
│ (Web UI)        │  └─────────────────┘
└─────────────────┘
```

## データ構造とファイル形式

### データファイルの配置

**重要**: すべてのデータファイルは `predictor/data/` フォルダに配置されます（ダッシュボード内で自動参照）。

```
プロジェクトルート/
├── order_counter/
│   ├── app.py
│   └── static/               (日次注文カウンターUI)
├── predictor/
│   ├── app.py
│   ├── dummy.py / train_model.py / predict_orders.py / predict_realtime.py
│   ├── templates/
│   ├── static/               (ダッシュボード用アイコン)
│   └── data/                 (detections_minutely.jsonl, orders.jsonl, model.json, prediction_results.txt)
└── master_console/ ほか
```

### 1. 人流検出データ (`predictor/data/detections_minutely.jsonl`)

**データソース**: 4つのカメラからのYOLO検出結果（分単位）

**ファイル形式**: JSONL（JSON Lines）形式
- 1行に1つのJSONオブジェクト
- 各行は改行文字（`\n`）で区切られる
- UTF-8エンコーディング
- ファイルへの追記（append）モードでデータを追加

**完全なJSONスキーマ**:
```json
{
  "timestamp": "YYYY-MM-DDTHH:MM:SS",
  "camera_id": 1,
  "right_count": 66,
  "left_count": 5,
  "total_count": 168,
  "unknown_count": 97,
  "unique_detections": 2
}
```

**実際のデータ例**:
```jsonl
{"timestamp": "2025-12-15T14:30:00", "camera_id": 1, "right_count": 66, "left_count": 5, "total_count": 168, "unknown_count": 97, "unique_detections": 2}
{"timestamp": "2025-12-15T14:30:00", "camera_id": 2, "right_count": 19, "left_count": 6, "total_count": 49, "unknown_count": 24, "unique_detections": 3}
{"timestamp": "2025-12-15T14:30:00", "camera_id": 3, "right_count": 8, "left_count": 48, "total_count": 91, "unknown_count": 35, "unique_detections": 3}
{"timestamp": "2025-12-15T14:30:00", "camera_id": 4, "right_count": 9, "left_count": 14, "total_count": 38, "unknown_count": 15, "unique_detections": 1}
{"timestamp": "2025-12-15T14:31:00", "camera_id": 1, "right_count": 49, "left_count": 12, "total_count": 143, "unknown_count": 82, "unique_detections": 3}
```

**データ生成の仕様**:
- **更新頻度**: 1分ごとに4つのカメラ分のデータを追加
- **タイムスタンプ**: 同一時刻に4つのカメラ（camera_id: 1, 2, 3, 4）のデータが記録される
- **データの順序**: タイムスタンプ順に並んでいる必要はないが、最新データはファイル末尾に追加される
- **必須フィールド**: すべてのフィールドが必須
- **データ型**: すべて数値は整数型（JSONのnumber型、小数点なし）

**システムでの読み込み方法**:
```python
import json
import os

detections_path = os.path.join('predictor', 'data', 'detections_minutely.jsonl')
detections = []
with open(detections_path, 'r', encoding='utf-8') as f:
    for line in f:
        if line.strip():
            detections.append(json.loads(line))
```

**予測に使用される特徴量**:
- `right_count` と `left_count` のみが予測モデルに使用される
- 特徴量名: `cam{camera_id}_right`, `cam{camera_id}_left`

### 2. 注文データ (`predictor/data/orders.jsonl`)

**データソース**: 注文管理システムからの注文発生データ

**ファイル形式**: JSONL形式

**完全なJSONスキーマ**:
```json
{
  "timestamp": "YYYY-MM-DDTHH:MM:SS",
  "order_occurred": true,
  "order_count": 8,
  "takoyaki_count": 8,
  "total_price": 550
}
```

**データ生成の仕様**:
- 注文が発生した時点で記録
- `order_count`/`takoyaki_count` は販売されたたこ焼き総個数  
  - 4/6/8/10/14個入りはそのまま個数として加算  
  - たこせんは 2 個、トッピングは 0 個として換算  
  - 上記に当てはまらない場合は `items` 情報や、必要に応じて `total_price`（50円→1個換算。`TAKOYAKI_UNIT_PRICE` で変更可能）から推定する
- `total_price` は分析用に保存（未指定なら `items` の単価×数量から算出）

### 3. 学習済みモデル (`predictor/data/model.json`)

**ファイル形式**: JSON（単一オブジェクト）

**スキーマ**:
```json
{
  "intercept": 6.2958934440467145,
  "coefficients": [...],
  "feature_names": [...],
  "r2": 0.06545704085645765,
  "rmse": 0.971539185383162,
  "results": [...]
}
```

## ファイル構成（予測システム）

- `predictor/train_model.py` - モデル学習
- `predictor/predict_orders.py` - 詳細分析
- `predictor/app.py` - Webアプリ（リアルタイム予測）
- `predictor/predict_realtime.py` - リアルタイム予測モジュール
- `predictor/dummy.py` - ダミーデータ生成
- `predictor/data/` - JSONL/学習済みモデル/レポート置き場
- `predictor/templates/index.html` - Web UI

## インストール

```bash
pip install flask flask-cors numpy
```

仮想環境推奨:

```bash
python -m venv venv
source venv/bin/activate  # Windowsは Scripts\\activate
pip install flask flask-cors numpy
```

## 使い方

1. `python predictor/train_model.py` でモデル学習
2. `python predictor/predict_orders.py` で詳細分析（任意）
3. `PREDICT_PORT=5100 python predictor/app.py` で Flask アプリ起動
4. Web UI (`http://localhost:5100`) からダミーデータ制御や予測確認

ダミーデータ生成:

```bash
python predictor/dummy.py       # デフォルト60秒間隔
python predictor/dummy.py 30    # 30秒間隔
```

## 予測の仕組み

- 最小二乗法の重回帰
- 特徴量: 4カメラ × 2方向 = 8個
- 予測式: `切片 + Σ(係数 × 特徴量)`
- 10分後の注文数を推定

## データ統合のポイント

- `predictor/data/` 配下に `detections_minutely.jsonl` / `orders.jsonl` を設置
- ISO8601のタイムスタンプ (`YYYY-MM-DDTHH:MM:SS`)
- JSONL形式（一行一レコード、UTF-8）
- 既存システムからは append モードで追記

## トラブルシューティング

- `model.json` が無い → `python train_model.py`
- `detections_minutely.jsonl` が空 → ダミーデータ開始 or 既存システムから出力
- 予測が変わらない → データ更新を確認

## 運用

- 学習フェーズ: 定期的に `train_model.py`
- 予測フェーズ: 常に `app.py`
- `dummy.py` はテスト用。実運用では実データに差し替え

## 移植手順

1. リポジトリ一式をコピー
2. 仮想環境を構築して依存をインストール
3. `predictor/data/` 内のデータを移行
4. `python predictor/train_model.py` → `python predictor/app.py`

## 注意事項

- すべてのデータは `predictor/data/` に配置
- モデル性能が低い場合はより多くのデータが必要
- ダミーデータはテスト専用
- カレントディレクトリはプロジェクトルートで実行する
