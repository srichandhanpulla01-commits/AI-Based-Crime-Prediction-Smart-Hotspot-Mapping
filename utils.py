import json
import base64
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen

import folium
import pandas as pd
from folium import Element
from folium.plugins import Fullscreen, HeatMap, MiniMap
from sklearn.cluster import KMeans

from routes import generate_patrol_route, get_high_risk_zones


BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "fir_data.csv"
CCTNS_DATA_FILE = BASE_DIR / "cctns_fir_data.csv"
MAP_FILE = BASE_DIR / "static" / "crime_map.html"
HEAT_MAP_FILE = BASE_DIR / "static" / "crime_heat_map.html"
ROUTE_MAP_FILE = BASE_DIR / "static" / "route_map.html"
ROUTE_PICKER_FILE = BASE_DIR / "static" / "route_picker.html"
GEOCODE_CACHE_FILE = BASE_DIR / "static" / "location_cache.json"

REQUIRED_COLUMNS = [
    "latitude",
    "longitude",
    "crime_type",
    "time",
    "people",
    "section",
    "station_name",
    "fir_number",
    "incident_date",
    "location_name",
    "status",
    "victim_count",
    "suspect_count",
]

COLUMN_ALIASES = {
    "latitude": ["latitude", "lat", "gps latitude", "geo_latitude"],
    "longitude": ["longitude", "lon", "lng", "gps longitude", "geo_longitude"],
    "crime_type": ["crime_type", "crime type", "nature_of_offence", "nature of offence", "offence", "act description"],
    "time": ["time", "incident_time", "occurrence time", "time_of_occurrence", "time of occurrence"],
    "people": ["people", "persons_involved", "complainant/accused", "complainant_accused", "accused details"],
    "section": ["section", "ipc_section", "ipc section", "act_section", "section of law"],
    "station_name": ["station_name", "police_station", "police station", "ps_name", "station"],
    "fir_number": ["fir_number", "fir no", "fir_no", "fir number", "case_id", "case id"],
    "incident_date": ["incident_date", "date", "occurrence_date", "date of occurrence", "incident date"],
    "location_name": ["location_name", "location", "place_of_occurrence", "place of occurrence", "address"],
    "status": ["status", "case_status", "fir status"],
    "victim_count": ["victim_count", "victims", "victim count"],
    "suspect_count": ["suspect_count", "suspects", "suspect count"],
}

TIME_SLOT_ORDER = ["early morning", "morning", "afternoon", "evening", "night", "late night"]
TIME_SLOT_LABELS = {slot: slot.title() for slot in TIME_SLOT_ORDER}

CRIME_PROFILES = {
    "theft": {"severity": 1.1, "specification": "Property crime. Routine patrol, CCTV verification, repeat-offender watch."},
    "burglary": {"severity": 1.5, "specification": "Night-time perimeter patrol, door-to-door inquiry, evidence preservation."},
    "robbery": {"severity": 1.8, "specification": "Rapid armed response zone, witness tracing, escape-route blocking."},
    "assault": {"severity": 1.7, "specification": "Medical support check, conflict-area surveillance, crowd management."},
    "murder": {"severity": 2.5, "specification": "Critical crime scene control, homicide response, immediate area lockdown."},
    "attempt to murder": {"severity": 2.3, "specification": "Priority response, victim protection, armed suspect lookout."},
    "rape": {"severity": 2.4, "specification": "Sensitive response, survivor safety protocol, urgent forensic handling."},
    "kidnapping": {"severity": 2.2, "specification": "Fast-route interception, transport hub monitoring, high-alert patrols."},
    "harassment": {"severity": 1.6, "specification": "Victim-support patrol, repeated-complaint tracking, hotspot deterrence."},
    "stalking": {"severity": 1.7, "specification": "Victim watch zone, repeat-incident surveillance, suspect movement tracking."},
    "domestic violence": {"severity": 1.8, "specification": "Immediate welfare response, protection order follow-up, high-repeat risk monitoring."},
    "molestation": {"severity": 2.0, "specification": "Sensitive victim response, crowd-space patrol reinforcement, CCTV review."},
    "cybercrime": {"severity": 1.3, "specification": "Digital trace escalation, device seizure workflow, fraud desk coordination."},
    "drug offence": {"severity": 1.9, "specification": "Supply-route monitoring, repeat-location patrols, covert observation."},
    "chain snatching": {"severity": 1.6, "specification": "Mobile patrol near commuter corridors, scooter surveillance, rapid pursuit."},
}

ROUTE_RISK_COLORS = {
    "safe": "#22c55e",
    "watch": "#f59e0b",
    "critical": "#ef4444",
}

WOMEN_RELATED_KEYWORDS = {"harassment", "stalking", "molestation", "domestic violence", "rape"}
CRITICALITY_COLORS = {
    "low": "#22c55e",
    "medium": "#f59e0b",
    "critical": "#991b1b",
}
EARTH_RADIUS_M = 6371000


def ensure_columns(data):
    for column in REQUIRED_COLUMNS:
        if column not in data.columns:
            data[column] = "unknown"
    return data


def normalize_columns(data):
    rename_map = {}
    lowered = {str(column).strip().lower(): column for column in data.columns}
    for target, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                rename_map[lowered[alias]] = target
                break
    return ensure_columns(data.rename(columns=rename_map))


def clean_data(data):
    data = normalize_columns(data.copy())
    data["latitude"] = pd.to_numeric(data["latitude"], errors="coerce")
    data["longitude"] = pd.to_numeric(data["longitude"], errors="coerce")
    data = data.dropna(subset=["latitude", "longitude"])
    data = data[(data["latitude"].between(-90, 90)) & (data["longitude"].between(-180, 180))]

    for column in ["crime_type", "time", "people", "section", "station_name", "fir_number", "incident_date", "location_name", "status"]:
        data[column] = data[column].fillna("unknown").astype(str).str.strip()
        data.loc[data[column] == "", column] = "unknown"

    for column in ["victim_count", "suspect_count"]:
        data[column] = pd.to_numeric(data[column], errors="coerce").fillna(0).astype(int)

    data["time"] = data["time"].str.lower()
    data["incident_dt"] = pd.to_datetime(data["incident_date"], errors="coerce", format="mixed")
    return data.reset_index(drop=True)


def validate_coordinates(lat, lon):
    try:
        lat = float(lat)
        lon = float(lon)
        return -90 <= lat <= 90 and -180 <= lon <= 180
    except (TypeError, ValueError):
        return False


def current_date_string():
    return datetime.now().strftime("%Y-%m-%d")


