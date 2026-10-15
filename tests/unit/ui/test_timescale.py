from fractions import Fraction

from easy_video_editor.ui.timescale import MIN_US_PER_PX, TimeScale, drag_range, hit_test, snap

S = 1_000_000


def scale() -> TimeScale:
    result = TimeScale(duration_us=10 * S, width=1000)
    result.fit()
    return result


def test_fit_and_conversions() -> None:
    s = scale()
    assert s.us_per_px == 10_000
    assert s.x(5 * S) == 500
    assert s.us(250) == 2_500_000


def test_zoom_keeps_anchor_and_clamps() -> None:
    s = scale()
    s.zoom(2.0, anchor_x=500)
    assert s.us_per_px == 5_000
    assert s.us(500) == 5 * S
    for _ in range(50):
        s.zoom(2.0, anchor_x=0)
    assert s.us_per_px == MIN_US_PER_PX
    for _ in range(50):
        s.zoom(0.5, anchor_x=0)
    assert s.us_per_px == s.max_us_per_px and s.start_us == 0


def test_scroll_is_clamped() -> None:
    s = scale()
    s.zoom(4.0, 0)
    s.scroll(-1000)
    assert s.start_us == 0
    s.scroll(10_000)
    assert s.start_us == 10 * S - 1000 * s.us_per_px


def test_snap_prefers_edges_then_frames() -> None:
    s = scale()
    assert snap(3_020_000, s, Fraction(25), targets=[3_000_000]) == 3_000_000
    assert snap(3_020_000, s, Fraction(25), targets=[9 * S]) == 3_040_000
    assert snap(-5, s, None) == 0
    assert snap(11 * S, s, None) == 10 * S


def test_hit_test_edges_and_body() -> None:
    s = scale()
    ranges = [(1 * S, 3 * S), (5 * S, 6 * S)]
    assert hit_test(ranges, s, 102) is not None and hit_test(ranges, s, 102).part == "start"  # type: ignore[union-attr]
    assert hit_test(ranges, s, 298).part == "end"  # type: ignore[union-attr]
    assert hit_test(ranges, s, 200).part == "body"  # type: ignore[union-attr]
    assert hit_test(ranges, s, 550).index == 1  # type: ignore[union-attr]
    assert hit_test(ranges, s, 450) is None


def test_drag_range_limits() -> None:
    assert drag_range(2 * S, 4 * S, "body", -5 * S, 10 * S, S // 10) == (0, 2 * S)
    assert drag_range(2 * S, 4 * S, "body", 9 * S, 10 * S, S // 10) == (8 * S, 10 * S)
    assert drag_range(2 * S, 4 * S, "start", 3 * S, 10 * S, S // 10) == (4 * S - S // 10, 4 * S)
    assert drag_range(2 * S, 4 * S, "end", 9 * S, 10 * S, S // 10) == (2 * S, 10 * S)
