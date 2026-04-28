import csv
import gzip
from pathlib import Path

import pytest

from app.parse import _normalize_sport_type, parse_file
from app.strava import (
    build_activity_from_strava_csv,
    merge_csv_metadata,
    parse_strava_csv,
    parse_strava_date,
    safe_float,
)

FIXTURES = Path(__file__).parent / "fixtures"


_CSV_HEADERS = [
    "Activity ID", "Activity Date", "Activity Name", "Activity Type",
    "Activity Description", "Elapsed Time", "Distance", "Max Heart Rate",
    "Relative Effort", "Commute", "Activity Private Note", "Activity Gear",
    "Filename", "Athlete Weight", "Bike Weight",
    "Elapsed Time", "Moving Time", "Distance", "Max Speed", "Average Speed",
    "Elevation Gain", "Elevation Loss", "Elevation Low", "Elevation High",
    "Max Grade", "Average Grade", "Average Positive Grade",
    "Average Negative Grade", "Max Cadence", "Average Cadence",
    "Max Heart Rate", "Average Heart Rate", "Max Watts", "Average Watts",
    "Calories", "Max Temperature", "Average Temperature", "Relative Effort",
    "Total Work", "Number of Runs", "Uphill Time", "Downhill Time",
    "Other Time", "Perceived Exertion", "Type", "Start Time",
    "Weighted Average Power", "Power Count", "Prefer Perceived Exertion",
    "Perceived Relative Effort", "Commute", "Total Weight Lifted",
    "From Upload", "Grade Adjusted Distance", "Weather Observation Time",
    "Weather Condition", "Weather Temperature", "Apparent Temperature",
    "Dewpoint", "Humidity", "Weather Pressure", "Wind Speed", "Wind Gust",
    "Wind Bearing", "Precipitation Intensity", "Sunrise Time", "Sunset Time",
    "Moon Phase", "Bike", "Gear", "Precipitation Probability",
    "Precipitation Type", "Cloud Cover", "Weather Visibility", "UV Index",
    "Weather Ozone", "Jump Count", "Total Grit", "Average Flow", "Flagged",
    "Average Elapsed Speed", "Dirt Distance", "Newly Explored Distance",
    "Newly Explored Dirt Distance", "Activity Count", "Total Steps",
    "Carbon Saved", "Pool Length", "Training Load", "Intensity",
    "Average Grade Adjusted Pace", "Timer Time", "Total Cycles", "Recovery",
    "With Pet", "Competition", "Long Run", "For a Cause", "With Kid",
    "Downhill Distance", "Total Sets", "Total Reps", "Media",
]


def _make_csv(rows: list[list[str]]) -> str:
    """Build a properly-quoted CSV string from a header + data rows.

    Uses csv.writer to handle quoting of fields that contain commas (like dates).
    """
    from io import StringIO as _StringIO
    output = _StringIO()
    writer = csv.writer(output)
    writer.writerow(_CSV_HEADERS)
    for row in rows:
        padded = row + [""] * (len(_CSV_HEADERS) - len(row))
        writer.writerow(padded)
    return output.getvalue()


def _make_ride_row(overrides=None):
    """Build a single CSV row list for a ride activity with sensible defaults."""
    defaults = {
        0: "12345",                           # Activity ID
        1: "Apr 1, 2026, 6:18:55 PM",        # Activity Date
        2: "First Ride Since Winter",         # Activity Name
        3: "Ride",                            # Activity Type
        4: "Beautiful day for a ride",        # Activity Description
        12: "activities/12345.fit.gz",        # Filename
        15: "6920.0",                         # Elapsed Time (detailed)
        16: "5241.0",                         # Moving Time
        17: "29292.6",                        # Distance (detailed, meters)
        18: "11.983",                         # Max Speed
        19: "5.589",                          # Average Speed
        20: "85.0",                           # Elevation Gain
        28: "",                               # Max Cadence
        29: "",                               # Average Cadence
        30: "205.0",                          # Max Heart Rate (detailed)
        31: "169.0",                          # Average Heart Rate
        32: "",                               # Max Watts
        33: "80.0",                           # Average Watts
        34: "1319.0",                         # Calories
        102: "",                              # Media
    }
    if overrides:
        defaults.update(overrides)
    row = [""] * 103
    for idx, val in defaults.items():
        row[idx] = val
    return row


