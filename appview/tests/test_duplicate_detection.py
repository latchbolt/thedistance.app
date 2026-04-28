from app.db import find_duplicates_in_list


def _activity(sport_type="cycling", started_at="2026-04-01T18:18:55+00:00",
              distance="29292.6", elapsed_time=6920):
    return {
        "sport_type": sport_type,
        "started_at": started_at,
        "distance": distance,
        "elapsed_time": elapsed_time,
    }


class TestFindDuplicatesInList:
    def test_exact_match(self):
        needle = _activity()
        candidates = [_activity()]
        assert len(find_duplicates_in_list(needle, candidates)) == 1

    def test_no_candidates(self):
        needle = _activity()
        assert find_duplicates_in_list(needle, []) == []

    def test_different_sport_type(self):
        needle = _activity(sport_type="cycling")
        candidates = [_activity(sport_type="running")]
        assert find_duplicates_in_list(needle, candidates) == []

    def test_time_within_window(self):
        needle = _activity(started_at="2026-04-01T18:18:55+00:00")
        candidates = [_activity(started_at="2026-04-01T18:19:30+00:00")]
        assert len(find_duplicates_in_list(needle, candidates)) == 1

    def test_time_outside_window(self):
        needle = _activity(started_at="2026-04-01T18:18:55+00:00")
        candidates = [_activity(started_at="2026-04-01T18:20:56+00:00")]
        assert find_duplicates_in_list(needle, candidates) == []

    def test_time_at_boundary(self):
        needle = _activity(started_at="2026-04-01T18:18:55+00:00")
        candidates = [_activity(started_at="2026-04-01T18:19:55+00:00")]
        assert len(find_duplicates_in_list(needle, candidates)) == 1

    def test_time_just_past_boundary(self):
        needle = _activity(started_at="2026-04-01T18:18:55+00:00")
        candidates = [_activity(started_at="2026-04-01T18:19:56+00:00")]
        assert find_duplicates_in_list(needle, candidates) == []

    def test_distance_within_tolerance(self):
        needle = _activity(distance="29292.6")
        candidates = [_activity(distance="29100.0")]
        assert len(find_duplicates_in_list(needle, candidates)) == 1

    def test_distance_outside_tolerance(self):
        needle = _activity(distance="29292.6")
        candidates = [_activity(distance="25000.0")]
        assert find_duplicates_in_list(needle, candidates) == []

    def test_distance_both_zero(self):
        """Zero-distance activities (e.g. workouts) should match each other."""
        needle = _activity(distance="0", elapsed_time=1850)
        candidates = [_activity(distance="0", elapsed_time=1850)]
        assert len(find_duplicates_in_list(needle, candidates)) == 1

    def test_distance_one_zero_one_not(self):
        needle = _activity(distance="0")
        candidates = [_activity(distance="29292.6")]
        assert find_duplicates_in_list(needle, candidates) == []

    def test_elapsed_time_within_tolerance(self):
        needle = _activity(elapsed_time=6920)
        candidates = [_activity(elapsed_time=6950)]
        assert len(find_duplicates_in_list(needle, candidates)) == 1

    def test_elapsed_time_outside_tolerance(self):
        needle = _activity(elapsed_time=6920)
        candidates = [_activity(elapsed_time=7100)]
        assert find_duplicates_in_list(needle, candidates) == []

    def test_multiple_candidates_mixed(self):
        needle = _activity()
        candidates = [
            _activity(),
            _activity(sport_type="running"),
            _activity(distance="1000.0"),
        ]
        matches = find_duplicates_in_list(needle, candidates)
        assert len(matches) == 1

    def test_distance_as_float(self):
        needle = _activity(distance=29292.6)
        candidates = [_activity(distance=29292.6)]
        assert len(find_duplicates_in_list(needle, candidates)) == 1

    def test_custom_tolerances(self):
        needle = _activity(started_at="2026-04-01T18:18:55+00:00")
        candidates = [_activity(started_at="2026-04-01T18:21:55+00:00")]
        assert find_duplicates_in_list(needle, candidates, time_window=60) == []
        assert len(find_duplicates_in_list(needle, candidates, time_window=300)) == 1

    def test_z_suffix_timestamps(self):
        needle = _activity(started_at="2026-04-01T18:18:55Z")
        candidates = [_activity(started_at="2026-04-01T18:18:55+00:00")]
        assert len(find_duplicates_in_list(needle, candidates)) == 1