def default_time_slot():
    hour = datetime.now().hour
    if hour < 6:
        return "early morning"
    if hour < 12:
        return "morning"
    if hour < 16:
        return "afternoon"
    if hour < 20:
        return "evening"
    if hour < 23:
        return "night"
    return "late night"


def get_data_source_path():
    return CCTNS_DATA_FILE if CCTNS_DATA_FILE.exists() else DATA_FILE


def _load_geocode_cache():
    if GEOCODE_CACHE_FILE.exists():
        try:
            return json.loads(GEOCODE_CACHE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_geocode_cache(cache):
    GEOCODE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    GEOCODE_CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _extract_location_name(payload):
    address = payload.get("address", {})
    parts = [
        address.get("suburb"),
        address.get("neighbourhood"),
        address.get("road"),
        address.get("city") or address.get("town") or address.get("village"),
    ]
    cleaned = [part for part in parts if part]
    if cleaned:
        return ", ".join(dict.fromkeys(cleaned))
    return payload.get("display_name", "Location unavailable")


def resolve_location_name(lat, lon):
    cache = _load_geocode_cache()
    key = f"{round(float(lat), 4)},{round(float(lon), 4)}"
    if key in cache:
        return cache[key]

    params = urlencode({"lat": round(float(lat), 6), "lon": round(float(lon), 6), "format": "jsonv2", "addressdetails": 1, "zoom": 17})
    request = Request(
        f"https://nominatim.openstreetmap.org/reverse?{params}",
        headers={"User-Agent": "crime-intelligence-dashboard/1.0"},
    )
    try:
        with urlopen(request, timeout=4) as response:
            location_name = _extract_location_name(json.loads(response.read().decode("utf-8")))
    except (URLError, TimeoutError, ValueError):
        location_name = f"Near {round(float(lat), 4)}, {round(float(lon), 4)}"

    cache[key] = location_name
    _save_geocode_cache(cache)
    return location_name


def geocode_place_name(query):
    if not query or not str(query).strip():
        return None

    raw_query = str(query).strip()
    if "," in raw_query:
        try:
            lat_str, lon_str = [part.strip() for part in raw_query.split(",", 1)]
            lat = float(lat_str)
            lon = float(lon_str)
            if validate_coordinates(lat, lon):
                return {
                    "name": resolve_location_name(lat, lon),
                    "latitude": lat,
                    "longitude": lon,
                }
        except ValueError:
            pass

    cache = _load_geocode_cache()
    key = f"search::{raw_query.lower()}"
    if key in cache:
        return cache[key]

    params = urlencode({"q": raw_query, "format": "jsonv2", "limit": 1})
    request = Request(
        f"https://nominatim.openstreetmap.org/search?{params}",
        headers={"User-Agent": "crime-intelligence-dashboard/1.0"},
    )

    try:
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if not payload:
                return None
            first = payload[0]
            result = {
                "name": first.get("display_name", raw_query),
                "latitude": float(first["lat"]),
                "longitude": float(first["lon"]),
            }
            cache[key] = result
            _save_geocode_cache(cache)
            return result
    except (URLError, TimeoutError, ValueError, KeyError):
        return None


def build_route_picker_map(path=ROUTE_PICKER_FILE):
    picker_map = folium.Map(location=[13.0827, 80.2707], zoom_start=12, tiles=None, control_scale=True)
    folium.TileLayer("OpenStreetMap", name="Street Map").add_to(picker_map)
    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        attr="CartoDB Voyager",
        name="Navigation View",
    ).add_to(picker_map)
    Fullscreen(position="topright").add_to(picker_map)

    map_name = picker_map.get_name()
    script = f"""
    <script>
    document.addEventListener("DOMContentLoaded", function () {{
        var mapRef = {map_name};
        var clickState = "start";
        var startMarker = null;
        var endMarker = null;

        function postLocation(field, lat, lon) {{
            window.parent.postMessage({{
                type: "route-picker",
                field: field,
                value: lat.toFixed(6) + ", " + lon.toFixed(6)
            }}, "*");
        }}

        mapRef.on("click", function (event) {{
            var lat = event.latlng.lat;
            var lon = event.latlng.lng;

            if (clickState === "start") {{
                if (startMarker) mapRef.removeLayer(startMarker);
                startMarker = L.marker([lat, lon], {{ icon: L.icon({{
                    iconUrl: "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-green.png",
                    shadowUrl: "https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png",
                    iconSize: [25, 41],
                    iconAnchor: [12, 41]
                }}) }}).addTo(mapRef).bindPopup("Start Point").openPopup();
                postLocation("start_location", lat, lon);
                clickState = "end";
            }} else {{
                if (endMarker) mapRef.removeLayer(endMarker);
                endMarker = L.marker([lat, lon], {{ icon: L.icon({{
                    iconUrl: "https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-red.png",
                    shadowUrl: "https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png",
                    iconSize: [25, 41],
                    iconAnchor: [12, 41]
                }}) }}).addTo(mapRef).bindPopup("Destination").openPopup();
                postLocation("end_location", lat, lon);
                clickState = "start";
            }}
        }});
    }});
    </script>
    """
    picker_map.get_root().html.add_child(Element(script))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    picker_map.save(path)
    return "static/route_picker.html"


def attach_location_names(data):
    data = clean_data(data)
    missing = data["location_name"].fillna("").eq("unknown") | data["location_name"].fillna("").eq("")
    if missing.any():
        for index in data[missing].index:
            row = data.loc[index]
            data.at[index, "location_name"] = resolve_location_name(row["latitude"], row["longitude"])
    return data


def load_fir_data(path=None):
    selected_path = get_data_source_path() if path is None else Path(path)
    if not selected_path.exists():
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    return attach_location_names(pd.read_csv(selected_path, on_bad_lines="skip"))


def save_fir_data(data, path=DATA_FILE):
    attach_location_names(data).drop(columns=["incident_dt"], errors="ignore").to_csv(path, index=False)


def build_fir_number(data):
    prefix = f"CCTNS-{datetime.now().strftime('%Y')}-"
    existing = data["fir_number"].astype(str).str.extract(r"(\d+)$", expand=False)
    next_id = pd.to_numeric(existing, errors="coerce").fillna(0).max() + 1
    return f"{prefix}{int(next_id):04d}"


def add_fir_record(record, path=DATA_FILE):
    data = load_fir_data(path)
    record.setdefault("fir_number", build_fir_number(data))
    record.setdefault("station_name", "unknown")
    record.setdefault("status", "Registered")
    record.setdefault("victim_count", 1)
    record.setdefault("suspect_count", 1)
    record.setdefault("location_name", "unknown")

    new_row = pd.DataFrame([record], columns=REQUIRED_COLUMNS)
    updated = pd.concat([data, attach_location_names(new_row)], ignore_index=True)
    save_fir_data(updated, path)
    return updated


