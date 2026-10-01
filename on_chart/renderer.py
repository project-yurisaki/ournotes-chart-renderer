"""Skia flat preview: height-based pages, curved ribbons and vector notes."""
from __future__ import annotations

from bisect import bisect_right
import math
from pathlib import Path as FilePath
import os
import tempfile

import skia

from .config import Config, LOGO_PATH
from .elements import Chart, Note, RenderMetadata
from .layout import axis, contains_note, make_layout, y_for
from .judgement import build_judgements
from .notes import curve_anchors, note_color, note_kind, sample_segment
from .ss_format import ChartError, PPQ


def paint(color, *, stroke=0):
    result = skia.Paint(Color=color, AntiAlias=True)
    if stroke:
        result.setStyle(skia.Paint.kStroke_Style)
        result.setStrokeWidth(stroke)
        result.setStrokeJoin(skia.Paint.kRound_Join)
    return result


def alpha(color, value):
    return (max(0, min(255, int(value))) << 24) | (color & 0xFFFFFF)


def path(points, *, close=False):
    result = skia.Path()
    result.moveTo(*points[0])
    for point in points[1:]:
        result.lineTo(*point)
    if close:
        result.close()
    return result


def format_time(ms):
    prefix = "-" if ms < 0 else ""
    value = abs(ms) / 1000
    return f"{prefix}{int(value // 60)}:{value % 60:04.1f}"


class RenderContext:
    def __init__(self, chart: Chart, config: Config):
        self.chart, self.config, self.palette = chart, config, config.palette
        self.layout = make_layout(chart, config)
        self.judgements = build_judgements(chart)
        self.typeface = skia.Typeface.MakeFromFile(config.font_path)
        if self.typeface is None:
            raise ChartError(f"cannot load font: {config.font_path}")
        self._fonts = {}
        self._fallbacks = {}
        self._runs = {}
        self.font_manager = skia.FontMgr.RefDefault()

    def font(self, size, typeface=None):
        typeface = typeface or self.typeface
        key = (size, typeface.uniqueID())
        if key not in self._fonts:
            self._fonts[key] = skia.Font(typeface, size)
        return self._fonts[key]

    def text_runs(self, text):
        if text in self._runs:
            return self._runs[text]
        glyphs = self.font(16).textToGlyphs(text)
        if all(glyphs):
            result = [(text, self.typeface)]
        else:
            faces = []
            for char, glyph in zip(text, glyphs):
                if glyph:
                    faces.append(self.typeface)
                else:
                    if char not in self._fallbacks:
                        self._fallbacks[char] = self.font_manager.matchFamilyStyleCharacter(
                            self.typeface.getFamilyName(), skia.FontStyle.Normal(), ["en", "ja", "zh-CN"], ord(char))
                    face = self._fallbacks[char]
                    if face is None or not all(self.font(16, face).textToGlyphs(char)):
                        raise ChartError(f"no font covers U+{ord(char):04X}; supply --font with the required glyphs")
                    faces.append(face)
            # Prefer one complete fallback face to mixed styles in a CJK title.
            replacement = next((face for face in faces if face.uniqueID() != self.typeface.uniqueID()
                                and all(self.font(16, face).textToGlyphs(text))), None)
            if replacement:
                result = [(text, replacement)]
            else:
                result = []
                for char, face in zip(text, faces):
                    if result and result[-1][1].uniqueID() == face.uniqueID():
                        result[-1] = (result[-1][0] + char, face)
                    else:
                        result.append((char, face))
        self._runs[text] = result
        return result

    def measure_text(self, text, size):
        return sum(self.font(size, face).measureText(run) for run, face in self.text_runs(text))

    def text(self, canvas, text, x, baseline, size=14, color=None, width=None):
        text = " ".join(str(text).splitlines())
        if width is not None:
            while text and self.measure_text(text, size) > width:
                text = text[:-2] + "…" if len(text) > 2 else ""
        for run, face in self.text_runs(text):
            font = self.font(size, face)
            canvas.drawString(run, x, baseline, font, paint(color if color is not None else self.palette.text))
            x += font.measureText(run)