def _make_workout_row(overrides=None):
    """Build a CSV row for a workout (no distance, no GPS)."""
    defaults = {
        0: "67890",
        1: "Dec 21, 2025, 7:05:39 PM",
        2: "Afternoon Workout",
        3: "Workout",
        4: "",
        12: "activities/67890.fit.gz",
        15: "1850.0",
        16: "1850.0",
        17: "0.0",
        18: "0.0",
        19: "0.0",
        20: "0.0",
        30: "180.0",
        31: "138.0",
        34: "360.0",
    }
    if overrides:
        defaults.update(overrides)
    row = [""] * 103
    for idx, val in defaults.items():
        row[idx] = val
    return row


# ---------------------------------------------------------------------------
# Strava date parsing
# ---------------------------------------------------------------------------

class TestParseStravaDate:
    def test_standard_format(self):
        result = parse_strava_date("Apr 1, 2026, 6:18:55 PM")
        assert result is not None
        assert "2026-04-01" in result
        assert "T" in result

    def test_morning_time(self):
        result = parse_strava_date("Dec 21, 2025, 7:05:39 AM")
        assert result is not None
        assert "07:05:39" in result

    def test_empty_string(self):
        assert parse_strava_date("") is None

    def test_none(self):
        assert parse_strava_date(None) is None

    def test_invalid_format(self):
        assert parse_strava_date("not a date") is None


# ---------------------------------------------------------------------------
# safe_float
# ---------------------------------------------------------------------------

class TestSafeFloat:
    def test_valid_number(self):
        assert safe_float("123.45") == 123.45

    def test_integer_string(self):
        assert safe_float("100") == 100.0

    def test_zero_returns_none(self):
        assert safe_float("0.0") is None
        assert safe_float("0") is None

    def test_empty_string(self):
        assert safe_float("") is None

    def test_whitespace(self):
        assert safe_float("  ") is None

    def test_none(self):
        assert safe_float(None) is None

    def test_invalid(self):
        assert safe_float("abc") is None

    def test_strips_whitespace(self):
        assert safe_float("  42.5  ") == 42.5


# ---------------------------------------------------------------------------
# Sport type mapping (Strava-specific additions)
# ---------------------------------------------------------------------------

class TestNormalizeSportTypeStrava:
    def test_ride(self):
        assert _normalize_sport_type("Ride") == "cycling"

    def test_run(self):
        assert _normalize_sport_type("Run") == "running"

    def test_walk(self):
        assert _normalize_sport_type("Walk") == "walking"

    def test_hike(self):
        assert _normalize_sport_type("Hike") == "hiking"

    def test_swim(self):
        assert _normalize_sport_type("Swim") == "swimming"

    def test_workout(self):
        assert _normalize_sport_type("Workout") == "workout"

    def test_trail_run(self):
        assert _normalize_sport_type("Trail Run") == "running"

    def test_mountain_bike_ride(self):
        assert _normalize_sport_type("Mountain Bike Ride") == "cycling"

    def test_e_bike_ride(self):
        assert _normalize_sport_type("E-Bike Ride") == "cycling"

    def test_gravel_ride(self):
        assert _normalize_sport_type("Gravel Ride") == "cycling"

    def test_virtual_ride(self):
        assert _normalize_sport_type("VirtualRide") == "cycling"

    def test_virtual_run(self):
        assert _normalize_sport_type("VirtualRun") == "running"

    def test_weight_training(self):
        assert _normalize_sport_type("Weight Training") == "weight_training"

    def test_unknown_type_passes_through(self):
        assert _normalize_sport_type("Kayaking") == "kayaking"

    def test_existing_biking_still_works(self):
        assert _normalize_sport_type("Biking") == "cycling"