def crime_profile(crime_type):
    return CRIME_PROFILES.get(str(crime_type).strip().lower(), {"severity": 1.0, "specification": "Standard response and monitoring."})


def crime_criticality(crime_type):
    severity = crime_profile(crime_type)["severity"]
    if severity >= 2.1:
        return "critical"
    if severity >= 1.5:
        return "medium"
    return "low"


def criticality_color(crime_type):
    return CRITICALITY_COLORS[crime_criticality(crime_type)]


def risk_band_from_score(score):
    if score >= 95:
        return "Critical"
    if score >= 78:
        return "High"
    if score >= 60:
        return "Elevated"
    return "Low"


def haversine_meters(lat1, lon1, lat2, lon2):
    from math import asin, cos, radians, sin, sqrt

    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    lat1 = radians(lat1)
    lat2 = radians(lat2)

    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


def point_to_route_distance_m(point_lat, point_lon, start_lat, start_lon, end_lat, end_lon):
    from math import cos, radians, sqrt

    lat_ref = radians((start_lat + end_lat + point_lat) / 3)
    meters_per_deg_lat = 111320
    meters_per_deg_lon = 111320 * cos(lat_ref)

    px = (point_lon - start_lon) * meters_per_deg_lon
    py = (point_lat - start_lat) * meters_per_deg_lat
    sx = 0
    sy = 0
    ex = (end_lon - start_lon) * meters_per_deg_lon
    ey = (end_lat - start_lat) * meters_per_deg_lat

    seg_dx = ex - sx
    seg_dy = ey - sy
    seg_len_sq = (seg_dx * seg_dx) + (seg_dy * seg_dy)
    if seg_len_sq == 0:
        return sqrt((px - sx) ** 2 + (py - sy) ** 2)

    projection = ((px - sx) * seg_dx + (py - sy) * seg_dy) / seg_len_sq
    projection = max(0, min(1, projection))
    closest_x = sx + projection * seg_dx
    closest_y = sy + projection * seg_dy
    return sqrt((px - closest_x) ** 2 + (py - closest_y) ** 2)


def find_crimes_along_route(data, start_point, end_point, corridor_m=700, limit=12):
    working = attach_location_names(data)
    if working.empty:
        return []

    matches = []
    for _, row in working.iterrows():
        distance_m = point_to_route_distance_m(
            row["latitude"],
            row["longitude"],
            start_point["latitude"],
            start_point["longitude"],
            end_point["latitude"],
            end_point["longitude"],
        )
        if distance_m <= corridor_m:
            profile = crime_profile(row["crime_type"])
            row_dict = row.to_dict()
            row_dict["distance_from_route_m"] = round(distance_m, 1)
            row_dict["severity"] = profile["severity"]
            matches.append(row_dict)

    matches.sort(key=lambda item: (-item["severity"], item["distance_from_route_m"]))
    return matches[:limit]


def suspect_photo_data_uri(row):
    criticality = crime_criticality(row["crime_type"])
    border = CRITICALITY_COLORS[criticality]
    label = criticality.upper()
    fir_number = str(row["fir_number"])[:16]
    crime_name = str(row["crime_type"]).title()[:18]
    svg = f"""
    <svg xmlns="http://www.w3.org/2000/svg" width="160" height="190" viewBox="0 0 160 190">
      <rect width="160" height="190" rx="14" fill="#f8fafc"/>
      <rect x="8" y="8" width="144" height="174" rx="12" fill="#e2e8f0" stroke="{border}" stroke-width="5"/>
      <rect x="20" y="18" width="120" height="32" rx="8" fill="{border}"/>
      <text x="80" y="39" font-size="16" text-anchor="middle" fill="white" font-family="Arial, sans-serif" font-weight="700">{label}</text>
      <circle cx="80" cy="86" r="28" fill="#334155"/>
      <rect x="48" y="118" width="64" height="40" rx="18" fill="#334155"/>
      <text x="80" y="171" font-size="12" text-anchor="middle" fill="#0f172a" font-family="Arial, sans-serif" font-weight="700">{fir_number}</text>
      <text x="80" y="184" font-size="10" text-anchor="middle" fill="#475569" font-family="Arial, sans-serif">{crime_name}</text>
    </svg>
    """.strip()
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def cluster_crimes(data, max_clusters=4):
    data = attach_location_names(data)
    if data.empty:
        data["cluster"] = pd.Series(dtype=int)
        return data, 0

    cluster_count = min(max_clusters, len(data))
    if cluster_count <= 1:
        data["cluster"] = 0
        return data, 1

    kmeans = KMeans(n_clusters=cluster_count, random_state=42, n_init=10)
    data["cluster"] = kmeans.fit_predict(data[["latitude", "longitude"]])
    return data, cluster_count


def map_center(data):
    if data.empty:
        return [13.0827, 80.2707]
    return [data["latitude"].mean(), data["longitude"].mean()]


def get_patrol_filtered_data(data, route_date=None, route_time=None):
    filtered = clean_data(data)
    if filtered.empty:
        return filtered

    route_date = route_date or current_date_string()
    route_time = route_time or default_time_slot()
    selected_dt = pd.to_datetime(route_date, errors="coerce")

    if pd.notna(selected_dt):
        weekday = selected_dt.dayofweek
        filtered = filtered[(filtered["incident_dt"].dt.dayofweek == weekday) | (filtered["incident_dt"].isna())]

    if route_time and route_time != "all":
        filtered = filtered[filtered["time"] == route_time]

    if len(filtered) < 6:
        filtered = clean_data(data)
    return filtered.reset_index(drop=True)


def generate_area_predictions(data, route_date=None, route_time=None, radius_m=100, top_n=8):
    working = get_patrol_filtered_data(data, route_date, route_time)
    if working.empty:
        return []

    candidates = []
    for _, center in working.iterrows():
        nearby = working[
            working.apply(
                lambda row: haversine_meters(
                    center["latitude"],
                    center["longitude"],
                    row["latitude"],
                    row["longitude"],
                )
                <= radius_m,
                axis=1,
            )
        ].copy()

        if nearby.empty:
            continue

        nearby["severity"] = nearby["crime_type"].map(lambda value: crime_profile(value)["severity"])
        density = len(nearby)
        avg_severity = nearby["severity"].mean()
        weighted_score = round((density * 10) + (avg_severity * 24), 2)

        candidates.append(
            {
                "latitude": round(center["latitude"], 6),
                "longitude": round(center["longitude"], 6),
                "location_name": center["location_name"],
                "crime_type": nearby["crime_type"].value_counts().idxmax().title(),
                "incident_count": int(density),
                "avg_severity": round(avg_severity, 2),
                "risk_score": weighted_score,
                "risk_band": risk_band_from_score(weighted_score),
                "radius_m": radius_m,
            }
        )

    candidates.sort(key=lambda item: item["risk_score"], reverse=True)
    selected = []
    for candidate in candidates:
        if all(
            haversine_meters(
                candidate["latitude"],
                candidate["longitude"],
                existing["latitude"],
                existing["longitude"],
            )
            > radius_m
            for existing in selected
        ):
            selected.append(candidate)
        if len(selected) >= top_n:
            break

    return selected


