from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DETECTIONS_FILE = DATA_DIR / "detections_minutely.jsonl"
ORDERS_FILE = DATA_DIR / "orders.jsonl"
MODEL_FILE = DATA_DIR / "model.json"
RESULTS_FILE = DATA_DIR / "prediction_results.txt"

CAMERA_IDS = [1, 2, 3, 4]
FEATURE_NAMES = [
    f"cam{camera_id}_{direction}"
    for camera_id in CAMERA_IDS
    for direction in ("left", "right")
]
TAKOYAKI_UNIT_PRICE = int(os.environ.get("TAKOYAKI_UNIT_PRICE", "50"))


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip().lower()


TAKOYAKI_MENU_COUNTS = {
    "four": 4,
    "six": 6,
    "eight": 8,
    "ten": 10,
    "fourteen": 14,
    "takosen": 2,
    "topping": 0,
}
TAKOYAKI_NAME_COUNTS = {
    _normalize_text("4"): 4,
    _normalize_text("６"): 6,
    _normalize_text("8"): 8,
    _normalize_text("８"): 8,
    _normalize_text("10"): 10,
    _normalize_text("１４"): 14,
    _normalize_text("four"): 4,
    _normalize_text("six"): 6,
    _normalize_text("eight"): 8,
    _normalize_text("ten"): 10,
    _normalize_text("fourteen"): 14,
    _normalize_text("たこせん"): 2,
    _normalize_text("タコセン"): 2,
    _normalize_text("takosen"): 2,
    _normalize_text("tako sen"): 2,
    _normalize_text("トッピング"): 0,
    _normalize_text("topping"): 0,
}


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _read_jsonl(path: Path) -> List[Dict]:
    if not path.exists():
        return []
    rows: List[Dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
    return rows


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def load_detections() -> List[Dict]:
    return _read_jsonl(DETECTIONS_FILE)


def load_orders() -> List[Dict]:
    return _read_jsonl(ORDERS_FILE)


def _empty_feature_template() -> Dict[str, int]:
    return {name: 0 for name in FEATURE_NAMES}


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _lookup_item_units(item: Dict) -> Optional[int]:
    if not isinstance(item, dict):
        return None
    menu_id = item.get("menuId") or item.get("menu_id")
    if isinstance(menu_id, str):
        menu_key = _normalize_text(menu_id)
        if menu_key in TAKOYAKI_MENU_COUNTS:
            return TAKOYAKI_MENU_COUNTS[menu_key]
    name = item.get("name")
    if isinstance(name, str):
        normalized = _normalize_text(name)
        if normalized in TAKOYAKI_NAME_COUNTS:
            return TAKOYAKI_NAME_COUNTS[normalized]
        digits = "".join(
            ch for ch in unicodedata.normalize("NFKC", name) if ch.isdigit()
        )
        if digits:
            try:
                return int(digits)
            except ValueError:
                pass
    return None


def _takoyaki_units_from_items(items) -> Optional[int]:
    if not isinstance(items, list) or not items:
        return None
    total = 0
    matched = False
    for item in items:
        units = _lookup_item_units(item)
        if units is None:
            continue
        qty = max(0, _safe_int(item.get("quantity"), default=1))
        total += units * qty
        matched = True
    return total if matched else None


def _fallback_quantity_total(items) -> Optional[int]:
    if not isinstance(items, list) or not items:
        return None
    subtotal = 0
    for item in items:
        subtotal += max(0, _safe_int(item.get("quantity"), default=1))
    return subtotal


def _extract_total_price(payload: Dict) -> Optional[float]:
    for key in ("total_price", "total", "amount", "price"):
        raw = payload.get(key)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    items = payload.get("items")
    if isinstance(items, list):
        subtotal = 0.0
        has_value = False
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                price_value = float(item.get("price", 0))
                qty_value = int(item.get("quantity", 1))
            except (TypeError, ValueError):
                continue
            if price_value <= 0 or qty_value <= 0:
                continue
            subtotal += price_value * qty_value
            has_value = True
        if has_value:
            return subtotal
    return None


def _price_to_takoyaki_count(total_price: Optional[float]) -> Optional[int]:
    if total_price is None or total_price <= 0:
        return None
    return max(1, int(round(total_price / TAKOYAKI_UNIT_PRICE)))


def build_feature_map(detections: Iterable[Dict]) -> Dict[str, Dict[str, int]]:
    feature_map: Dict[str, Dict[str, int]] = {}
    for row in detections:
        timestamp = row.get("timestamp")
        camera_id = row.get("camera_id")
        if timestamp is None or camera_id is None:
            continue
        camera_id = int(camera_id)
        if camera_id not in CAMERA_IDS:
            continue
        if timestamp not in feature_map:
            feature_map[timestamp] = _empty_feature_template()
        feature_map[timestamp][f"cam{camera_id}_left"] = int(row.get("left_count", 0))
        feature_map[timestamp][f"cam{camera_id}_right"] = int(row.get("right_count", 0))
    return feature_map


def _find_order_for_target(
    orders: List[Tuple[datetime, int]], target_time: datetime, tolerance: timedelta
) -> Optional[Tuple[datetime, int]]:
    best: Optional[Tuple[datetime, int]] = None
    best_diff = tolerance + timedelta(seconds=1)
    for order_time, order_count in orders:
        diff = abs(order_time - target_time)
        if diff <= tolerance and diff < best_diff:
            best = (order_time, order_count)
            best_diff = diff
    return best


def _order_target_value(row: Dict) -> int:
    takoyaki_from_items = _takoyaki_units_from_items(row.get("items"))
    if takoyaki_from_items is not None:
        return takoyaki_from_items
    try:
        explicit = row.get("takoyaki_count")
        if explicit is not None:
            return max(0, int(explicit))
    except (TypeError, ValueError):
        pass
    try:
        return max(0, int(row.get("order_count", 0)))
    except (TypeError, ValueError):
        pass
    price_based = _price_to_takoyaki_count(_extract_total_price(row))
    if price_based is not None:
        return price_based
    fallback_qty = _fallback_quantity_total(row.get("items"))
    return fallback_qty if fallback_qty is not None else 0


def build_dataset_records(
    horizon_minutes: int = 10,
    tolerance_minutes: int = 5,
) -> List[Tuple[datetime, Dict[str, int], int, datetime]]:
    detections = load_detections()
    orders_raw = load_orders()
    if not detections or not orders_raw:
        return []

    feature_map = build_feature_map(detections)
    order_series: List[Tuple[datetime, int]] = []
    for order in orders_raw:
        timestamp_value = order.get("timestamp")
        if not timestamp_value:
            continue
        target_value = _order_target_value(order)
        order_series.append((_parse_timestamp(timestamp_value), target_value))
    order_series.sort(key=lambda item: item[0])

    horizon = timedelta(minutes=horizon_minutes)
    tolerance = timedelta(minutes=tolerance_minutes)
    dataset: List[Tuple[datetime, Dict[str, int], int, datetime]] = []

    for timestamp_str, feature_values in feature_map.items():
        base_time = _parse_timestamp(timestamp_str)
        target = base_time + horizon
        match = _find_order_for_target(order_series, target, tolerance)
        if match is None:
            continue
        dataset.append((base_time, feature_values, match[1], match[0]))

    dataset.sort(key=lambda record: record[0])
    return dataset


def load_model() -> Optional[Dict]:
    if not MODEL_FILE.exists():
        return None
    with MODEL_FILE.open("r", encoding="utf-8") as handle:
        try:
            return json.load(handle)
        except json.JSONDecodeError:
            return None


def save_model(model_dict: Dict) -> None:
    _ensure_data_dir()
    with MODEL_FILE.open("w", encoding="utf-8") as handle:
        json.dump(model_dict, handle, ensure_ascii=False, indent=2)


def load_latest_features() -> Optional[Tuple[str, Dict[str, int]]]:
    detections = load_detections()
    if not detections:
        return None
    feature_map = build_feature_map(detections)
    if not feature_map:
        return None
    latest_ts = max(feature_map.keys())
    return latest_ts, feature_map[latest_ts]


def predict_from_features(model: Dict, features: Dict[str, int]) -> float:
    prediction = float(model.get("intercept", 0.0))
    for name, coef in zip(model.get("feature_names", []), model.get("coefficients", [])):
        prediction += float(coef) * float(features.get(name, 0))
    return max(0.0, prediction)


def describe_influences(model: Dict, features: Dict[str, int]) -> List[Dict]:
    influences: List[Dict] = []
    names = model.get("feature_names", [])
    coefs = model.get("coefficients", [])
    for name, coef in zip(names, coefs):
        value = float(features.get(name, 0))
        contribution = value * float(coef)
        influences.append(
            {
                "feature": name,
                "value": value,
                "coefficient": float(coef),
                "contribution": contribution,
            }
        )
    influences.sort(key=lambda item: abs(item["contribution"]), reverse=True)
    return influences


def _feature_labels(feature_name: str) -> Tuple[str, str]:
    """
    Convert a feature name like 'cam1_left' into UI-friendly camera/direction labels.
    """
    match = re.match(r"cam(\d+)_(left|right)$", feature_name or "")
    if not match:
        safe_name = feature_name.strip() if feature_name else "feature"
        return safe_name, ""

    camera_id, direction = match.groups()
    camera_label = f"カメラ{camera_id}"
    direction_label = "左方向" if direction == "left" else "右方向"
    return camera_label, direction_label


def influences_to_reasons(influences: List[Dict]) -> List[Dict]:
    """
    Build the frontend-friendly "reasons" payload from coefficient contributions.
    """
    reasons: List[Dict] = []
    for item in influences:
        feature_name = str(item.get("feature", "") or "")
        camera_label, direction_label = _feature_labels(feature_name)
        impact_value = float(item.get("contribution", 0.0))
        count_value = float(item.get("value", 0.0))
        reasons.append(
            {
                "camera": camera_label,
                "direction": direction_label,
                "impact": impact_value,
                "count": count_value,
            }
        )
    return reasons


def compute_busy_level(prediction: float) -> Dict[str, str]:
    if prediction < 2:
        return {"label": "余裕あり", "emoji": "😌"}
    if prediction < 4:
        return {"label": "やや忙しい", "emoji": "🙂"}
    if prediction < 6:
        return {"label": "混雑気味", "emoji": "😅"}
    if prediction < 8:
        return {"label": "かなり忙しい", "emoji": "😖"}
    return {"label": "ピーク注意", "emoji": "🔥"}


def load_prediction_results_text() -> str:
    if not RESULTS_FILE.exists():
        return ""
    return RESULTS_FILE.read_text(encoding="utf-8")


def recent_orders(limit: int = 10) -> List[Dict]:
    rows = load_orders()
    rows.sort(key=lambda row: row.get("timestamp", ""))
    selected = rows[-limit:]
    enriched: List[Dict] = []
    for row in selected:
        item = dict(row)
        item["takoyaki_count"] = _order_target_value(item)
        enriched.append(item)
    return enriched