# ---------------------------------------------------------------------------
# parse_strava_csv
# ---------------------------------------------------------------------------

class TestParseStravaCsv:
    def test_parses_rows(self):
        csv_data = _make_csv([_make_ride_row(), _make_workout_row()])
        rows = parse_strava_csv(csv_data)
        assert len(rows) == 2

    def test_extracts_correct_fields(self):
        csv_data = _make_csv([_make_ride_row()])
        rows = parse_strava_csv(csv_data)
        row = rows[0]
        assert row["activity_id"] == "12345"
        assert row["activity_name"] == "First Ride Since Winter"
        assert row["activity_type"] == "Ride"
        assert row["filename"] == "activities/12345.fit.gz"
        assert row["distance"] == "29292.6"
        assert row["elapsed_time"] == "6920.0"

    def test_handles_duplicate_column_names(self):
        """The detailed Distance (col 17) should be used, not display Distance (col 6)."""
        csv_data = _make_csv([_make_ride_row()])
        rows = parse_strava_csv(csv_data)
        row = rows[0]
        assert row["distance"] == "29292.6"

    def test_empty_csv(self):
        header_only = _make_csv([])
        rows = parse_strava_csv(header_only)
        assert rows == []

    def test_strips_whitespace(self):
        csv_data = _make_csv([_make_ride_row({2: "  Afternoon Ride  "})])
        rows = parse_strava_csv(csv_data)
        assert rows[0]["activity_name"] == "Afternoon Ride"

    def test_skips_short_rows(self):
        csv_data = _make_csv([_make_ride_row()])
        # Truncate the last row to make it too short
        lines = csv_data.split("\n")
        lines[1] = ",".join(lines[1].split(",")[:10])
        csv_data = "\n".join(lines)
        rows = parse_strava_csv(csv_data)
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# build_activity_from_strava_csv
# ---------------------------------------------------------------------------

class TestBuildActivityFromStravaCsv:
    @pytest.fixture()
    def ride_row(self):
        csv_data = _make_csv([_make_ride_row()])
        return parse_strava_csv(csv_data)[0]

    @pytest.fixture()
    def workout_row(self):
        csv_data = _make_csv([_make_workout_row()])
        return parse_strava_csv(csv_data)[0]

    def test_required_fields_present(self, ride_row):
        activity = build_activity_from_strava_csv(ride_row)
        for field in ["title", "sport_type", "started_at", "elapsed_time",
                      "moving_time", "distance", "source", "created_at"]:
            assert field in activity, f"Missing required field: {field}"

    def test_source_is_strava(self, ride_row):
        activity = build_activity_from_strava_csv(ride_row)
        assert activity["source"] == "strava"

    def test_title_from_csv(self, ride_row):
        activity = build_activity_from_strava_csv(ride_row)
        assert activity["title"] == "First Ride Since Winter"

    def test_sport_type_normalized(self, ride_row):
        activity = build_activity_from_strava_csv(ride_row)
        assert activity["sport_type"] == "cycling"

    def test_distance_as_string(self, ride_row):
        activity = build_activity_from_strava_csv(ride_row)
        assert isinstance(activity["distance"], str)
        assert float(activity["distance"]) == 29292.6

    def test_elapsed_time_as_int(self, ride_row):
        activity = build_activity_from_strava_csv(ride_row)
        assert isinstance(activity["elapsed_time"], int)
        assert activity["elapsed_time"] == 6920

    def test_description_included(self, ride_row):
        activity = build_activity_from_strava_csv(ride_row)
        assert activity["description"] == "Beautiful day for a ride"

    def test_calories_included(self, ride_row):
        activity = build_activity_from_strava_csv(ride_row)
        assert activity["calories"] == 1319

    def test_heart_rate_included(self, ride_row):
        activity = build_activity_from_strava_csv(ride_row)
        assert activity["avg_heart_rate"] == 169
        assert activity["max_heart_rate"] == 205

    def test_workout_with_zero_distance(self, workout_row):
        activity = build_activity_from_strava_csv(workout_row)
        assert activity["distance"] == "0"
        assert activity["sport_type"] == "workout"

    def test_no_description_when_empty(self):
        csv_data = _make_csv([_make_ride_row({4: ""})])
        row = parse_strava_csv(csv_data)[0]
        activity = build_activity_from_strava_csv(row)
        assert "description" not in activity


