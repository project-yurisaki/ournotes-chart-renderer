"""Height-based, measure-safe pages arranged in one horizontal row."""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import math

from .config import Config
from .elements import Chart
from .ss_format import ChartError, PPQ


@dataclass(frozen=True)
class Column:
    index: int
    start: int
    end: int
    left: float
    top: float
    bottom: float


@dataclass(frozen=True)
class Layout:
    width: int
    height: int
    columns: tuple[Column, ...]
    measures: tuple[int, ...]
    track_height: int


def axis(chart: Chart, config: Config, tick: float):
    if config.spacing == "time":
        return chart.timing.time_ms(tick) * config.pixels_per_second / 1000.0
    return tick * config.pixels_per_beat / PPQ


def measure_lines(chart: Chart, config: Config):
    segments = {segment["t"]: segment for segment in chart.timing.sig_segments}
    ticks = sorted(segments)
    first, last = min(0, chart.start_tick), chart.end_tick + PPQ // 2
    start_segment = segments[ticks[max(0, bisect_right(ticks, first) - 1)]]
    end_segment = segments[ticks[max(0, bisect_right(ticks, last) - 1)]]
    first = start_segment["t"] + ((first - start_segment["t"]) // start_segment["ticksPerMeasure"]) * start_segment["ticksPerMeasure"]
    last = end_segment["t"] + math.ceil((last - end_segment["t"]) / end_segment["ticksPerMeasure"]) * end_segment["ticksPerMeasure"]
    if (last - first) / PPQ > config.max_grid_lines:
        raise ChartError("chart exceeds preview grid limit; trim input or increase config.max_grid_lines")
    lines = {first, last}
    for index, tick in enumerate(ticks):
        segment = segments[tick]
        lo = max(first, tick if index else first)
        hi = min(last, ticks[index + 1] if index + 1 < len(ticks) else last)
        if lo > hi:
            continue
        period = segment["ticksPerMeasure"]
        beginning = tick + math.ceil((lo - tick) / period) * period
        count = (hi - beginning) // period + 1
        if count > config.max_grid_lines:
            raise ChartError("too many measure lines to render")
        lines.update(range(beginning, hi + 1, period))
        if first <= tick <= last:
            lines.add(tick)  # Partial measure at a signature change.
    return sorted(lines)


def page_boundaries(chart: Chart, config: Config, measures):
    """Match InFalsus: last fitting bar line, or the next oversized bar.

    Every cut advances along the chart. A final short slice is not redistributed
    among other pages, and no fixed number of measures is imposed on a page.
    """
    offsets = [axis(chart, config, tick) for tick in measures]
    if any(b < a for a, b in zip(offsets, offsets[1:])):
        raise ChartError("visual timing must be nondecreasing to paginate")
    boundaries, cursor = [measures[0]], 0
    last = len(measures) - 1
    while round(offsets[last] - offsets[cursor], 9) > config.page_target_height:
        next_index = max(cursor + 1, bisect_right(offsets, offsets[cursor] + config.page_target_height + 1e-9) - 1)
        if next_index >= last:
            break
        boundaries.append(measures[next_index])
        cursor = next_index
    boundaries.append(measures[-1])
    return boundaries


def make_layout(chart: Chart, config: Config) -> Layout:
    config.validate()
    measures = measure_lines(chart, config)
    sigs = chart.timing.sig_segments
    sig_ticks = [segment["t"] for segment in sigs]
    grid_count = len(measures)
    for start, end in zip(measures, measures[1:]):
        sig = sigs[max(0, bisect_right(sig_ticks, start) - 1)]
        grid_count += max(0, math.ceil((end - start) / (PPQ * 4 / sig["denominator"])) - 1)
        if grid_count > config.max_grid_lines:
            raise ChartError("signature subdivisions exceed preview grid limit")
    boundaries = page_boundaries(chart, config, measures)
    intervals = list(zip(boundaries, boundaries[1:]))
    # Decimal pixel scales can yield 3840.000000000001 after subtraction;
    # that must not inflate an exactly fitting page by one pixel on ceil().
    spans = [round(axis(chart, config, end) - axis(chart, config, start), 9) for start, end in intervals]
    if any(not math.isfinite(span) or span < 0 for span in spans):
        raise ChartError("invalid visual timing extent")
    track_height = max(config.page_target_height, math.ceil(max(spans)))
    column_width = config.time_gutter + config.track_width + config.event_gutter
    row_width = len(intervals) * column_width + (len(intervals) - 1) * config.column_gap
    width = max(800, 2 * config.padding + row_width)
    height = config.header_height + config.legend_height + 2 * config.padding + track_height + 12
    if width * height > config.max_pixels:
        raise ChartError(f"preview {width}x{height} exceeds {config.max_pixels} pixels; reduce track width/scale or render a smaller chart")
    origin = (width - row_width) / 2
    bottom = config.header_height + config.legend_height + config.padding + track_height
    columns = tuple(Column(index, start, end,
                           origin + index * (column_width + config.column_gap) + config.time_gutter,
                           bottom - spans[index], bottom)
                    for index, (start, end) in enumerate(intervals))
    return Layout(width, height, columns, tuple(measures), track_height)


def y_for(chart, config, column, tick):
    return column.bottom - round(axis(chart, config, tick) - axis(chart, config, column.start), 9)


def contains_note(column: Column, tick: int):
    # The reference assigns cut-line labels to the preceding page only.
    return column.start < tick <= column.end or (column.index == 0 and tick == column.start)
