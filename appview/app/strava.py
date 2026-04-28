import csv
import logging
from datetime import UTC, datetime
from io import StringIO

from app.parse import _build_activity, _normalize_sport_type, _parse_iso_time

log = logging.getLogger(__name__)

# The Strava export CSV has duplicate column names (e.g. two "Elapsed Time"
# columns). The first set are display-friendly values, the second are precise.
# We use positional indices to disambiguate.
STRAVA_CSV_COLUMNS = {
    "activity_id": 0,
    "activity_date": 1,
    "activity_name": 2,
    "activity_type": 3,
    "activity_description": 4,
    "filename": 12,
    "elapsed_time": 15,
    "moving_time": 16,
    "distance": 17,
    "max_speed": 18,
    "avg_speed": 19,
    "elevation_gain": 20,
    "max_cadence": 28,
    "avg_cadence": 29,
    "max_heart_rate": 30,
    "avg_heart_rate": 31,
    "max_watts": 32,
    "avg_watts": 33,
    "calories": 34,
    "start_time": 45,
    "media": 102,
}


def parse_strava_csv(csv_data: str) -> list[dict]:
    """Parse a Strava export activities.csv into a list of row dicts.

    Each dict uses our internal field names (see STRAVA_CSV_COLUMNS) with
    values as raw strings. Callers can pass individual rows to
    build_activity_from_strava_csv() or merge_csv_metadata().
    """
    reader = csv.reader(StringIO(csv_data))
    next(reader)  # skip header row

    max_col = max(STRAVA_CSV_COLUMNS.values())
    rows = []
    for line_num, raw_row in enumerate(reader, start=2):
        if len(raw_row) <= max_col:
            log.warning("Strava CSV line %d: expected at least %d columns, got %d -- skipping",
                        line_num, max_col + 1, len(raw_row))
            continue

        row = {}
        for field_name, col_idx in STRAVA_CSV_COLUMNS.items():
            row[field_name] = raw_row[col_idx].strip()
        rows.append(row)

    return rows


def build_activity_from_strava_csv(row: dict) -> dict:
    """Build an activity dict from a Strava CSV row alone (no backing file).

    Returns the same shape as _build_activity() output. Used when an activity
    has no GPX/FIT file in the export.
    """
    sport_type = _normalize_sport_type(row.get("activity_type"))

    started_at = parse_strava_date(row.get("activity_date"))
    start_time = _parse_iso_time(started_at) if started_at else None

    elapsed_time = safe_float(row.get("elapsed_time"))
    moving_time = safe_float(row.get("moving_time"))
    distance = safe_float(row.get("distance"))

    activity = _build_activity(
        sport_type=sport_type,
        start_time=start_time,
        started_at=started_at or datetime.now(UTC).isoformat(),
        elapsed_time=elapsed_time,
        moving_time=moving_time or elapsed_time,
        distance=distance,
        source="strava",
        elevation_gain=safe_float(row.get("elevation_gain")),
        avg_speed=safe_float(row.get("avg_speed")),
        max_speed=safe_float(row.get("max_speed")),
        avg_heart_rate=safe_float(row.get("avg_heart_rate")),
        max_heart_rate=safe_float(row.get("max_heart_rate")),
        avg_cadence=safe_float(row.get("avg_cadence")),
        max_cadence=safe_float(row.get("max_cadence")),
        avg_power=safe_float(row.get("avg_watts")),
        max_power=safe_float(row.get("max_watts")),
        calories=safe_float(row.get("calories")),
    )

    title = row.get("activity_name", "").strip()
    if title:
        activity["title"] = title

    description = row.get("activity_description", "").strip()
    if description:
        activity["description"] = description

    return activity


def merge_csv_metadata(file_activity: dict, csv_row: dict) -> dict:
    """Overlay Strava CSV metadata onto a file-parsed activity.

    The file parser (GPX/FIT) provides GPS tracks and core stats. The CSV
    provides the user-set title, description, and any stats the file parser
    didn't extract (e.g. calories). File-parsed values take precedence for
    stats that both sources have, since they come from the raw data.
    """
    merged = dict(file_activity)
    merged["source"] = "strava"

    title = csv_row.get("activity_name", "").strip()
    if title:
        merged["title"] = title

    description = csv_row.get("activity_description", "").strip()
    if description:
        merged["description"] = description

    # Fill in stats that the file parser didn't produce
    csv_stats = {
        "calories": safe_float(csv_row.get("calories")),
        "avg_heart_rate": safe_float(csv_row.get("avg_heart_rate")),
        "max_heart_rate": safe_float(csv_row.get("max_heart_rate")),
        "avg_cadence": safe_float(csv_row.get("avg_cadence")),
        "max_cadence": safe_float(csv_row.get("max_cadence")),
        "avg_power": safe_float(csv_row.get("avg_watts")),
        "max_power": safe_float(csv_row.get("max_watts")),
        "elevation_gain": safe_float(csv_row.get("elevation_gain")),
        "avg_speed": safe_float(csv_row.get("avg_speed")),
        "max_speed": safe_float(csv_row.get("max_speed")),
    }

    for key, csv_val in csv_stats.items():
        if csv_val is not None and key not in merged:
            if key in ("calories", "avg_heart_rate", "max_heart_rate",
                       "avg_cadence", "max_cadence", "avg_power", "max_power"):
                merged[key] = int(csv_val)
            else:
                merged[key] = str(round(csv_val, 3))

    return merged


def parse_strava_date(date_str: str | None) -> str | None:
    """Parse Strava's date format (e.g. 'Apr 19, 2026, 9:35:37 PM') to ISO 8601."""
    if not date_str or not date_str.strip():
        return None
    try:
        dt = datetime.strptime(date_str.strip(), "%b %d, %Y, %I:%M:%S %p")
        return dt.replace(tzinfo=UTC).isoformat()
    except ValueError:
        log.warning("Could not parse Strava date: %s", date_str)
        return None


def safe_float(value: str | None) -> float | None:
    """Convert a string to float, returning None for empty or invalid values."""
    if not value or not value.strip():
        return None
    try:
        result = float(value.strip())
        return result if result != 0.0 else None
    except ValueError:
        return None
