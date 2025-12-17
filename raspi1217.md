以下は、**次の会話・別GPT・チームメンバーにそのまま渡せる引き継ぎ用まとめ**です。
**Markdown（md）形式／コピペ前提**で書いています。

---

# 📷 Raspberry Pi Zero 2 W 遠隔ライブカメラ構築

引き継ぎまとめ（2025-12-17 時点）

## 1. プロジェクト概要

### 目的

* **Raspberry Pi Zero 2 W + USBカメラ**を使い
  **Wi-Fi経由でPCへライブ映像配信**
* PC側で **YOLOによる画像解析**
* **720p / 約10fps / 1秒程度の遅延OK**
* **複数台運用（まずは2台、将来4台以上）**

---

## 2. 使用機材・環境

### ハードウェア

* Raspberry Pi Zero 2 W
* USBカメラ：**Logicool C270**
* 接続：Wi-Fi（2.4GHz）

### ソフトウェア

* OS：Raspberry Pi OS
* Python 3
* OpenCV
* Flask（MJPEG配信）
* ffmpeg（動作確認用）
* ※ mDNS（hostname.local）利用

### ネットワーク

* PC：Windows（常時稼働）
* RasPi → PC：Wi-Fi経由

---

## 3. デバイス構成・命名

| 項目       | 内容                    |
| -------- | --------------------- |
| ユーザー     | `pi`                  |
| camera 1 | hostname: `camera001` |
| camera 2 | hostname: `camera002` |
| カメラ      | `/dev/video0`（C270）   |

---

## 4. 現在の到達点（重要）

### ✅ 確認済み

* USBカメラ認識（`lsusb`）
* `/dev/video0` から静止画取得成功（ffmpeg）
* Flaskアプリ起動確認
* ポート指定での起動（例：5002）
* 複数 `/dev/video*` が見えるが **実体は video0**

```bash
lsusb
ls /dev/video*
```

---

## 5. カメラ動作確認コマンド

### USBカメラ認識確認

```bash
lsusb
```

### 静止画取得テスト（重要）

```bash
ffmpeg -f v4l2 -i /dev/video0 -frames:v 1 test.jpg
ls -l test.jpg
```

→ 画像が生成されれば **カメラ自体は正常**

---

## 6. Flask カメラサーバー起動方法（再起動手順）

### 基本形

```bash
python3 camera_server.py <camera_id> <port>
```

### 例（camera002 / ポート5002）

```bash
python3 camera_server.py 0 5002
```

起動ログ例：

```
============================================================
カメラサーバーを起動します...
カメラID: 0
ポート: 5002
カメラデバイスID: 0
```

### ブラウザ確認（PC側）

```
http://camera002.local:5002
```

または

```
http://<IPアドレス>:5002
```

---

## 7. IPアドレス・ポート確認方法

### Raspberry Pi の IP確認

```bash
ip a
```

または

```bash
hostname -I
```

### ホスト名確認

```bash
hostname
```

### ポート使用状況確認

```bash
ss -tuln
```

### 特定ポートを使っているプロセス確認

```bash
sudo lsof -i :5002
```

---

## 8. カメラサーバーを止める方法

### フォアグラウンド実行時

```bash
Ctrl + C
```

### バックグラウンドで動いている場合

```bash
ps aux | grep camera_server.py
kill <PID>
```

強制終了：

```bash
kill -9 <PID>
```

---

## 9. Raspberry Pi の電源OFF方法（安全）

### 推奨（必ずこれ）

```bash
sudo shutdown -h now
```

### 再起動

```bash
sudo reboot
```

※ LED消灯後に電源を抜く

---

## 10. よくある注意点・トラブル

### `/dev/video*` が複数ある

* **実カメラは v4l2-ctl で確認**

```bash
v4l2-ctl --list-devices
```

### ポートが開かない

* すでに他プロセスが使用中の可能性
* `lsof -i` で確認
* Flaskは **0.0.0.0 バインド必須**

```python
app.run(host="0.0.0.0", port=5002)
```

---

## 11. 露出制御（補足）

* `v4l2-ctl` を用いた露出制御は **Zero 2 W でも動作可能**
* 通信量：**影響ほぼなし**
* CPU負荷：設定変更時のみ微増
* 常時変更しなければ問題なし

---

## 12. 今後の予定・未完了

* [ ] カメラ台数増加（camera003, camera004）
* [ ] systemd サービス化（自動起動）
* [ ] RTSP or HLS 方式の検討
* [ ] PC側 YOLO パイプライン統合
* [ ] 帯域・FPSの最適化

---

## 13. このまとめの使い方

* **新しいChatGPT / VSCode Copilot / GPT-5.x にそのまま貼り付け可**
* 「このmdを前提に続きを進めたい」でOK
* 初心者・チームメンバー引き継ぎ対応済み

---

必要であれば、

* **systemd自動起動化**
* **複数台管理設計**
* **YOLO側の受信コード**
* **RTSP移行判断**

も整理して続けられます。