def _segment_risk_label(clustered_data, point):
    nearby = clustered_data[
        (clustered_data["latitude"].between(point[0] - 0.01, point[0] + 0.01))
        & (clustered_data["longitude"].between(point[1] - 0.01, point[1] + 0.01))
    ]
    if nearby.empty:
        return "safe"

    score = 0
    for _, row in nearby.iterrows():
        score += crime_profile(row["crime_type"])["severity"]

    if score >= 8:
        return "critical"
    if score >= 4:
        return "watch"
    return "safe"


def _build_route_segments(clustered_data, patrol_route):
    segments = []
    for index in range(max(0, len(patrol_route) - 1)):
        start = patrol_route[index]
        end = patrol_route[index + 1]
        end_name = resolve_location_name(end[0], end[1])
        risk = _segment_risk_label(clustered_data, end)
        segments.append(
            {
                "start": [start[0], start[1]],
                "end": [end[0], end[1]],
                "start_name": resolve_location_name(start[0], start[1]),
                "end_name": end_name,
                "risk": risk,
                "color": ROUTE_RISK_COLORS[risk],
                "label": f"Segment {index + 1}: {risk.title()} priority",
            }
        )
    return segments


def _add_road_routing(crime_map, segments):
    if not segments:
        return

    map_name = crime_map.get_name()
    segments_json = json.dumps(segments)
    assets = """
    <link rel="stylesheet" href="https://unpkg.com/leaflet-routing-machine@latest/dist/leaflet-routing-machine.css" />
    <script src="https://unpkg.com/leaflet-routing-machine@latest/dist/leaflet-routing-machine.js"></script>
    """
    script = f"""
    <script>
    document.addEventListener("DOMContentLoaded", function () {{
        var mapRef = {map_name};
        var segments = {segments_json};

        function drawSegments() {{
            if (!window.L || !L.Routing || !mapRef) {{
                return;
            }}
            segments.forEach(function(segment) {{
                L.Routing.control({{
                    waypoints: [
                        L.latLng(segment.start[0], segment.start[1]),
                        L.latLng(segment.end[0], segment.end[1])
                    ],
                    fitSelectedRoutes: false,
                    addWaypoints: false,
                    draggableWaypoints: false,
                    routeWhileDragging: false,
                    show: false,
                    createMarker: function() {{ return null; }},
                    lineOptions: {{
                        styles: [{{ color: segment.color, opacity: 0.92, weight: 6 }}]
                    }}
                }}).addTo(mapRef);
            }});
        }}
        if (window.L && L.Routing) {{
            drawSegments();
        }} else {{
            setTimeout(drawSegments, 1600);
        }}
    }});
    </script>
    """
    crime_map.get_root().header.add_child(Element(assets))
    crime_map.get_root().html.add_child(Element(script))


def _add_point_route(crime_map, start_point, end_point, route_color="#0b57d0"):
    map_name = crime_map.get_name()
    payload = json.dumps(
        {
            "start": [start_point["latitude"], start_point["longitude"]],
            "end": [end_point["latitude"], end_point["longitude"]],
            "color": route_color,
        }
    )
    assets = """
    <link rel="stylesheet" href="https://unpkg.com/leaflet-routing-machine@latest/dist/leaflet-routing-machine.css" />
    <script src="https://unpkg.com/leaflet-routing-machine@latest/dist/leaflet-routing-machine.js"></script>
    """
    script = f"""
    <script>
    document.addEventListener("DOMContentLoaded", function () {{
        var mapRef = {map_name};
        var routeData = {payload};
        function drawRoute() {{
            if (!window.L || !L.Routing || !mapRef) {{
                return;
            }}
            L.Routing.control({{
                waypoints: [
                    L.latLng(routeData.start[0], routeData.start[1]),
                    L.latLng(routeData.end[0], routeData.end[1])
                ],
                fitSelectedRoutes: true,
                addWaypoints: false,
                draggableWaypoints: false,
                routeWhileDragging: false,
                show: true,
                lineOptions: {{
                    styles: [{{ color: routeData.color, opacity: 0.92, weight: 7 }}]
                }}
            }}).addTo(mapRef);
        }}
        if (window.L && L.Routing) drawRoute();
        else setTimeout(drawRoute, 1600);
    }});
    </script>
    """
    crime_map.get_root().header.add_child(Element(assets))
    crime_map.get_root().html.add_child(Element(script))


