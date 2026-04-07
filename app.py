import os
from functools import wraps

from flask import Flask, redirect, render_template, request, session, url_for

from prediction import generate_prediction_summary
from utils import (
    TIME_SLOT_ORDER,
    add_fir_record,
    build_heat_map,
    build_map,
    build_route_map,
    build_route_picker_map,
    current_date_string,
    default_time_slot,
    get_data_source_path,
    load_fir_data,
    summarize_dashboard,
    validate_coordinates,
)


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "crime-intelligence-secret-key")

APP_USERNAME = os.environ.get("APP_USERNAME", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "police123")


def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapped_view


@app.route("/login", methods=["GET", "POST"])
def login():
    error_message = None

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if username == APP_USERNAME and password == APP_PASSWORD:
            session["authenticated"] = True
            session["username"] = username
            next_url = request.args.get("next") or url_for("home")
            return redirect(next_url)

        error_message = "Invalid username or password."

    return render_template("login.html", error_message=error_message)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def home():
    active_section = request.args.get("active_section", "home")
    route_date = request.args.get("route_date") or current_date_string()
    route_time = request.args.get("route_time") or default_time_slot()
    start_location = request.args.get("start_location", "").strip()
    end_location = request.args.get("end_location", "").strip()

    data = load_fir_data()
    prediction_summary = generate_prediction_summary(data, route_date, route_time)
    map_state = build_map(data, prediction_summary["prediction"], prediction_summary["area_predictions"], route_date, route_time)
    heat_map_state = build_heat_map(data, route_date, route_time)
    dashboard = summarize_dashboard(map_state["clustered_data"])
    route_map_state = build_route_map(start_location, end_location, data=data) if start_location and end_location else {
        "success": False,
        "message": "Enter a start and destination to generate a GPS-style patrol route.",
        "map_path": "static/route_map.html",
        "start": start_location,
        "end": end_location,
        "google_maps_url": "",
        "crimes_on_route": [],
        "critical_crimes_on_route": [],
    }
    build_route_picker_map()

    return render_template(
        "index.html",
        map_url=url_for("static", filename="crime_map.html"),
        heat_map_url=url_for("static", filename="crime_heat_map.html"),
        route_map_url=url_for("static", filename="route_map.html"),
        route_picker_url=url_for("static", filename="route_picker.html"),
        active_section=active_section,
        route_date=route_date,
        route_time=route_time,
        time_slots=TIME_SLOT_ORDER,
        start_location=start_location,
        end_location=end_location,
        route_map_state=route_map_state,
        heat_map_state=heat_map_state,
        total_cases=dashboard["total_cases"],
        crime_counts=dashboard["crime_counts"],
        time_counts=dashboard["time_counts"],
        section_counts=dashboard["section_counts"],
        station_counts=dashboard["station_counts"],
        crime_overview=dashboard["crime_overview"],
        crime_chart_labels=dashboard["crime_chart_labels"],
        crime_chart_values=dashboard["crime_chart_values"],
        time_chart_labels=dashboard["time_chart_labels"],
        time_chart_values=dashboard["time_chart_values"],
        station_chart_labels=dashboard["station_chart_labels"],
        station_chart_values=dashboard["station_chart_values"],
        trend_chart_labels=dashboard["trend_chart_labels"],
        trend_chart_values=dashboard["trend_chart_values"],
        data_source=dashboard["data_source"],
        women_safety_cases=dashboard["women_safety_cases"],
        women_safety_records=dashboard["women_safety_records"],
        women_safety_overview=dashboard["women_safety_overview"],
        women_safety_analysis=dashboard["women_safety_analysis"],
        women_team_patrols=dashboard["women_team_patrols"],
        emergency_overview=dashboard["emergency_overview"],
        emergency_alerts=dashboard["emergency_alerts"],
        major_crime_cases=dashboard["major_crime_cases"],
        latest_fir=dashboard["latest_fir"],
        last_updated=dashboard["last_updated"],
        patrol_route=map_state["patrol_route"],
        route_segments=map_state["route_segments"],
        area_predictions=map_state["area_predictions"],
        cluster_count=map_state["cluster_count"],
        route_window=map_state["route_window"],
        prediction_summary=prediction_summary,
        logged_in_user=session.get("username", APP_USERNAME),
    )


@app.route("/add", methods=["POST"])
@login_required
def add():
    form_data = {
        "latitude": request.form.get("lat", "").strip(),
        "longitude": request.form.get("lon", "").strip(),
        "crime_type": request.form.get("crime", "").strip() or "unknown",
        "time": request.form.get("time", "").strip() or default_time_slot(),
        "people": request.form.get("people", "").strip() or "unknown",
        "section": request.form.get("section", "").strip() or "unknown",
        "station_name": request.form.get("station_name", "").strip() or "unknown",
        "fir_number": request.form.get("fir_number", "").strip() or "",
        "incident_date": request.form.get("incident_date", "").strip() or current_date_string(),
        "status": request.form.get("status", "").strip() or "Registered",
        "victim_count": request.form.get("victim_count", "1").strip() or "1",
        "suspect_count": request.form.get("suspect_count", "1").strip() or "1",
        "location_name": request.form.get("location_name", "").strip() or "unknown",
    }

    if not validate_coordinates(form_data["latitude"], form_data["longitude"]):
        return "Invalid latitude or longitude. Please enter valid coordinates.", 400

    form_data["latitude"] = float(form_data["latitude"])
    form_data["longitude"] = float(form_data["longitude"])
    form_data["victim_count"] = int(form_data["victim_count"])
    form_data["suspect_count"] = int(form_data["suspect_count"])

    add_fir_record(form_data, get_data_source_path())
    return redirect(url_for("home", route_date=form_data["incident_date"], route_time=form_data["time"], active_section="fir"))


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true",
    )