def _diamond(canvas, x, y, radius, color):
    canvas.drawPath(path([(x, y - radius), (x + radius, y), (x, y + radius), (x - radius, y)], close=True), paint(color))


def draw_note(canvas, note, left, y, width, config):
    """Draw a single head/node in local track pixels. Input position is not snapped."""
    kind, color = note_kind(note), note_color(note, config.palette)
    if note.consumed:
        return
    if kind == "guide-boundary":
        return
    if kind == "hidden" and not config.show_hidden:
        return
    # "auto" controls interpolated geometry, not whether the node is drawn.
    if kind == "connection" and not note.visible and not config.show_hidden:
        return
    center, h = left + width / 2, config.note_height
    if kind == "hidden":
        canvas.drawCircle(center, y, 3.4, paint(alpha(color, 190), stroke=1.3))
        return
    width = max(2, width)
    # Slightly bevel the two ends, like the plates in the screenshot references.
    thin = kind in ("trace", "connection")
    h = h * 0.68 if thin else h
    bevel = min(h * 0.6, width / 4)
    plate = path([(left + bevel, y - h / 2), (left + width - bevel, y - h / 2),
                  (left + width, y), (left + width - bevel, y + h / 2),
                  (left + bevel, y + h / 2), (left, y)], close=True)
    shader = skia.GradientShader.MakeLinear([skia.Point(0, y - h / 2), skia.Point(0, y + h / 2)],
                                           [0xFFF5FFFF, color, alpha(color, 255)], [0, 0.45, 1])
    fill = paint(color)
    fill.setShader(shader)
    canvas.drawPath(plate, paint(alpha(color, 45), stroke=5))
    canvas.drawPath(plate, fill)
    canvas.drawPath(plate, paint(color, stroke=1.2))
    _diamond(canvas, center, y, 2 if thin else 2.6, 0xFFF6F1FF if thin else color)
    if kind == "flick":
        if note.direction in ("left", "right"):
            direction = -1 if note.direction == "left" else 1
            for index in (-1, 0, 1):
                x = center + index * 12
                arrow = path([(x - direction * 3, y - 21), (x + direction * 4, y - 15), (x - direction * 3, y - 9)])
                canvas.drawPath(arrow, paint(alpha(color, 60), stroke=7))
                canvas.drawPath(arrow, paint(color, stroke=3.5))
        else:
            arrow = path([(center - 12, y - 11), (center, y - 21), (center + 12, y - 11)])
            canvas.drawPath(arrow, paint(alpha(color, 60), stroke=7))
            canvas.drawPath(arrow, paint(color, stroke=3.8))


def _lane_x(column, config, coordinate):
    return column.left + max(-24, min(48, coordinate)) * config.track_width / 24


def _ribbons(ctx, canvas, column):
    config, chart, p = ctx.config, ctx.chart, ctx.palette
    for slide in chart.slides:
        for index, (a, b) in enumerate(zip(slide.nodes, slide.nodes[1:])):
            lo, hi = max(column.start, a.tick), min(column.end, b.tick)
            if hi <= lo:
                continue
            curve_a, curve_b = curve_anchors(slide.nodes, index)
            # Sampling is restricted to the visible column. Tempo breakpoints
            # are included so time-spaced ribbons remain smooth across BPM changes.
            breaks = sorted({lo, hi, *(s["t"] for s in chart.timing.bpm_segments if lo < s["t"] < hi)})
            samples = []
            for start, end in zip(breaks, breaks[1:]):
                extent = abs(axis(chart, config, end) - axis(chart, config, start))
                section = sample_segment(curve_a, curve_b, start, end, extent, config.curve_step)
                samples.extend(section if not samples else section[1:])
            left = [(_lane_x(column, config, x), y_for(chart, config, column, t)) for t, x, _ in samples]
            right = [(_lane_x(column, config, x), y_for(chart, config, column, t)) for t, _, x in samples]
            critical = a.critical and b.critical
            color = alpha(p.critical, 85) if critical else p.guide_fill if slide.kind == "guide" else p.slide_fill
            edge = p.critical if critical else p.guide_edge if slide.kind == "guide" else p.slide_edge
            fill, outline = paint(color), paint(alpha(edge, 185), stroke=1.2)
            if curve_a.alpha in ("in", "out"):
                first, last = ((0, 255) if curve_a.alpha == "in" else (255, 0))
                start_y, end_y = y_for(chart, config, column, curve_a.tick), y_for(chart, config, column, curve_b.tick)
                if start_y != end_y:
                    for target, base in ((fill, color), (outline, alpha(edge, 185))):
                        opacity = (base >> 24) & 255
                        target.setShader(skia.GradientShader.MakeLinear(
                            [skia.Point(0, start_y), skia.Point(0, end_y)],
                            [alpha(base, first * opacity / 255), alpha(base, last * opacity / 255)]))
            canvas.drawPath(path(left + list(reversed(right)), close=True), fill)
            canvas.drawPath(path(left), outline)
            canvas.drawPath(path(right), outline)