# ---------------------------------------------------------------------------
# merge_csv_metadata
# ---------------------------------------------------------------------------

class TestMergeCsvMetadata:
    @pytest.fixture()
    def csv_row(self):
        csv_data = _make_csv([_make_ride_row()])
        return parse_strava_csv(csv_data)[0]

    @pytest.fixture()
    def file_activity(self):
        """Simulates what parse_file would return for a FIT/GPX file."""
        return {
            "title": "Evening Ride",
            "sport_type": "cycling",
            "started_at": "2026-04-01T18:18:55+00:00",
            "elapsed_time": 6920,
            "moving_time": 5241,
            "distance": "29292.6",
            "source": "fit-file",
            "created_at": "2026-04-28T12:00:00+00:00",
            "elevation_gain": "85.0",
            "avg_speed": "5.589",
            "max_speed": "11.983",
            "polyline": "abc123encoded",
        }

    def test_title_overridden_by_csv(self, file_activity, csv_row):
        merged = merge_csv_metadata(file_activity, csv_row)
        assert merged["title"] == "First Ride Since Winter"

    def test_source_set_to_strava(self, file_activity, csv_row):
        merged = merge_csv_metadata(file_activity, csv_row)
        assert merged["source"] == "strava"

    def test_description_added(self, file_activity, csv_row):
        merged = merge_csv_metadata(file_activity, csv_row)
        assert merged["description"] == "Beautiful day for a ride"

    def test_polyline_preserved(self, file_activity, csv_row):
        merged = merge_csv_metadata(file_activity, csv_row)
        assert merged["polyline"] == "abc123encoded"

    def test_file_stats_not_overwritten(self, file_activity, csv_row):
        """File-parsed stats should take precedence over CSV stats."""
        merged = merge_csv_metadata(file_activity, csv_row)
        assert merged["elevation_gain"] == "85.0"
        assert merged["avg_speed"] == "5.589"

    def test_missing_stats_filled_from_csv(self, file_activity, csv_row):
        """Stats not present in file output should come from CSV."""
        merged = merge_csv_metadata(file_activity, csv_row)
        assert merged["calories"] == 1319
        assert merged["avg_heart_rate"] == 169
        assert merged["max_heart_rate"] == 205

    def test_does_not_mutate_original(self, file_activity, csv_row):
        original_title = file_activity["title"]
        merge_csv_metadata(file_activity, csv_row)
        assert file_activity["title"] == original_title

    def test_empty_description_not_added(self, file_activity):
        csv_data = _make_csv([_make_ride_row({4: ""})])
        row = parse_strava_csv(csv_data)[0]
        merged = merge_csv_metadata(file_activity, row)
        assert "description" not in merged


# ---------------------------------------------------------------------------
# Gzip FIT support in parse_file
# ---------------------------------------------------------------------------

class TestParseFileGzip:
    def test_fit_gz_dispatches_to_fit_parser(self):
        fit_data = (FIXTURES / "ride.fit").read_bytes()
        gz_data = gzip.compress(fit_data)
        result = parse_file("ride.fit.gz", gz_data)
        assert result["source"] == "fit-file"
        assert result["sport_type"] != "unknown"

    def test_fit_gz_produces_same_result_as_fit(self):
        fit_data = (FIXTURES / "ride.fit").read_bytes()
        gz_data = gzip.compress(fit_data)

        from_fit = parse_file("ride.fit", fit_data)
        from_gz = parse_file("ride.fit.gz", gz_data)

        # created_at will differ slightly, so compare everything else
        for key in from_fit:
            if key == "created_at":
                continue
            assert from_fit[key] == from_gz[key], f"Mismatch on {key}"