def crime_popup_html(row):
    profile = crime_profile(row["crime_type"])
    criticality = crime_criticality(row["crime_type"])
    marker_color = criticality_color(row["crime_type"])
    risk_score = round(profile["severity"] * 40 + (10 if row["time"] in {"night", "late night"} else 0), 1)
    suspect_photo = suspect_photo_data_uri(row)
    return f"""
    <div style="min-width:290px;font-family:Segoe UI,sans-serif;">
        <h4 style="margin:0 0 10px;color:#0f172a;">Incident Detail</h4>
        <div style="display:flex;gap:12px;align-items:flex-start;margin-bottom:10px;">
            <img src="{suspect_photo}" alt="Suspect profile" style="width:96px;height:114px;object-fit:cover;border-radius:10px;border:3px solid {marker_color};background:#fff;" />
            <div style="flex:1;padding:8px 10px;background:#eff6ff;border-left:4px solid {marker_color};border-radius:8px;color:#0f172a;">
                <strong>{row['crime_type'].title()}</strong><br>{row['location_name']}<br>
                <span style="display:inline-block;margin-top:6px;padding:4px 8px;border-radius:999px;background:{marker_color};color:white;font-size:11px;font-weight:700;">{criticality.title()} Criticality</span>
            </div>
        </div>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <tr><td style="padding:6px 8px;font-weight:700;">FIR Number</td><td style="padding:6px 8px;">{row['fir_number']}</td></tr>
            <tr style="background:#f8fafc;"><td style="padding:6px 8px;font-weight:700;">Police Station</td><td style="padding:6px 8px;">{row['station_name']}</td></tr>
            <tr><td style="padding:6px 8px;font-weight:700;">Incident Date</td><td style="padding:6px 8px;">{row['incident_date']}</td></tr>
            <tr style="background:#f8fafc;"><td style="padding:6px 8px;font-weight:700;">Time Slot</td><td style="padding:6px 8px;">{TIME_SLOT_LABELS.get(row['time'], row['time'].title())}</td></tr>
            <tr><td style="padding:6px 8px;font-weight:700;">IPC Section</td><td style="padding:6px 8px;">{row['section']}</td></tr>
            <tr style="background:#f8fafc;"><td style="padding:6px 8px;font-weight:700;">People</td><td style="padding:6px 8px;">{row['people']}</td></tr>
            <tr><td style="padding:6px 8px;font-weight:700;">Victims / Suspects</td><td style="padding:6px 8px;">{row['victim_count']} / {row['suspect_count']}</td></tr>
            <tr style="background:#f8fafc;"><td style="padding:6px 8px;font-weight:700;">Status</td><td style="padding:6px 8px;">{row['status']}</td></tr>
            <tr><td style="padding:6px 8px;font-weight:700;">Criticality</td><td style="padding:6px 8px;">{criticality.title()}</td></tr>
            <tr style="background:#f8fafc;"><td style="padding:6px 8px;font-weight:700;">Operational Spec</td><td style="padding:6px 8px;">{profile['specification']}</td></tr>
            <tr><td style="padding:6px 8px;font-weight:700;">Coordinates</td><td style="padding:6px 8px;">{round(row['latitude'], 5)}, {round(row['longitude'], 5)}</td></tr>
            <tr style="background:#f8fafc;"><td style="padding:6px 8px;font-weight:700;">Risk Score</td><td style="padding:6px 8px;">{risk_score}</td></tr>
        </table>
    </div>
    """


def build_map(data, predicted_hotspot=None, area_predictions=None, route_date=None, route_time=None, path=MAP_FILE):
    all_data, cluster_count = cluster_crimes(data)
    patrol_data = get_patrol_filtered_data(all_data, route_date, route_time)
    patrol_clustered, patrol_cluster_count = cluster_crimes(patrol_data)
    center = map_center(all_data)

    crime_map = folium.Map(location=center, zoom_start=12, tiles=None, control_scale=True)
    folium.TileLayer("OpenStreetMap", name="Street Map").add_to(crime_map)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Satellite View",
    ).add_to(crime_map)
    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        attr="CartoDB Voyager",
        name="Navigation View",
    ).add_to(crime_map)
    Fullscreen(position="topright").add_to(crime_map)
    MiniMap(toggle_display=True).add_to(crime_map)

    for _, row in all_data.iterrows():
        profile = crime_profile(row["crime_type"])
        marker_color = criticality_color(row["crime_type"])
        folium.CircleMarker(
            location=[row["latitude"], row["longitude"]],
            radius=9 if profile["severity"] >= 2 else 7,
            color="#ffffff",
            weight=1,
            fill=True,
            fill_color=marker_color,
            fill_opacity=0.88,
            popup=folium.Popup(crime_popup_html(row), max_width=360),
            tooltip=f"{row['crime_type'].title()} | {row['location_name']}",
        ).add_to(crime_map)

    route_points = get_high_risk_zones(patrol_clustered, top_n=min(4, max(patrol_cluster_count, 1)))
    patrol_route = generate_patrol_route(route_points, start_point=center if route_points else None)
    route_segments = _build_route_segments(patrol_clustered, patrol_route)
    _add_road_routing(crime_map, route_segments)

    for index, point in enumerate(route_points, start=1):
        hotspot_name = resolve_location_name(point[0], point[1])
        risk = _segment_risk_label(patrol_clustered, point)
        marker_color = "green" if risk == "safe" else "orange" if risk == "watch" else "red"
        folium.Marker(
            location=point,
            icon=folium.Icon(color=marker_color, icon="info-sign"),
            tooltip=f"Hotspot {index} | {hotspot_name}",
            popup=f"<b>Hotspot {index}</b><br>Location: {hotspot_name}<br>Priority: {risk.title()}",
        ).add_to(crime_map)

    for segment in route_segments:
        midpoint = [(segment["start"][0] + segment["end"][0]) / 2, (segment["start"][1] + segment["end"][1]) / 2]
        folium.Marker(
            location=midpoint,
            icon=folium.DivIcon(
                html=f"<div style='background:{segment['color']};color:white;padding:4px 8px;border-radius:999px;font-size:11px;font-weight:700;'>{segment['risk'].title()}</div>"
            ),
            tooltip=segment["label"],
        ).add_to(crime_map)

    if predicted_hotspot:
        hotspot_name = resolve_location_name(predicted_hotspot["latitude"], predicted_hotspot["longitude"])
        folium.Circle(
            location=[predicted_hotspot["latitude"], predicted_hotspot["longitude"]],
            radius=240,
            color="#7c3aed",
            fill=True,
            fill_color="#c4b5fd",
            fill_opacity=0.2,
            popup=(
                "<b>Predicted Hotspot</b><br>"
                f"Location: {hotspot_name}<br>"
                f"Expected crime: {predicted_hotspot['crime_type']}<br>"
                f"Suggested window: {predicted_hotspot['peak_time']} on {predicted_hotspot['route_date']}<br>"
                f"Risk band: {predicted_hotspot['risk_band']}<br>"
                f"Confidence: {predicted_hotspot['confidence']}%"
            ),
        ).add_to(crime_map)

    for area in area_predictions or []:
        band_color = {
            "Low": "#22c55e",
            "Elevated": "#f59e0b",
            "High": "#dc2626",
            "Critical": "#991b1b",
        }[area["risk_band"]]
        folium.Circle(
            location=[area["latitude"], area["longitude"]],
            radius=area["radius_m"],
            color=band_color,
            fill=True,
            fill_color=band_color,
            fill_opacity=0.14,
            popup=(
                f"<b>{area['risk_band']} 100m Prediction Zone</b><br>"
                f"Location: {area['location_name']}<br>"
                f"Likely crime: {area['crime_type']}<br>"
                f"Nearby incidents: {area['incident_count']}<br>"
                f"Risk score: {area['risk_score']}"
            ),
            tooltip=f"{area['risk_band']} | {area['location_name']}",
        ).add_to(crime_map)

    folium.LayerControl(collapsed=False).add_to(crime_map)
    legend_html = f"""
    <div style="position: fixed; bottom: 24px; left: 24px; z-index: 9999; background: white; padding: 12px 14px; border-radius: 14px; box-shadow: 0 8px 24px rgba(0,0,0,0.18); font-size: 12px; max-width: 270px;">
        <div style="font-weight:700; margin-bottom:8px;">Patrol Planning Window</div>
        <div>Date: {route_date or current_date_string()}</div>
        <div>Time: {TIME_SLOT_LABELS.get(route_time or default_time_slot(), (route_time or default_time_slot()).title())}</div>
        <hr style="border:none;border-top:1px solid #e2e8f0;margin:8px 0;">
        <div><span style="display:inline-block;width:12px;height:12px;background:#22c55e;border-radius:50%;margin-right:8px;"></span>Safe segment</div>
        <div><span style="display:inline-block;width:12px;height:12px;background:#f59e0b;border-radius:50%;margin-right:8px;"></span>Watch segment</div>
        <div><span style="display:inline-block;width:12px;height:12px;background:#ef4444;border-radius:50%;margin-right:8px;"></span>Critical segment</div>
        <div style="margin-top:8px;"><span style="display:inline-block;width:12px;height:12px;background:#22c55e;border-radius:50%;margin-right:8px;"></span>Low criticality incidents</div>
        <div><span style="display:inline-block;width:12px;height:12px;background:#f59e0b;border-radius:50%;margin-right:8px;"></span>Medium criticality incidents</div>
        <div><span style="display:inline-block;width:12px;height:12px;background:#991b1b;border-radius:50%;margin-right:8px;"></span>Critical crimes like murder</div>
    </div>
    """
    crime_map.get_root().html.add_child(Element(legend_html))

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    crime_map.save(path)
    return {
        "clustered_data": all_data,
        "route_points": route_points,
        "patrol_route": patrol_route,
        "route_segments": route_segments,
        "area_predictions": area_predictions or [],
        "cluster_count": cluster_count,
        "route_window": {
            "date": route_date or current_date_string(),
            "time": route_time or default_time_slot(),
            "filtered_cases": int(len(patrol_data)),
        },
    }