def _grid(ctx, canvas, column):
    p, config, chart = ctx.palette, ctx.config, ctx.chart
    canvas.drawRect(skia.Rect(column.left, column.top,
                              column.left + config.track_width, column.bottom), paint(p.lane_alt))
    # Six visible lanes, each spanning four SS coordinate units. Keep the
    # original 24-unit note geometry without drawing extra subdivision lines.
    for boundary in range(1, 6):
        x = column.left + boundary * config.track_width / 6
        canvas.drawLine(x, column.top, x, column.bottom,
                        paint(alpha(p.border, 95), stroke=1))
    measure_ticks = ctx.layout.measures
    sigs = chart.timing.sig_segments
    sig_ticks = [s["t"] for s in sigs]
    for start, end in zip(measure_ticks, measure_ticks[1:]):
        if start < column.start or start >= column.end:
            continue
        signature = sigs[max(0, bisect_right(sig_ticks, start) - 1)]
        beat = PPQ * 4 / signature["denominator"]
        count = math.ceil((end - start) / beat)
        if count > config.max_grid_lines:
            raise ChartError("too many signature subdivisions to render")
        for index in range(1, count):
            y = y_for(chart, config, column, start + index * beat)
            canvas.drawLine(column.left, y, column.left + config.track_width, y, paint(p.minor_grid, stroke=0.8))
    for tick in measure_ticks:
        if not column.start <= tick <= column.end:
            continue
        y = y_for(chart, config, column, tick)
        canvas.drawLine(column.left, y, column.left + config.track_width, y, paint(p.major_grid, stroke=1.1))
        if not contains_note(column, tick):
            continue
        combo = ctx.judgements.combo_at(chart.timing.time_ms(tick))
        ctx.text(canvas, str(combo), column.left - config.time_gutter + 5, y - 3, 13, p.text)
        ctx.text(canvas, format_time(chart.timing.time_ms(tick)), column.left - config.time_gutter + 5, y + 12, 11, p.muted)
    canvas.drawRect(skia.Rect(column.left, column.top, column.left + config.track_width, column.bottom), paint(alpha(p.border, 145), stroke=1))


def _events(ctx, canvas, column):
    chart, config, p = ctx.chart, ctx.config, ctx.palette
    if not config.show_events:
        return
    x = column.left + config.track_width
    for event in chart.events["fever"]:
        lo, hi = max(column.start, event["startTick"]), min(column.end, event["endTick"])
        if hi > lo:
            canvas.drawRect(skia.Rect(x + 4, y_for(chart, config, column, hi), x + 7, y_for(chart, config, column, lo)), paint(p.fever))
    labels = []
    for event in chart.timing.bpm_segments:
        labels.append((event["t"], f'{event["bpm"]:g}', p.tap, "BPM"))
    for event in chart.events["sig"]:
        labels.append((event["t"], f'{event["numerator"]}/{event["denominator"]}', p.muted, "SIG"))
    labels.extend((event["t"], "SK", p.skill, "") for event in chart.events["skill"])
    labels.extend((event["t"], "CALL", p.call, "") for event in chart.events["call"])
    for event in chart.events["fever"]:
        labels.extend(((event["startTick"], "FEVER", p.fever, ""), (event["endTick"], "END", p.fever, "")))
    placed = []
    for tick, label, color, sub in sorted(labels, key=lambda value: (value[0], value[1]), reverse=True):
        if not contains_note(column, tick):
            continue
        y = y_for(chart, config, column, tick)
        canvas.drawLine(x + 1, y, x + 11, y, paint(color, stroke=1.6))
        baseline = y - 2
        # Markers retain their actual y; short leaders move only colliding labels.
        if placed and baseline < placed[-1] + 14:
            baseline = placed[-1] + 14
        if baseline > column.bottom + 11:
            continue
        if abs(baseline - y) > 12:
            canvas.drawLine(x + 10, y, x + 13, baseline - 3, paint(alpha(color, 140), stroke=0.7))
        ctx.text(canvas, label, x + 14, baseline, 10.5, color, config.event_gutter - 14)
        placed.append(baseline)


