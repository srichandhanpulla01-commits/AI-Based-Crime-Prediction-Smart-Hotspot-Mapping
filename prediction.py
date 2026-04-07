import math

import pandas as pd
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor

from utils import (
    TIME_SLOT_ORDER,
    clean_data,
    cluster_crimes,
    crime_profile,
    current_date_string,
    default_time_slot,
    generate_area_predictions,
    get_patrol_filtered_data,
    risk_band_from_score,
)


MIN_TRAINING_WINDOWS = 10


def _time_slot_index(slot):
    try:
        return TIME_SLOT_ORDER.index(str(slot).strip().lower())
    except ValueError:
        return 0


def _safe_int(value, fallback=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _build_training_frame(data):
    working = clean_data(data)
    if working.empty:
        return pd.DataFrame()

    clustered, cluster_count = cluster_crimes(working, max_clusters=5)
    if clustered.empty or cluster_count == 0:
        return pd.DataFrame()

    clustered = clustered.copy()
    clustered["weekday"] = clustered["incident_dt"].dt.dayofweek.fillna(-1).astype(int)
    clustered["month"] = clustered["incident_dt"].dt.month.fillna(0).astype(int)
    clustered["time_slot_index"] = clustered["time"].map(_time_slot_index).astype(int)
    clustered["severity"] = clustered["crime_type"].map(lambda value: crime_profile(value)["severity"])
    clustered["is_critical"] = (clustered["severity"] >= 2.1).astype(int)

    grouped = (
        clustered.groupby(["cluster", "weekday", "month", "time_slot_index"], dropna=False)
        .agg(
            incident_count=("crime_type", "size"),
            avg_severity=("severity", "mean"),
            critical_count=("is_critical", "sum"),
            latitude=("latitude", "mean"),
            longitude=("longitude", "mean"),
            dominant_crime=("crime_type", lambda values: values.mode().iloc[0] if not values.mode().empty else values.iloc[0]),
            location_name=("location_name", lambda values: values.mode().iloc[0] if not values.mode().empty else values.iloc[0]),
        )
        .reset_index()
    )

    cluster_sizes = clustered.groupby("cluster").size().rename("cluster_history")
    grouped = grouped.merge(cluster_sizes, on="cluster", how="left")
    grouped["incident_count"] = grouped["incident_count"].astype(int)
    grouped["critical_count"] = grouped["critical_count"].astype(int)
    grouped["cluster_history"] = grouped["cluster_history"].fillna(0).astype(int)
    grouped["avg_severity"] = grouped["avg_severity"].round(3)
    return grouped


def _build_candidate_rows(training_frame, route_date, route_time):
    selected_dt = pd.to_datetime(route_date, errors="coerce")
    weekday = _safe_int(selected_dt.dayofweek if pd.notna(selected_dt) else -1, -1)
    month = _safe_int(selected_dt.month if pd.notna(selected_dt) else 0, 0)
    time_slot_index = _time_slot_index(route_time)

    clusters = training_frame["cluster"].drop_duplicates().tolist()
    rows = []
    for cluster_id in clusters:
        cluster_history = training_frame.loc[training_frame["cluster"] == cluster_id, "cluster_history"].max()
        cluster_rows = training_frame[training_frame["cluster"] == cluster_id]
        rows.append(
            {
                "cluster": int(cluster_id),
                "weekday": weekday,
                "month": month,
                "time_slot_index": time_slot_index,
                "cluster_history": int(cluster_history),
                "latitude": round(cluster_rows["latitude"].mean(), 6),
                "longitude": round(cluster_rows["longitude"].mean(), 6),
                "avg_severity": round(cluster_rows["avg_severity"].mean(), 2),
                "critical_count": int(round(cluster_rows["critical_count"].mean())),
                "location_name": cluster_rows["location_name"].mode().iloc[0] if not cluster_rows["location_name"].mode().empty else "Unknown area",
            }
        )
    return pd.DataFrame(rows)


def _fallback_prediction(data, route_date, route_time):
    areas = generate_area_predictions(data, route_date, route_time, radius_m=100, top_n=8)
    if not areas:
        return {
            "headline": "AI prediction is waiting for more FIR history before estimating the next hotspot.",
            "details": "Once the dataset grows, the system will combine date, time, cluster density, and crime severity to estimate the next high-risk window.",
            "prediction": None,
            "area_predictions": [],
        }

    top_area = areas[0]
    confidence = min(86, 48 + top_area["incident_count"] * 4)
    prediction = {
        "cluster": 1,
        "latitude": top_area["latitude"],
        "longitude": top_area["longitude"],
        "crime_type": top_area["crime_type"],
        "peak_time": str(route_time).title(),
        "risk_score": round(top_area["risk_score"], 1),
        "risk_band": top_area["risk_band"],
        "confidence": round(confidence, 1),
        "expected_cases": int(top_area["incident_count"]),
        "route_date": route_date,
        "route_time": route_time,
        "drivers": [
            "Fallback AI mode is using 100-meter hotspot density because the historical training windows are still limited.",
            f"{top_area['incident_count']} nearby FIR records were detected within the strongest 100-meter prediction zone.",
            f"Dominant expected crime pattern in that zone is {top_area['crime_type']}.",
            f"Risk band is {top_area['risk_band']} with a weighted score of {top_area['risk_score']}.",
        ],
        "model_name": "AI Hotspot Density Estimator",
    }
    return {
        "headline": f"AI estimate points to {top_area['location_name']} as the next likely hotspot for the selected patrol window.",
        "details": "This prediction is currently based on clustered FIR density and severity because there are not yet enough historical windows for a full ensemble model.",
        "prediction": prediction,
        "area_predictions": areas,
    }


def generate_prediction_summary(data, route_date=None, route_time=None):
    route_date = route_date or current_date_string()
    route_time = route_time or default_time_slot()

    filtered = get_patrol_filtered_data(data, route_date, route_time)
    training_frame = _build_training_frame(data)
    area_predictions = generate_area_predictions(data, route_date, route_time, radius_m=100, top_n=8)

    if training_frame.empty or len(training_frame) < MIN_TRAINING_WINDOWS:
        return _fallback_prediction(filtered if not filtered.empty else data, route_date, route_time)

    feature_columns = ["cluster", "weekday", "month", "time_slot_index", "cluster_history", "avg_severity", "critical_count"]
    X = training_frame[feature_columns]
    y_count = training_frame["incident_count"]
    y_crime = training_frame["dominant_crime"]

    count_model = RandomForestRegressor(
        n_estimators=180,
        max_depth=8,
        min_samples_leaf=1,
        random_state=42,
    )
    crime_model = RandomForestClassifier(
        n_estimators=180,
        max_depth=8,
        min_samples_leaf=1,
        random_state=42,
    )
    count_model.fit(X, y_count)
    crime_model.fit(X, y_crime)

    candidate_rows = _build_candidate_rows(training_frame, route_date, route_time)
    if candidate_rows.empty:
        return _fallback_prediction(filtered if not filtered.empty else data, route_date, route_time)

    candidate_features = candidate_rows[feature_columns]
    predicted_counts = count_model.predict(candidate_features)
    predicted_crimes = crime_model.predict(candidate_features)
    crime_probabilities = crime_model.predict_proba(candidate_features)

    candidate_rows = candidate_rows.copy()
    candidate_rows["predicted_cases"] = predicted_counts
    candidate_rows["predicted_crime"] = predicted_crimes
    candidate_rows["confidence"] = [float(prob.max()) for prob in crime_probabilities]

    candidate_rows["risk_score"] = (
        candidate_rows["predicted_cases"].clip(lower=0) * 16
        + candidate_rows["avg_severity"] * 22
        + candidate_rows["critical_count"] * 6
    )
    candidate_rows["risk_band"] = candidate_rows["risk_score"].map(risk_band_from_score)
    candidate_rows = candidate_rows.sort_values(["risk_score", "predicted_cases", "confidence"], ascending=False).reset_index(drop=True)

    top = candidate_rows.iloc[0]
    expected_cases = max(1, int(round(top["predicted_cases"])))
    confidence = min(98.0, max(52.0, round(top["confidence"] * 100, 1)))
    risk_score = round(float(top["risk_score"]), 1)

    feature_importance_pairs = sorted(
        zip(feature_columns, count_model.feature_importances_),
        key=lambda item: item[1],
        reverse=True,
    )
    label_map = {
        "cluster": "historical hotspot cluster identity",
        "weekday": "matching weekday crime pattern",
        "month": "seasonal month pattern",
        "time_slot_index": "selected patrol time slot",
        "cluster_history": "historical FIR volume in the cluster",
        "avg_severity": "average severity of crimes in the cluster",
        "critical_count": "critical-crime concentration in the cluster",
    }
    top_drivers = [label_map.get(name, name) for name, weight in feature_importance_pairs[:3] if weight > 0]

    prediction = {
        "cluster": int(top["cluster"]),
        "latitude": round(float(top["latitude"]), 6),
        "longitude": round(float(top["longitude"]), 6),
        "crime_type": str(top["predicted_crime"]).title(),
        "peak_time": str(route_time).title(),
        "risk_score": risk_score,
        "risk_band": risk_band_from_score(risk_score),
        "confidence": confidence,
        "expected_cases": expected_cases,
        "route_date": route_date,
        "route_time": route_time,
        "drivers": [
            f"AI model: Random Forest trained on {len(training_frame)} historical FIR cluster windows.",
            f"The strongest next hotspot is cluster {int(top['cluster'])} around {top['location_name']}.",
            f"Expected case volume for the selected window is {expected_cases}, with predicted dominant crime {str(top['predicted_crime']).title()}.",
            f"Main pattern signals: {', '.join(top_drivers)}.",
        ],
        "model_name": "Random Forest Patrol Predictor",
    }

    headline = (
        f"AI prediction indicates {top['location_name']} as the most likely hotspot for "
        f"{route_date} during {str(route_time).title()}."
    )
    details = (
        f"The model expects about {expected_cases} case(s) in this cluster, with "
        f"{str(top['predicted_crime']).title()} emerging as the leading risk pattern."
    )

    enriched_areas = []
    for area in area_predictions:
        area_copy = dict(area)
        distance_to_top = math.hypot(area_copy["latitude"] - prediction["latitude"], area_copy["longitude"] - prediction["longitude"])
        area_copy["ai_priority"] = "Primary" if distance_to_top < 0.0025 else "Monitor"
        enriched_areas.append(area_copy)

    return {
        "headline": headline,
        "details": details,
        "prediction": prediction,
        "area_predictions": enriched_areas,
    }