def build_heat_map(data, route_date=None, route_time=None, path=HEAT_MAP_FILE):
    working = get_patrol_filtered_data(data, route_date, route_time)
    display_data = working if not working.empty else attach_location_names(data)
    center = map_center(display_data)
    top_hotspots = []

    heat_map = folium.Map(location=center, zoom_start=12, tiles=None, control_scale=True)
    folium.TileLayer("OpenStreetMap", name="Street Map").add_to(heat_map)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Satellite View",
    ).add_to(heat_map)
    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        attr="CartoDB Voyager",
        name="Navigation View",
    ).add_to(heat_map)
    Fullscreen(position="topright").add_to(heat_map)
    MiniMap(toggle_display=True).add_to(heat_map)

    if not display_data.empty:
        heat_points = [
            [row["latitude"], row["longitude"], crime_profile(row["crime_type"])["severity"]]
            for _, row in display_data.iterrows()
        ]
        HeatMap(
            heat_points,
            name="Crime Density",
            radius=28,
            blur=20,
            min_opacity=0.3,
            max_zoom=15,
            gradient={0.2: "#22c55e", 0.45: "#f59e0b", 0.7: "#ef4444", 1.0: "#991b1b"},
        ).add_to(heat_map)

        top_hotspots = generate_area_predictions(display_data, route_date, route_time, radius_m=180, top_n=6)
        for area in top_hotspots:
            band_color = {"Low": "#22c55e", "Elevated": "#f59e0b", "High": "#dc2626", "Critical": "#991b1b"}[area["risk_band"]]
            folium.Circle(
                location=[area["latitude"], area["longitude"]],
                radius=max(area["radius_m"], 180),
                color=band_color,
                fill=True,
                fill_color=band_color,
                fill_opacity=0.18,
                popup=(
                    f"<b>{area['risk_band']} Collective Crime Zone</b><br>"
                    f"Location: {area['location_name']}<br>"
                    f"Likely crime: {area['crime_type']}<br>"
                    f"Nearby incidents: {area['incident_count']}<br>"
                    f"Risk score: {area['risk_score']}"
                ),
                tooltip=f"{area['risk_band']} | {area['location_name']}",
            ).add_to(heat_map)
            folium.Marker(
                location=[area["latitude"], area["longitude"]],
                icon=folium.DivIcon(
                    html=(
                        f"<div style='background:{band_color};color:white;padding:6px 10px;border-radius:999px;"
                        "font-size:11px;font-weight:700;box-shadow:0 8px 16px rgba(0,0,0,0.18);'>"
                        f"{area['risk_band']} Zone</div>"
                    )
                ),
                tooltip=f"{area['risk_band']} | {area['location_name']}",
            ).add_to(heat_map)

    folium.LayerControl(collapsed=False).add_to(heat_map)
    legend_html = """
    <div style="position: fixed; bottom: 24px; left: 24px; z-index: 9999; background: white; padding: 12px 14px; border-radius: 14px; box-shadow: 0 8px 24px rgba(0,0,0,0.18); font-size: 12px; max-width: 270px;">
        <div style="font-weight:700; margin-bottom:8px;">Crime Heat Map</div>
        <div><span style="display:inline-block;width:12px;height:12px;background:#22c55e;border-radius:50%;margin-right:8px;"></span>Low collective activity</div>
        <div><span style="display:inline-block;width:12px;height:12px;background:#f59e0b;border-radius:50%;margin-right:8px;"></span>Moderate collective activity</div>
        <div><span style="display:inline-block;width:12px;height:12px;background:#ef4444;border-radius:50%;margin-right:8px;"></span>High collective activity</div>
        <div><span style="display:inline-block;width:12px;height:12px;background:#991b1b;border-radius:50%;margin-right:8px;"></span>Critical crime concentration</div>
    </div>
    """
    heat_map.get_root().html.add_child(Element(legend_html))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    heat_map.save(path)
    return {
        "path": "static/crime_heat_map.html",
        "case_count": int(len(display_data)),
        "window_date": route_date or current_date_string(),
        "window_time": TIME_SLOT_LABELS.get(route_time or default_time_slot(), (route_time or default_time_slot()).title()),
        "hotspots": top_hotspots,
    }