def _header(ctx, canvas, metadata):
    config, p, chart = ctx.config, ctx.palette, ctx.chart
    width = ctx.layout.width
    canvas.drawRect(skia.Rect(0, 0, width, config.header_height), paint(p.panel))
    jacket_size = 170
    x, y = config.padding, 32
    canvas.drawRoundRect(skia.Rect(x, y, x + jacket_size, y + jacket_size), 10, 10, paint(p.lane))
    if metadata.jacket_path:
        try:
            image = skia.Image.open(metadata.jacket_path)
            if image is None:
                raise ValueError("unsupported image")
            edge = min(image.width(), image.height())
            source = skia.Rect((image.width() - edge) / 2, (image.height() - edge) / 2,
                               (image.width() + edge) / 2, (image.height() + edge) / 2)
            canvas.save()
            canvas.clipRRect(skia.RRect.MakeRectXY(skia.Rect(x, y, x + jacket_size, y + jacket_size), 10, 10))
            canvas.drawImageRect(image, source, skia.Rect(x, y, x + jacket_size, y + jacket_size))
            canvas.restore()
        except Exception as exc:
            raise ChartError(f"cannot load jacket {metadata.jacket_path}: {exc}") from None
    else:
        ctx.text(canvas, "OUR", x + 34, y + 79, 26, p.tap)
        ctx.text(canvas, "NOTES", x + 28, y + 113, 25, p.text)
    content_x = x + jacket_size + 28
    available = width - content_x - config.padding
    logo_width = min(320, available * 0.4)
    logo_height = config.header_height - 2 * config.padding
    logo_left = width - config.padding - logo_width
    logo = skia.Image.open(str(LOGO_PATH))
    if logo is None:
        raise ChartError(f"cannot load logo: {LOGO_PATH}")
    scale = min(logo_width / logo.width(), logo_height / logo.height())
    draw_width, draw_height = logo.width() * scale, logo.height() * scale
    logo_x = logo_left + (logo_width - draw_width) / 2
    logo_y = config.padding + (logo_height - draw_height) / 2
    canvas.drawImageRect(logo, skia.Rect(0, 0, logo.width(), logo.height()),
                         skia.Rect(logo_x, logo_y, logo_x + draw_width, logo_y + draw_height),
                         skia.SamplingOptions(skia.FilterMode.kLinear))
    space = logo_left - content_x - 24
    ctx.text(canvas, "OUR NOTES  /  CHART PREVIEW", content_x, 47, 13, p.muted, space)
    ctx.text(canvas, metadata.title or "Our Notes", content_x, 94, 32, p.text, space)
    ctx.text(canvas, metadata.artist, content_x, 126, 18, p.muted, space)
    identity = "  ·  ".join(value for value in (metadata.difficulty.upper(), f"Lv. {metadata.level}" if metadata.level else "",
                                               "MIRROR" if chart.mirror else "") if value)
    ctx.text(canvas, identity, content_x, 155, 17, p.tap, space)
    tempos = [segment["bpm"] for segment in chart.timing.bpm_segments]
    bpm = f"{min(tempos):g}" if min(tempos) == max(tempos) else f"{min(tempos):g}–{max(tempos):g}"
    info = [f"BPM {bpm}", f"LAST NODE {format_time(max((n.time_ms for n in chart.all_nodes), default=0))}",
            f"COMBO {metadata.full_combo if metadata.full_combo is not None else ctx.judgements.full_combo}"]
    info_text = "   /   ".join(info)
    info_size = min(15, 15 * space / max(1, ctx.measure_text(info_text, 15)))
    ctx.text(canvas, info_text, content_x, 188, max(10, info_size), p.text, space)


