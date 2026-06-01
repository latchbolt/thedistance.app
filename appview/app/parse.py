import gzip
import logging
import math
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from io import BytesIO

import fitparse
import polyline

log = logging.getLogger(__name__)


def parse_file(filename: str, data: bytes) -> dict:
    """Detect file format from extension and dispatch to the appropriate parser.

    Returns a dict shaped to match the app.thedistance.activity record schema,
    using snake_case keys matching the database column names.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "gz":
        inner_name = filename[:-3]
        data = gzip.decompress(data)
        return parse_file(inner_name, data)
    elif ext == "fit":
        return parse_fit(data)
    elif ext == "gpx":
        return parse_gpx(data)
    elif ext == "tcx":
        return parse_tcx(data)
    else:
        raise ValueError(f"Unsupported file format: .{ext}")


def parse_fit(data: bytes) -> dict:
    """Parse a FIT file and return an activity record dict.

    FIT files contain multiple message types. We extract three:
    - "session": summary stats for the whole activity (times, distance, averages)
    - "record": per-second data points with GPS coordinates and sensor readings
    - "device_info": device metadata (we grab the first product_name we find)

    GPS coordinates in FIT are stored as semicircles (32-bit integers). We convert
    them to degrees and encode the full route as a Google encoded polyline.
    """
    fit = fitparse.FitFile(BytesIO(data))
    fit.parse()

    session = None
    records = []
    device_name = None

    for msg in fit.get_messages():
        if msg.name == "session":
            session = msg
        elif msg.name == "record":
            records.append(msg)
        elif msg.name == "device_info" and not device_name:
            name = msg.get_value("product_name")
            if name:
                device_name = name

    if not session:
        raise ValueError("No session data found in FIT file")

    # Use sub_sport for more specificity (e.g. "gravel_cycling" instead of "cycling"),
    # falling back to sport if sub_sport is absent or "generic"
    sport = session.get_value("sport")
    sub_sport = session.get_value("sub_sport")
    sport_type = sub_sport if sub_sport and sub_sport != "generic" else sport

    start_time = session.get_value("start_time")
    if isinstance(start_time, datetime):
        started_at = start_time.replace(tzinfo=UTC).isoformat()
    else:
        started_at = str(start_time)

    elapsed_time = session.get_value("total_elapsed_time")
    moving_time = session.get_value("total_timer_time")
    distance = session.get_value("total_distance")
    elevation_gain = session.get_value("total_ascent")
    avg_speed = session.get_value("avg_speed")
    max_speed = session.get_value("max_speed")
    avg_heart_rate = session.get_value("avg_heart_rate")
    max_heart_rate = session.get_value("max_heart_rate")
    avg_cadence = session.get_value("avg_cadence")
    max_cadence = session.get_value("max_cadence")
    avg_power = session.get_value("avg_power")
    max_power = session.get_value("max_power")
    calories = session.get_value("total_calories")

    # Build polyline from per-second GPS records
    route_points = []
    for rec in records:
        lat = rec.get_value("position_lat")
        lon = rec.get_value("position_long")
        if lat is not None and lon is not None:
            lat_deg = lat * (180 / 2**31)
            lon_deg = lon * (180 / 2**31)
            route_points.append((lat_deg, lon_deg))

    encoded_polyline = polyline.encode(route_points) if route_points else None

    activity = _build_activity(
        sport_type=sport_type,
        start_time=start_time,
        started_at=started_at,
        elapsed_time=elapsed_time,
        moving_time=moving_time,
        distance=distance,
        source="fit-file",
        elevation_gain=elevation_gain,
        avg_speed=avg_speed,
        max_speed=max_speed,
        avg_heart_rate=avg_heart_rate,
        max_heart_rate=max_heart_rate,
        avg_cadence=avg_cadence,
        max_cadence=max_cadence,
        avg_power=avg_power,
        max_power=max_power,
        calories=calories,
        encoded_polyline=encoded_polyline,
        device_name=device_name,
    )

    return activity


def parse_gpx(data: bytes) -> dict:
    """Parse a GPX file and return an activity record dict.

    GPX files store tracks as sequences of lat/lon points with optional elevation
    and timestamps. Summary stats (distance, elevation gain, duration) are computed
    from the raw track points since GPX has no session-level summary.

    Heart rate, cadence, and power may appear in Garmin TrackPointExtension elements.
    """
    root = ET.fromstring(data)
    ns = _gpx_namespaces(root)

    trk = root.find("gpx:trk", ns)
    if trk is None:
        raise ValueError("No track found in GPX file")

    # Sport type from <type> element, common in Garmin GPX exports
    type_el = trk.find("gpx:type", ns)
    sport_type = type_el.text.strip().lower().replace(" ", "_") if type_el is not None else None

    points = []
    for trkpt in trk.findall(".//gpx:trkseg/gpx:trkpt", ns):
        pt = _parse_gpx_trackpoint(trkpt, ns)
        points.append(pt)

    if not points:
        raise ValueError("No track points found in GPX file")

    start_time = points[0].get("time")
    end_time = points[-1].get("time")
    started_at = start_time.isoformat() if start_time else datetime.now(UTC).isoformat()

    elapsed_time = None
    if start_time and end_time:
        elapsed_time = (end_time - start_time).total_seconds()

    distance = _compute_distance(points)
    elevation_gain = _compute_elevation_gain(points)

    route_points = [(p["lat"], p["lon"]) for p in points]
    encoded_polyline = polyline.encode(route_points) if route_points else None

    # Collect heart rate, cadence, and power values to compute averages and maxes
    hr_values = [p["hr"] for p in points if p.get("hr") is not None]
    cad_values = [p["cad"] for p in points if p.get("cad") is not None]
    power_values = [p["power"] for p in points if p.get("power") is not None]

    activity = _build_activity(
        sport_type=sport_type,
        start_time=start_time,
        started_at=started_at,
        elapsed_time=elapsed_time,
        moving_time=elapsed_time,
        distance=distance,
        source="gpx-file",
        elevation_gain=elevation_gain,
        avg_heart_rate=sum(hr_values) / len(hr_values) if hr_values else None,
        max_heart_rate=max(hr_values) if hr_values else None,
        avg_cadence=sum(cad_values) / len(cad_values) if cad_values else None,
        max_cadence=max(cad_values) if cad_values else None,
        avg_power=sum(power_values) / len(power_values) if power_values else None,
        max_power=max(power_values) if power_values else None,
        encoded_polyline=encoded_polyline,
    )

    return activity


def parse_tcx(data: bytes) -> dict:
    """Parse a TCX file and return an activity record dict.

    TCX files organize data into Laps, each with summary stats and a track of
    individual points. We sum lap-level stats for totals and collect all track
    points for the polyline and per-point metrics.
    """
    root = ET.fromstring(data)
    ns = {"tcx": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"}
    ext_ns = "http://www.garmin.com/xmlschemas/ActivityExtension/v2"

    activity_el = root.find(".//tcx:Activity", ns)
    if activity_el is None:
        raise ValueError("No activity found in TCX file")

    sport_type = (activity_el.get("Sport") or "").lower().replace(" ", "_") or None

    # Sum stats across all laps
    total_time = 0.0
    total_distance = 0.0
    total_calories = 0
    points = []
    hr_values = []
    cad_values = []
    power_values = []

    for lap in activity_el.findall("tcx:Lap", ns):
        time_val = lap.find("tcx:TotalTimeSeconds", ns)
        if time_val is not None:
            total_time += float(time_val.text)

        dist_val = lap.find("tcx:DistanceMeters", ns)
        if dist_val is not None:
            total_distance += float(dist_val.text)

        cal_val = lap.find("tcx:Calories", ns)
        if cal_val is not None:
            total_calories += int(cal_val.text)

        for tp in lap.findall(".//tcx:Trackpoint", ns):
            pt = _parse_tcx_trackpoint(tp, ns, ext_ns)
            if pt.get("lat") is not None and pt.get("lon") is not None:
                points.append(pt)
            if pt.get("hr") is not None:
                hr_values.append(pt["hr"])
            if pt.get("cad") is not None:
                cad_values.append(pt["cad"])
            if pt.get("power") is not None:
                power_values.append(pt["power"])

    if not points:
        raise ValueError("No track points with position data found in TCX file")

    # Compute elevation gain from track points
    elevation_gain = _compute_elevation_gain(points)

    start_time = points[0].get("time")
    started_at = start_time.isoformat() if start_time else datetime.now(UTC).isoformat()

    route_points = [(p["lat"], p["lon"]) for p in points]
    encoded_polyline = polyline.encode(route_points) if route_points else None

    activity = _build_activity(
        sport_type=sport_type,
        start_time=start_time,
        started_at=started_at,
        elapsed_time=total_time,
        moving_time=total_time,
        distance=total_distance,
        source="tcx-file",
        elevation_gain=elevation_gain,
        calories=total_calories if total_calories > 0 else None,
        avg_heart_rate=sum(hr_values) / len(hr_values) if hr_values else None,
        max_heart_rate=max(hr_values) if hr_values else None,
        avg_cadence=sum(cad_values) / len(cad_values) if cad_values else None,
        max_cadence=max(cad_values) if cad_values else None,
        avg_power=sum(power_values) / len(power_values) if power_values else None,
        max_power=max(power_values) if power_values else None,
        encoded_polyline=encoded_polyline,
    )

    return activity


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _generate_title(sport_type, start_time) -> str:
    """Generate a default title like 'Morning Ride' from sport type and time of day."""
    sport_display_names = {"cycling": "Ride", "walking": "Walk", "hiking": "Hike"}
    if not sport_type or sport_type == "unknown":
        sport_display = "Activity"
    else:
        sport_display = sport_display_names.get(
            str(sport_type), str(sport_type).replace("_", " ").title()
        )

    if isinstance(start_time, datetime):
        hour = start_time.hour
    else:
        hour = 12

    if hour < 12:
        time_of_day = "Morning"
    elif hour < 17:
        time_of_day = "Afternoon"
    else:
        time_of_day = "Evening"

    return f"{time_of_day} {sport_display}"


def _normalize_sport_type(sport_type: str | None) -> str:
    """Normalize sport type labels to FIT canonical values.

    Different file formats use different names for the same sport (e.g. TCX uses
    "Biking" while FIT uses "cycling"). We normalize to FIT values so the database
    stays consistent regardless of upload format.
    """
    if not sport_type:
        return "unknown"

    normalized = str(sport_type).lower().replace(" ", "_")

    synonyms = {
        "biking": "cycling",
        "ride": "cycling",
        "virtualride": "cycling",
        "ebikeride": "cycling",
        "e-bike_ride": "cycling",
        "mountainbikeride": "cycling",
        "mountain_bike_ride": "cycling",
        "gravelride": "cycling",
        "gravel_ride": "cycling",
        "run": "running",
        "virtualrun": "running",
        "trailrun": "running",
        "trail_run": "running",
        "walk": "walking",
        "hike": "hiking",
        "swim": "swimming",
        "workout": "workout",
        "weighttraining": "weight_training",
        "weight_training": "weight_training",
        "yoga": "yoga",
        "rock_climbing": "rock_climbing",
        "rockclimbing": "rock_climbing",
    }

    return synonyms.get(normalized, normalized)


def _build_activity(
    *,
    sport_type,
    start_time,
    started_at,
    elapsed_time,
    moving_time,
    distance,
    source,
    elevation_gain=None,
    avg_speed=None,
    max_speed=None,
    avg_heart_rate=None,
    max_heart_rate=None,
    avg_cadence=None,
    max_cadence=None,
    avg_power=None,
    max_power=None,
    calories=None,
    encoded_polyline=None,
    device_name=None,
) -> dict:
    """Build the activity dict with required and optional fields."""
    sport_type = _normalize_sport_type(sport_type)
    activity = {
        "title": _generate_title(sport_type, start_time),
        "sport_type": sport_type,
        "started_at": started_at,
        "elapsed_time": int(elapsed_time) if elapsed_time else 0,
        "moving_time": int(moving_time) if moving_time else 0,
        "distance": str(round(distance, 1)) if distance else "0",
        "source": source,
        "created_at": datetime.now(UTC).isoformat(),
    }

    if elevation_gain is not None:
        activity["elevation_gain"] = str(round(float(elevation_gain), 1))
    if avg_speed is not None:
        activity["avg_speed"] = str(round(float(avg_speed), 3))
    if max_speed is not None:
        activity["max_speed"] = str(round(float(max_speed), 3))
    if avg_heart_rate is not None:
        activity["avg_heart_rate"] = int(avg_heart_rate)
    if max_heart_rate is not None:
        activity["max_heart_rate"] = int(max_heart_rate)
    if avg_cadence is not None:
        activity["avg_cadence"] = int(avg_cadence)
    if max_cadence is not None:
        activity["max_cadence"] = int(max_cadence)
    if avg_power is not None:
        activity["avg_power"] = int(avg_power)
    if max_power is not None:
        activity["max_power"] = int(max_power)
    if calories is not None:
        activity["calories"] = int(calories)
    if encoded_polyline:
        activity["polyline"] = encoded_polyline
    if device_name:
        activity["device"] = device_name

    return activity


def _gpx_namespaces(root) -> dict:
    """Extract the default GPX namespace from the root element."""
    tag = root.tag
    if tag.startswith("{"):
        default_ns = tag[1 : tag.index("}")]
    else:
        default_ns = "http://www.topografix.com/GPX/1/1"
    return {
        "gpx": default_ns,
        "gpxtpx": "http://www.garmin.com/xmlschemas/TrackPointExtension/v1",
    }


def _parse_iso_time(text: str) -> datetime | None:
    """Parse an ISO 8601 timestamp, returning a timezone-aware datetime or None."""
    if not text:
        return None
    text = text.strip()
    # Python's fromisoformat handles most formats, but some GPX/TCX files use
    # trailing Z instead of +00:00
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse_gpx_trackpoint(trkpt, ns: dict) -> dict:
    """Extract data from a single GPX <trkpt> element."""
    lat = float(trkpt.get("lat"))
    lon = float(trkpt.get("lon"))

    time_el = trkpt.find("gpx:time", ns)
    time = _parse_iso_time(time_el.text) if time_el is not None else None

    ele_el = trkpt.find("gpx:ele", ns)
    ele = float(ele_el.text) if ele_el is not None else None

    # Garmin TrackPointExtension values
    hr = None
    cad = None
    power = None
    ext = trkpt.find(".//gpxtpx:TrackPointExtension", ns)
    if ext is not None:
        hr_el = ext.find("gpxtpx:hr", ns)
        if hr_el is not None:
            hr = int(hr_el.text)
        cad_el = ext.find("gpxtpx:cad", ns)
        if cad_el is not None:
            cad = int(cad_el.text)

    return {"lat": lat, "lon": lon, "time": time, "ele": ele, "hr": hr, "cad": cad, "power": power}


def _parse_tcx_trackpoint(tp, ns: dict, ext_ns: str) -> dict:
    """Extract data from a single TCX <Trackpoint> element."""
    time_el = tp.find("tcx:Time", ns)
    time = _parse_iso_time(time_el.text) if time_el is not None else None

    pos = tp.find("tcx:Position", ns)
    lat = None
    lon = None
    if pos is not None:
        lat_el = pos.find("tcx:LatitudeDegrees", ns)
        lon_el = pos.find("tcx:LongitudeDegrees", ns)
        if lat_el is not None and lon_el is not None:
            lat = float(lat_el.text)
            lon = float(lon_el.text)

    alt_el = tp.find("tcx:AltitudeMeters", ns)
    ele = float(alt_el.text) if alt_el is not None else None

    hr_el = tp.find("tcx:HeartRateBpm/tcx:Value", ns)
    hr = int(hr_el.text) if hr_el is not None else None

    cad_el = tp.find("tcx:Cadence", ns)
    cad = int(cad_el.text) if cad_el is not None else None

    # Power from Garmin ActivityExtension/v2
    power = None
    for ext_el in tp.findall(f".//{{{ext_ns}}}Watts"):
        power = int(ext_el.text)
        break

    return {"lat": lat, "lon": lon, "time": time, "ele": ele, "hr": hr, "cad": cad, "power": power}


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute the great-circle distance in meters between two lat/lon points."""
    r = 6_371_000  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _compute_distance(points: list[dict]) -> float:
    """Sum haversine distances between consecutive points. Returns meters."""
    total = 0.0
    for i in range(1, len(points)):
        total += _haversine(
            points[i - 1]["lat"], points[i - 1]["lon"],
            points[i]["lat"], points[i]["lon"],
        )
    return total


def _compute_elevation_gain(points: list[dict]) -> float | None:
    """Sum positive elevation changes between consecutive points. Returns meters or None."""
    elevations = [p["ele"] for p in points if p.get("ele") is not None]
    if len(elevations) < 2:
        return None
    gain = 0.0
    for i in range(1, len(elevations)):
        diff = elevations[i] - elevations[i - 1]
        if diff > 0:
            gain += diff
    return gain
