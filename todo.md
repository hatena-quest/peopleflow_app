# TODO (peopleflow_app) — 優先度順

このファイルは初心者向けの実行可能な TODO リストです。まずはカメラ実機以外でできる作業を中心に整理しています。

## 優先 (今すぐできる)
- [ ] 依存チェック: `requirements.txt` と `requirements_child.txt` を確認し、重複や不足を報告する。
- [ ] README に「子機デプロイ手順（systemd 例）」を追記する（初心者向けコマンド付き）。
- [OK] `child_camera_server.py` をリポジトリに追加（`Octpass/YOLO_tako/camera_server.py` のコピー、別名で保存）。

 - [OK] `raspi1217.md` の内容を最終版に整形（必要なら追記・修正）。

## 中優先（Pi 実機が戻ってきたら）
- [ ] Pi でカメラ接続確認手順の実行（`lsusb`, `v4l2-ctl`, `ffmpeg`）。
- [ ] systemd ユニットを配置し自動起動を確認する（`camera_server.service` の例を README に）。
- [ ] `master_console` を起動して `camera_discovery` で検出されるか確認。
- [ ] YOLO 処理が `detections_minutely.jsonl` に出力されるか確認。

## 低優先（運用改善）
- [ ] `run_all` の起動スクリプト（親機向け）を追加（Windows 用 `.ps1` と Unix 用 `.sh`）。
- [ ] テスト用ダミーフレーム生成スクリプトを整備し、`master_console` の流れを検証。
- [ ] systemd / デプロイ手順の自動化（簡易デプロイスクリプト）。
- [ ] ドキュメント整備: トラブルシュート FAQ、ログ確認コマンド集。

## 備考 / チェックポイント
- カメラのデフォルト解像度・FPS (`camera_server.py` は 640x480/8fps) 
- 物理カメラがない環境でもできる作業（README整備、依存確認、スクリプト作成）を優先してください。

---

作業が進んだらこの `todo.md` を更新してチェックを付けてください。