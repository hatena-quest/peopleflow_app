from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import numpy as np

from predict_realtime import FEATURE_NAMES, build_dataset_records, save_model


def _approx_two_tailed_p_value(t_stat: float) -> float:
    """正規近似による両側検定のp値"""
    z = abs(t_stat)
    # survival function for standard normal
    tail = 0.5 * math.erfc(z / math.sqrt(2))
    return min(1.0, max(0.0, 2 * tail))


def _build_matrices() -> Tuple[np.ndarray, np.ndarray, List[str], List[str]]:
    dataset = build_dataset_records()
    if not dataset:
        raise RuntimeError("学習用のデータが不足しています。detections_minutely.jsonl と orders.jsonl を確認してください。")

    features: List[List[float]] = []
    targets: List[float] = []
    timestamps: List[str] = []
    order_times: List[str] = []
    for base_time, feature_values, order_count, order_time in dataset:
        features.append([float(feature_values.get(name, 0)) for name in FEATURE_NAMES])
        targets.append(float(order_count))
        timestamps.append(base_time.isoformat())
        order_times.append(order_time.isoformat())

    X = np.asarray(features, dtype=float)
    y = np.asarray(targets, dtype=float)
    return X, y, timestamps, order_times


def train(save: bool = True) -> Tuple[Dict, Dict]:
    X, y, timestamps, order_times = _build_matrices()
    n_samples, n_features = X.shape

    X_design = np.hstack([np.ones((n_samples, 1)), X])
    xtx = X_design.T @ X_design
    xtx_inv = np.linalg.pinv(xtx)
    beta = xtx_inv @ X_design.T @ y
    predictions = X_design @ beta
    residuals = y - predictions

    dof = max(int(n_samples - len(beta)), 1)
    ss_res = float(residuals @ residuals)
    ss_tot = float(((y - y.mean()) ** 2).sum()) if n_samples > 1 else 0.0
    r_squared = 0.0 if ss_tot == 0 else max(0.0, 1 - ss_res / ss_tot)
    rmse = math.sqrt(ss_res / n_samples)

    sigma_sq = ss_res / dof if dof > 0 else 0.0
    covariance = sigma_sq * xtx_inv
    std_errors = np.sqrt(np.maximum(np.diag(covariance), 1e-12))

    t_stats = [float(b / se) if se else 0.0 for b, se in zip(beta, std_errors)]
    p_values = [_approx_two_tailed_p_value(t) for t in t_stats]

    intercept = float(beta[0])
    coefficients = [float(value) for value in beta[1:]]

    results_table = []
    results_table.append(
        {
            "feature": "intercept",
            "coefficient": intercept,
            "t_statistic": t_stats[0],
            "p_value": p_values[0],
        }
    )
    for name, coef, t_stat, p_val in zip(FEATURE_NAMES, coefficients, t_stats[1:], p_values[1:]):
        results_table.append(
            {
                "feature": name,
                "coefficient": coef,
                "t_statistic": t_stat,
                "p_value": p_val,
            }
        )

    model_dict = {
        "intercept": intercept,
        "coefficients": coefficients,
        "feature_names": FEATURE_NAMES,
        "r2": r_squared,
        "rmse": rmse,
        "trained_samples": n_samples,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "results": results_table,
    }

    artifacts = {
        "timestamps": timestamps,
        "order_timestamps": order_times,
        "actual": y.tolist(),
        "predicted": predictions.tolist(),
        "residuals": residuals.tolist(),
    }

    if save:
        save_model(model_dict)

    return model_dict, artifacts


if __name__ == "__main__":
    model, _details = train(save=True)
    print("=== モデル学習が完了しました ===")
    print(f"サンプル数: {model['trained_samples']}")
    print(f"決定係数 R^2: {model['r2']:.4f}")
    print(f"RMSE: {model['rmse']:.4f}")