def build_route_map(start_query, end_query, data=None, path=ROUTE_MAP_FILE):
    start_point = geocode_place_name(start_query)
    end_point = geocode_place_name(end_query)

    if not start_point or not end_point:
        return {
            "success": False,
            "message": "Unable to find one or both locations. Try using a more specific area or landmark name.",
            "map_path": "static/route_map.html",
            "start": start_query,
            "end": end_query,
            "google_maps_url": "",
            "crimes_on_route": [],
        }

    center = [
        (start_point["latitude"] + end_point["latitude"]) / 2,
        (start_point["longitude"] + end_point["longitude"]) / 2,
    ]
    route_map = folium.Map(location=center, zoom_start=12, tiles=None, control_scale=True)
    folium.TileLayer("OpenStreetMap", name="Street Map").add_to(route_map)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Satellite View",
    ).add_to(route_map)
    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
        attr="CartoDB Voyager",
        name="Navigation View",
    ).add_to(route_map)
    Fullscreen(position="topright").add_to(route_map)
    MiniMap(toggle_display=True).add_to(route_map)

    folium.Marker(
        location=[start_point["latitude"], start_point["longitude"]],
        tooltip=f"Start | {start_point['name']}",
        popup=f"<b>Start</b><br>{start_point['name']}",
        icon=folium.Icon(color="green", icon="play"),
    ).add_to(route_map)
    folium.Marker(
        location=[end_point["latitude"], end_point["longitude"]],
        tooltip=f"Destination | {end_point['name']}",
        popup=f"<b>Destination</b><br>{end_point['name']}",
        icon=folium.Icon(color="red", icon="flag"),
    ).add_to(route_map)

    _add_point_route(route_map, start_point, end_point)
    crimes_on_route = find_crimes_along_route(data, start_point, end_point) if data is not None else []

    for incident in crimes_on_route:
        folium.CircleMarker(
            location=[incident["latitude"], incident["longitude"]],
            radius=8 if incident["severity"] >= 2 else 6,
            color="#ffffff",
            weight=1,
            fill=True,
            fill_color=criticality_color(incident["crime_type"]),
            fill_opacity=0.92,
            popup=folium.Popup(crime_popup_html(incident), max_width=360),
            tooltip=f"{str(incident['crime_type']).title()} | {incident['location_name']}",
        ).add_to(route_map)

    folium.LayerControl(collapsed=False).add_to(route_map)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    route_map.save(path)

    start_coords = f"{start_point['latitude']},{start_point['longitude']}"
    end_coords = f"{end_point['latitude']},{end_point['longitude']}"
    google_maps_url = (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={quote_plus(start_coords)}"
        f"&destination={quote_plus(end_coords)}"
        "&travelmode=driving"
        "&dir_action=navigate"
    )

    return {
        "success": True,
        "message": "Route generated. Use the Google Maps button below for full GPS-style navigation.",
        "map_path": "static/route_map.html",
        "start": start_point["name"],
        "end": end_point["name"],
        "google_maps_url": google_maps_url,
        "crimes_on_route": crimes_on_route,
        "critical_crimes_on_route": [item for item in crimes_on_route if item["severity"] >= 2.1],
    }


def summarize_women_safety(women_safety):
    if women_safety.empty:
        return {
            "overview": {
                "total_incidents": 0,
                "critical_incidents": 0,
                "night_incidents": 0,
                "repeat_stations": 0,
            },
            "analysis": [],
            "team_patrols": [],
        }

    night_incidents = women_safety[women_safety["time"].isin({"night", "late night"})]
    critical_incidents = women_safety[women_safety["crime_type"].str.lower().isin({"rape", "molestation", "stalking"})]
    station_focus = women_safety["station_name"].value_counts().head(3)
    time_focus = (
        women_safety.assign(time_label=women_safety["time"].map(TIME_SLOT_LABELS).fillna(women_safety["time"].str.title()))["time_label"]
        .value_counts()
        .head(3)
    )
    crime_focus = women_safety["crime_type"].str.title().value_counts().head(3)

    analysis = [
        {
            "label": "Most Affected Station",
            "value": station_focus.index[0] if not station_focus.empty else "No pattern available",
            "detail": f"{int(station_focus.iloc[0])} women-safety FIRs registered" if not station_focus.empty else "Awaiting women-safety FIR records",
        },
        {
            "label": "Most Sensitive Time Window",
            "value": time_focus.index[0] if not time_focus.empty else "No pattern available",
            "detail": f"{int(time_focus.iloc[0])} incidents mapped in this time band" if not time_focus.empty else "Awaiting time-based incident pattern",
        },
        {
            "label": "Primary Incident Type",
            "value": crime_focus.index[0] if not crime_focus.empty else "No pattern available",
            "detail": f"{int(crime_focus.iloc[0])} FIRs in this category" if not crime_focus.empty else "Awaiting category pattern",
        },
    ]

    team_patrols = []
    for station_name, count in station_focus.items():
        station_rows = women_safety[women_safety["station_name"] == station_name]
        peak_time = (
            station_rows.assign(time_label=station_rows["time"].map(TIME_SLOT_LABELS).fillna(station_rows["time"].str.title()))["time_label"]
            .value_counts()
            .index[0]
            if not station_rows.empty
            else "All Day"
        )
        top_locations = station_rows["location_name"].replace("unknown", pd.NA).dropna().value_counts().head(2).index.tolist()
        deployment_area = ", ".join(top_locations) if top_locations else "Community patrol coverage zone"
        team_patrols.append(
            {
                "team": f"Women Patrol Team {len(team_patrols) + 1}",
                "station": station_name,
                "time_window": peak_time,
                "coverage": deployment_area,
                "priority": "Critical" if station_rows["crime_type"].str.lower().isin({"rape", "molestation"}).any() else "Watch",
                "instruction": f"Deploy visible women patrol presence around {deployment_area} during {peak_time.lower()} and coordinate with beat officers.",
                "incident_count": int(count),
            }
        )

    return {
        "overview": {
            "total_incidents": int(len(women_safety)),
            "critical_incidents": int(len(critical_incidents)),
            "night_incidents": int(len(night_incidents)),
            "repeat_stations": int((women_safety["station_name"].value_counts() > 1).sum()),
        },
        "analysis": analysis,
        "team_patrols": team_patrols,
    }


