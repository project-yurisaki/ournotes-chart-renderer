"""Note semantics and independent left/right slide edge interpolation."""
from __future__ import annotations

import math

from .config import Palette
from .elements import Note


FLICKS = {40, 41, 42, 102}
TRACES = {60, 61, 62, 63, 104, 105}
HIDDEN = {80, 82, 122}
GUIDE_BOUNDARIES = {100, 103}


def note_kind(note: Note):
    if note.operation in FLICKS:
        return "flick"
    if note.operation in TRACES:
        return "trace"
    if note.operation in HIDDEN:
        return "hidden"
    if note.operation in GUIDE_BOUNDARIES:
        return "guide-boundary"
    if note.operation == 21:
        return "connection"
    return "tap"


def note_color(note: Note, palette: Palette):
    if note.critical:
        return palette.critical
    kind = note_kind(note)
    if kind == "flick":
        return {"left": palette.flick_left, "right": palette.flick_right}.get(note.direction, palette.flick_up)
    if note.operation == 21:
        return palette.trace
    if note.operation in (20, 22):
        return palette.slide_head
    return palette.trace if kind in ("trace", "guide-boundary") else palette.tap


def ease(kind, t):
    return t * t if kind == "in" else (2 - t) * t if kind == "out" else t


def edges_at(a: Note, b: Note, tick: float):
    progress = max(0, min(1, (tick - a.tick) / (b.tick - a.tick))) if b.tick != a.tick else 0
    left = a.pos + (b.pos - a.pos) * ease(a.ease[0], progress)
    right = a.pos + a.width + (b.pos + b.width - a.pos - a.width) * ease(a.ease[1], progress)
    return left, right


def curve_anchors(nodes, index):
    """Auto nodes sample a curve; they must not restart its edge easing.

    Keep endpoints, including native auto endpoints with inherited geometry.
    Only interior auto nodes are skipped when finding geometry anchors.
    """
    left, right = index, index + 1
    while left > 0 and nodes[left].auto:
        left -= 1
    while right < len(nodes) - 1 and nodes[right].auto:
        right += 1
    return nodes[left], nodes[right]


def sample_segment(a: Note, b: Note, start, end, pixel_extent, curve_step=5):
    if b.tick <= a.tick:
        return []
    lo, hi = max(start, a.tick), min(end, b.tick)
    if hi <= lo:
        return []
    count = max(1, math.ceil(pixel_extent / curve_step))
    return [(lo + (hi - lo) * index / count, *edges_at(a, b, lo + (hi - lo) * index / count))
            for index in range(count + 1)]
