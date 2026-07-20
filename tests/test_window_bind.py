from palworld_companion.window_bind import (
    GameWindowState,
    OverlayAnchor,
    WindowRect,
    overlay_anchor,
    overlay_position,
)


def test_default_anchor_tracks_top_right_across_move_and_resize():
    first = WindowRect(100, 200, 1700, 1100)
    moved_and_resized = WindowRect(2100, 100, 3380, 820)

    assert overlay_position(first, (340, 240), OverlayAnchor()) == (1340, 220)
    assert overlay_position(moved_and_resized, (340, 240), OverlayAnchor()) == (3020, 120)


def test_dragged_anchor_round_trips_without_storing_monitor_coordinates():
    rect = WindowRect(100, 200, 1700, 1100)
    position = (620, 590)

    anchor = overlay_anchor(rect, (340, 240), position)

    assert overlay_position(rect, (340, 240), anchor) == position
    assert 0 < anchor.x_ratio < 1
    assert 0 < anchor.y_ratio < 1


def test_small_client_rect_clamps_overlay_to_a_stable_position():
    rect = WindowRect(50, 80, 300, 220)

    assert overlay_position(rect, (340, 240), OverlayAnchor(0.25, 0.75)) == (50, 80)


def test_window_state_fails_closed_when_minimized_or_not_foreground():
    rect = WindowRect(0, 0, 1920, 1080)

    assert GameWindowState(rect).displayable is True
    assert GameWindowState(rect, minimized=True).displayable is False
    assert GameWindowState(rect, foreground=False).displayable is False
    assert GameWindowState(WindowRect(0, 0, 0, 0)).displayable is False
