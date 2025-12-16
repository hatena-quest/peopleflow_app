from __future__ import annotations

from pathlib import Path
from typing import List

from predict_realtime import RESULTS_FILE
from train_model import train


def _format_table(model: dict) -> List[str]:
    lines = ["feature,coefficient,t_statistic,p_value"]
    for row in model.get("results", []):
        lines.append(
            f"{row['feature']},{row['coefficient']:.6f},{row['t_statistic']:.4f},{row['p_value']:.4f}"
        )
    return lines


def _matching_rate(actual: List[float], predicted: List[float], tolerance: float = 1.0) -> float:
    if not actual:
        return 0.0
    matched = sum(1 for a, p in zip(actual, predicted) if abs(a - p) <= tolerance)
    return matched / len(actual)


def generate_report() -> str:
    model, artifacts = train(save=True)
    actual = artifacts["actual"]
    predicted = artifacts["predicted"]
    match_rate = _matching_rate(actual, predicted)

    lines: List[str] = []
    lines.append("=== たこ焼き注文数予測 詳細分析 ===")
    lines.append(f"学習サンプル数: {model['trained_samples']}")
    lines.append(f"決定係数 R^2: {model['r2']:.4f}")
    lines.append(f"RMSE: {model['rmse']:.4f}")
    lines.append(f"±1個以内で一致した割合: {match_rate * 100:.1f}%")
    lines.append("")
    lines.append("特徴量ごとの係数一覧 (CSV形式)")
    lines.extend(_format_table(model))
    lines.append("")
    lines.append("予測と実測の比較")
    for timestamp, actual_value, predicted_value in zip(
        artifacts["timestamps"], actual, predicted
    ):
        lines.append(f"{timestamp} -> 実測:{actual_value:.2f} / 予測:{predicted_value:.2f}")

    report_text = "\n".join(lines)
    Path(RESULTS_FILE).write_text(report_text, encoding="utf-8")
    return report_text


if __name__ == "__main__":
    text = generate_report()
    print(text)
