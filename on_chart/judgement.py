"""Gameplay judgement instants, independently of preview/source node timing.

Reproduces MusicScoreNoteCreator and ComboCounter. This is the cumulative
perfect-play Combo; actual player input, misses and timing offsets are absent.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import math

from .elements import Chart
from .ss_format import ChartError, f32


NON_JUDGEMENT = frozenset((0, 80, 82, 100, 103, 121, 122, 123))
MERGEABLE_ENDPOINT = frozenset((20, 22, 41, 42, 61, 62, 80, 82, 100, 101, 102, 103, 104, 105))
MAX_JUDGEMENTS = 1_000_000


@dataclass(frozen=True)
class Judgement:
    time_ms: int
    operation: int
    source_path: str
    group_id: int | None


@dataclass(frozen=True)
class JudgementTimeline:
    entries: tuple[Judgement, ...]
    times_ms: tuple[int, ...]

    @property
    def full_combo(self) -> int:
        return len(self.entries)

    def combo_at(self, time_ms: int, *, inclusive=False) -> int:
        """Count instants strictly before time_ms, like GetTimingCombo.

        Set inclusive=True to query after all judgements at that millisecond.
        Simultaneous notes contribute separately; merged endpoints contribute once.
        """
        return (bisect_right if inclusive else bisect_left)(self.times_ms, time_ms)


class _RuntimeTiming:
    def __init__(self, chart):
        self.timing = chart.timing
        self.positions = {}
        self.times = {}
        self.bpms = [(self.position(e['t']), e['timeMs'], e['bpm'])
                     for e in self.timing.bpm_segments]
        self.sigs = [(self.position(e['t']), self.timing.time_ms(e['t']),
                      f32(f32(e['numerator'] * 4.0) / e['denominator']))
                     for e in self.timing.sig_segments]
        self.sig_bars = [p[0] for p, _, _ in self.sigs]
        self.bpm_times = [ms for _, ms, _ in self.bpms]

    def position(self, tick):
        if tick not in self.positions:
            p = self.timing.position(tick)
            self.positions[tick] = p['bar'], p['barProgress']
        return self.positions[tick]

    def time_ms(self, bar, progress):
        key = bar, progress
        if key in self.times:
            return self.times[key]
        origin, origin_ms, bpm, rhythm = (0, 0.0), 0, 120.0, 4.0
        # Native comparisons are strict. Signature and BPM positions also
        # supply the latest time origin, in addition to changing the rate.
        for p, ms, r in self.sigs:
            if p < key:
                rhythm = r
                if origin_ms < ms:
                    origin, origin_ms = p, ms
        for p, ms, value in self.bpms:
            if p < key:
                bpm = value
                if origin_ms < ms:
                    origin, origin_ms = p, ms
        seconds_per_bar = f32(f32(rhythm * 60.0) / bpm)
        delta = f32(f32(seconds_per_bar * f32(bar - origin[0])) +
                    f32(seconds_per_bar * f32(progress - origin[1])))
        result = origin_ms + math.floor(f32(delta * 1000.0))
        self.times[key] = result
        return result

    def node_time(self, note):
        return self.time_ms(*self.position(note.tick))

    def bar_rhythm(self, bar):
        index = bisect_right(self.sig_bars, bar) - 1
        return self.sigs[index][2] if index >= 0 else 4.0

    def candidates(self, start, end):
        bar, progress = self.position(start.tick)
        step = 1.0 / f32(2.0 * self.bar_rhythm(bar))
        end_ms = self.node_time(end)
        # Progress accumulates as double, then is cast to float for the
        # position and time conversion. Do not round it back to SS ticks.
        for _ in range(MAX_JUDGEMENTS):
            progress += step
            if progress >= 1.0:
                progress -= 1.0
                bar += 1
                step = 1.0 / f32(2.0 * self.bar_rhythm(bar))
            p = f32(progress)
            ms = self.time_ms(bar, p)
            if ms >= end_ms:
                return
            yield bar, p, ms
        raise ChartError('slide judgement count exceeds safety limit')


def _same_position(a, b):
    # Unity Mathf.Approximately, applied to float32 barProgress values.
    return a[0] == b[0] and abs(f32(a[1] - b[1])) < max(
        f32(f32(1e-6) * max(abs(a[1]), abs(b[1]))), 8 * 1.401298464324817e-45)


def build_judgements(chart: Chart) -> JudgementTimeline:
    """Build native source and generated slide judgements (Master not required)."""
    timing, entries, merged = _RuntimeTiming(chart), [], set()
    for n in chart.all_nodes:
        if n.consumed or n.operation in NON_JUDGEMENT:
            continue
        if n.operation in MERGEABLE_ENDPOINT:
            key = (timing.position(n.tick), round(n.pos), max(1, round(n.width)),
                   n.operation, n.direction, n.critical, n.ease)
            if key in merged:
                continue
            merged.add(key)
        entries.append(Judgement(timing.node_time(n), n.operation, n.source_path, n.group_id))
    for slide in chart.slides:
        # Guide ribbons have no automatic hold judgement. Their judged heads
        # and trace nodes, including absorbed singles, are counted above.
        if slide.kind == 'guide':
            continue
        begin, end = slide.nodes[0], slide.nodes[-1]
        if end.tick <= begin.tick:
            continue
        midpoints = [n for n in slide.nodes[1:-1] if n.operation not in NON_JUDGEMENT]
        boundaries = [begin, *midpoints, end]
        candidates = [p for a, b in zip(boundaries, boundaries[1:]) for p in timing.candidates(a, b)]
        existing = [timing.position(n.tick) for n in slide.nodes if n.operation not in NON_JUDGEMENT]
        midpoint_ms = [timing.node_time(n) for n in midpoints]
        begin_ms, end_ms = timing.node_time(begin), timing.node_time(end)
        for index, (bar, progress, ms) in enumerate(candidates):
            if any(_same_position((bar, progress), p) for p in existing):
                continue
            bpm_index = max(0, bisect_right(timing.bpm_times, ms) - 1)
            window = 15000.0 / timing.bpms[bpm_index][2]
            if any(ms < t <= ms + window for t in midpoint_ms):
                continue
            if index == 0 and ms - begin_ms < window:
                continue
            if index == len(candidates) - 1 and end_ms - ms < window:
                continue
            entries.append(Judgement(ms, 120, begin.source_path, slide.id))
            if len(entries) > MAX_JUDGEMENTS:
                raise ChartError('chart judgement count exceeds safety limit')
    ordered = tuple(sorted(entries, key=lambda e: e.time_ms))
    return JudgementTimeline(ordered, tuple(e.time_ms for e in ordered))