def _legend(ctx, canvas):
    x, y, config = ctx.config.padding, ctx.config.header_height + 36, ctx.config
    items = [("Tap", Note(0, 0, 0, 6, "tap")), ("Critical", Note(0, 0, 0, 6, "tap", critical=True)),
             ("Flick", Note(0, 0, 0, 6, "flick", operation=40)),
             ("Left", Note(0, 0, 0, 6, "flick", direction="left", operation=40)),
             ("Right", Note(0, 0, 0, 6, "flick", direction="right", operation=40)),
             ("Trace", Note(0, 0, 0, 6, "trace", operation=60))]
    slot = min(138, (ctx.layout.width - 2 * x) / len(items))
    for index, (label, note) in enumerate(items):
        origin = x + index * slot
        draw_note(canvas, note, origin, y, 33, config)
        ctx.text(canvas, label, origin + 40, y + 4, 11.5, ctx.palette.muted, slot - 44)


def render(chart: Chart, jacket_path="", title="", artist="", *, difficulty="", level="", full_combo=None,
           config: Config | None = None, metadata: RenderMetadata | None = None) -> skia.Image:
    config = config or Config()
    metadata = metadata or RenderMetadata(title or "Our Notes", artist, difficulty, str(level), full_combo, str(jacket_path))
    ctx = RenderContext(chart, config)
    surface = skia.Surface(ctx.layout.width, ctx.layout.height)
    if surface is None:
        raise ChartError("Skia could not allocate preview surface")
    canvas = surface.getCanvas()
    canvas.clear(ctx.palette.background)
    _header(ctx, canvas, metadata)
    _legend(ctx, canvas)
    nodes = sorted(chart.all_nodes, key=lambda value: (value.tick, value.source_path))
    for column in ctx.layout.columns:
        _grid(ctx, canvas, column)
        canvas.save()
        # Like slicing the reference's full track image, note plates and Flick
        # arrows crossing a cut are clipped into both adjacent page slices.
        clip_bottom = column.bottom + config.note_height if column.index == 0 else column.bottom
        canvas.clipRect(skia.Rect(column.left, column.top, column.left + config.track_width, clip_bottom))
        _ribbons(ctx, canvas, column)
        # Ribbons first; heads are painted afterward to remain visible in crossings.
        for note in nodes:
            y = y_for(chart, config, column, note.tick)
            below = config.note_height / 2 + 3
            above = max(below, 25) if note_kind(note) == "flick" else below
            if y + below >= column.top and y - above <= clip_bottom:
                left = _lane_x(column, config, note.pos)
                right = _lane_x(column, config, note.pos + max(0.25, note.width))
                if right > column.left and left < column.left + config.track_width:
                    draw_note(canvas, note, left, y, right - left, config)
        canvas.restore()
        _events(ctx, canvas, column)
    return surface.makeImageSnapshot()


def save_image(image: skia.Image, output, *, quality=92):
    output = FilePath(output)
    if output.suffix.lower() not in (".png", ".jpg", ".jpeg"):
        raise ChartError("output suffix must be .png, .jpg or .jpeg")
    if type(quality) is not int or not 1 <= quality <= 100:
        raise ChartError("JPEG quality must be an integer from 1 to 100")
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = image.encodeToData(skia.EncodedImageFormat.kPNG if output.suffix.lower() == ".png" else skia.EncodedImageFormat.kJPEG, quality)
    if encoded is None:
        raise ChartError("Skia could not encode preview image")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".preview-", suffix=".tmp", dir=output.parent, delete=False) as stream:
            temporary = FilePath(stream.name)
            stream.write(bytes(encoded))
        os.replace(temporary, output)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
