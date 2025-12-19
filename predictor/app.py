from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify, render_template

from dummy import DummyDataGenerator
from predict_realtime import (
    compute_busy_level,
    describe_influences,
    influences_to_reasons,
    load_latest_features,
    load_model,
    load_prediction_results_text,
    predict_from_features,
    recent_orders,
)

BASE_DIR = Path(__file__).resolve().parent
app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
dummy_generator = DummyDataGenerator()
PREDICT_PORT = int(os.environ.get("PREDICT_PORT", "5100"))


@app.route("/")
def index():
    return render_template("index.html")


@app.get("/api/predict")
def api_predict():
    model = load_model()
    if not model:
        return jsonify({"ok": False, "error": "model.json が見つかりません。train_model.py を実行してください。"}), 404

    snapshot = load_latest_features()
    if snapshot is None:
        return jsonify({"ok": False, "error": "最新の検出データがありません。dummy.py を実行してデータを生成してください。"}), 404

    timestamp, features = snapshot
    prediction = predict_from_features(model, features)
    influences = describe_influences(model, features)
    busy_level = compute_busy_level(prediction)

    response = {
        "ok": True,
        "timestamp": timestamp,
        "prediction": prediction,
        "busy_level": busy_level,
        "features": features,
        "influences": influences,
        "reasons": influences_to_reasons(influences),
        "model": {
            "r2": model.get("r2"),
            "rmse": model.get("rmse"),
            "trained_samples": model.get("trained_samples"),
            "trained_at": model.get("trained_at"),
        },
        "recent_orders": recent_orders(),
        "report_preview": load_prediction_results_text(),
    }
    return jsonify(response)


@app.get("/api/dummy/status")
def dummy_status():
    return jsonify({"ok": True, "running": dummy_generator.is_running(), "interval": dummy_generator.interval_seconds})


@app.post("/api/dummy/start")
def dummy_start():
    dummy_generator.start()
    return jsonify({"ok": True, "running": True})


@app.post("/api/dummy/stop")
def dummy_stop():
    dummy_generator.stop()
    return jsonify({"ok": True, "running": False})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PREDICT_PORT, debug=False)