def summarize_emergency_alerts(data):
    if data.empty:
        return {
            "overview": {
                "critical_open_cases": 0,
                "stations_on_alert": 0,
                "latest_dispatch_distance_km": 0.0,
            },
            "alerts": [],
        }

    station_points = (
        data[data["station_name"].ne("unknown")]
        .groupby("station_name")[["latitude", "longitude"]]
        .mean()
        .reset_index()
        .to_dict(orient="records")
    )

    emergency_rows = data.copy()
    emergency_rows["severity"] = emergency_rows["crime_type"].map(lambda value: crime_profile(value)["severity"])
    emergency_rows = emergency_rows[
        (emergency_rows["severity"] >= 1.8)
        | (emergency_rows["crime_type"].str.lower().isin({"murder", "attempt to murder", "rape", "kidnapping", "robbery"}))
    ].copy()
    emergency_rows = emergency_rows.sort_values(["severity", "incident_dt"], ascending=[False, False]).head(5)

    alerts = []
    for _, row in emergency_rows.iterrows():
        nearest_station = None
        nearest_distance = None
        for station in station_points:
            distance_m = haversine_meters(row["latitude"], row["longitude"], station["latitude"], station["longitude"])
            if nearest_distance is None or distance_m < nearest_distance:
                nearest_distance = distance_m
                nearest_station = station

        if nearest_station is None:
            nearest_station_name = row["station_name"] if row["station_name"] != "unknown" else "Nearest police station unavailable"
            nearest_distance_km = 0.0
        else:
            nearest_station_name = nearest_station["station_name"]
            nearest_distance_km = round(nearest_distance / 1000, 2)

        alerts.append(
            {
                "crime_type": str(row["crime_type"]).title(),
                "location_name": row["location_name"],
                "incident_date": row["incident_date"],
                "time": TIME_SLOT_LABELS.get(row["time"], str(row["time"]).title()),
                "nearest_station": nearest_station_name,
                "distance_km": nearest_distance_km,
                "severity_band": "Critical" if row["severity"] >= 2.2 else "Watch",
                "fir_number": row["fir_number"],
                "instruction": f"Send immediate field alert to {nearest_station_name} and dispatch patrol coverage for {row['location_name']}.",
            }
        )

    return {
        "overview": {
            "critical_open_cases": int(len(emergency_rows)),
            "stations_on_alert": int(len({alert['nearest_station'] for alert in alerts if alert['nearest_station']})),
            "latest_dispatch_distance_km": alerts[0]["distance_km"] if alerts else 0.0,
        },
        "alerts": alerts,
    }


def summarize_dashboard(data):
    data = attach_location_names(data)
    total_cases = int(len(data))

    crime_counts = data["crime_type"].str.title().value_counts().head(8).reset_index(name="count").rename(columns={"index": "crime_type"}) if not data.empty else pd.DataFrame(columns=["crime_type", "count"])
    time_counts = (
        data.assign(time_label=data["time"].map(TIME_SLOT_LABELS).fillna(data["time"].str.title()))
        .groupby("time_label")
        .size()
        .reindex([TIME_SLOT_LABELS[slot] for slot in TIME_SLOT_ORDER], fill_value=0)
        .reset_index(name="count")
        .rename(columns={"index": "time"})
        if not data.empty
        else pd.DataFrame(columns=["time", "count"])
    )
    station_counts = (
        data["station_name"].value_counts().head(6).reset_index(name="count").rename(columns={"index": "station_name"})
        if not data.empty
        else pd.DataFrame(columns=["station_name", "count"])
    )
    section_counts = (
        data["section"].value_counts().head(8).reset_index(name="count").rename(columns={"index": "section"})
        if not data.empty
        else pd.DataFrame(columns=["section", "count"])
    )

    trend_source = data.dropna(subset=["incident_dt"]).copy()
    monthly_counts = (
        trend_source.assign(month_key=trend_source["incident_dt"].dt.to_period("M"))
        .groupby("month_key")
        .size()
        .reset_index(name="count")
        .assign(month=lambda frame: frame["month_key"].dt.strftime("%b %Y"))[["month", "count"]]
        if not trend_source.empty
        else pd.DataFrame(columns=["month", "count"])
    )

    women_safety = data[data["crime_type"].str.lower().isin(WOMEN_RELATED_KEYWORDS)] if not data.empty else data
    women_safety_summary = summarize_women_safety(women_safety)
    emergency_summary = summarize_emergency_alerts(data)
    major_crimes = data[data["crime_type"].str.lower().isin({"murder", "attempt to murder", "rape", "kidnapping"})] if not data.empty else data
    latest_fir = data.sort_values("incident_dt", ascending=False).head(1).to_dict(orient="records")
    crime_peak = crime_counts.iloc[0].to_dict() if not crime_counts.empty else {"crime_type": "No data", "count": 0}
    busiest_station = station_counts.iloc[0].to_dict() if not station_counts.empty else {"station_name": "No data", "count": 0}
    peak_time = time_counts.iloc[time_counts["count"].idxmax()].to_dict() if not time_counts.empty else {"time_label": "No data", "count": 0}

    return {
        "total_cases": total_cases,
        "crime_counts": crime_counts.to_dict(orient="records"),
        "time_counts": time_counts.rename(columns={"time_label": "time"}).to_dict(orient="records"),
        "section_counts": section_counts.to_dict(orient="records"),
        "station_counts": station_counts.to_dict(orient="records"),
        "women_safety_cases": int(len(women_safety)),
        "women_safety_records": women_safety.to_dict(orient="records"),
        "women_safety_overview": women_safety_summary["overview"],
        "women_safety_analysis": women_safety_summary["analysis"],
        "women_team_patrols": women_safety_summary["team_patrols"],
        "emergency_overview": emergency_summary["overview"],
        "emergency_alerts": emergency_summary["alerts"],
        "major_crime_cases": int(len(major_crimes)),
        "latest_fir": latest_fir[0] if latest_fir else None,
        "last_updated": trend_source["incident_dt"].max().strftime("%Y-%m-%d") if not trend_source.empty else "Unknown",
        "crime_chart_labels": crime_counts["crime_type"].tolist() if not crime_counts.empty else [],
        "crime_chart_values": crime_counts["count"].tolist() if not crime_counts.empty else [],
        "time_chart_labels": time_counts["time_label"].tolist() if not time_counts.empty else [],
        "time_chart_values": time_counts["count"].tolist() if not time_counts.empty else [],
        "station_chart_labels": station_counts["station_name"].tolist() if not station_counts.empty else [],
        "station_chart_values": station_counts["count"].tolist() if not station_counts.empty else [],
        "trend_chart_labels": monthly_counts["month"].tolist() if not monthly_counts.empty else [],
        "trend_chart_values": monthly_counts["count"].tolist() if not monthly_counts.empty else [],
        "crime_overview": {
            "top_category": {"label": crime_peak.get("crime_type", "No data"), "count": int(crime_peak.get("count", 0) or 0)},
            "busiest_station": {"label": busiest_station.get("station_name", "No data"), "count": int(busiest_station.get("count", 0) or 0)},
            "peak_time": {"label": peak_time.get("time_label", "No data"), "count": int(peak_time.get("count", 0) or 0)},
        },
        "data_source": "CCTNS FIR Export" if get_data_source_path() == CCTNS_DATA_FILE else "Local FIR Dataset",
    }
