"""CLI for a standalone SS or a score ID from bot resources."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

from .config import Config, LOGO_PATH
from .elements import RenderMetadata
from .metadata import find_resource, verify_resource_bytes
from .parser import parse_chart
from .ss_format import ChartError, MAX_BYTES


def arguments():
    parser = argparse.ArgumentParser(description="Render an Our Notes SS (plain UTF-8 or gzip) chart preview")
    parser.add_argument("chart", help="SS path, or numeric score ID when --resources is supplied")
    parser.add_argument("output", type=Path, help="output .png/.jpg/.jpeg")
    parser.add_argument("--resources", type=Path, help="bot resource directory; automatically use song/level/cover metadata")
    parser.add_argument("--score-id", type=int, help="metadata score ID when the SS filename is not numeric")
    parser.add_argument("--title", help="override song title")
    parser.add_argument("--artist", help="override artist/band name")
    parser.add_argument("--difficulty", help="override difficulty label")
    parser.add_argument("--level", help="override displayed difficulty level")
    parser.add_argument("--full-combo", type=int, help="authoritative combo count; not inferred from source nodes")
    parser.add_argument("--jacket", type=Path, help="override cover image")
    parser.add_argument("--font", type=Path, help="custom font file; default bundled Chiron Hei HK")
    parser.add_argument("--mirror", action="store_true", help="mirror lane positions, directions and edge easing")
    parser.add_argument("--spacing", choices=("beat", "time"), default="time")
    parser.add_argument("--pixels-per-beat", type=float, default=40)
    parser.add_argument("--pixels-per-second", type=float, default=320)
    parser.add_argument("--track-width", type=int, default=300)
    parser.add_argument("--page-height", dest="page_target_height", type=int, default=2560,
                        help="target track height in pixels; cut at the last measure line that fits")
    parser.add_argument("--show-hidden", action="store_true", help="show hidden/auto geometry anchors for debugging")
    parser.add_argument("--no-events", action="store_true", help="hide BPM/signature/Skill/Fever/Call labels")
    parser.add_argument("--quality", type=int, default=92, help="JPEG quality 1..100")
    return parser


def main(argv=None):
    args = arguments().parse_args(argv)
    try:
        if args.score_id is not None and not args.resources:
            raise ChartError("--score-id requires --resources")
        entry = None
        input_path = Path(args.chart)
        metadata = RenderMetadata(title=input_path.stem)
        if args.resources:
            score_id = args.score_id
            if score_id is None:
                try:
                    score_id = int(input_path.stem)
                except ValueError:
                    raise ChartError("metadata requires a numeric SS filename, a numeric chart argument, or --score-id") from None
            entry = find_resource(args.resources, score_id)
            if args.chart.isdecimal():
                input_path = entry.chart_path
            metadata = entry.metadata
        protected = {input_path.resolve(), LOGO_PATH.resolve()}
        if args.resources:
            protected.update((args.resources / name).resolve() for name in ("songs.json", "bands.json"))
        for path in (args.jacket, args.font, Path(metadata.jacket_path) if metadata.jacket_path else None):
            if path:
                protected.add(path.resolve())
        if args.output.resolve() in protected:
            raise ChartError("output image would overwrite an input chart/font/cover/metadata file")
        if args.output.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            raise ChartError("output suffix must be .png, .jpg or .jpeg")
        if not 1 <= args.quality <= 100:
            raise ChartError("JPEG quality must be from 1 to 100")
        if args.full_combo is not None and args.full_combo < 0:
            raise ChartError("--full-combo must be nonnegative")
        with input_path.open("rb") as stream:
            data = stream.read(MAX_BYTES + 1)
        if entry:
            verify_resource_bytes(entry, data)
        chart = parse_chart(data, mirror=args.mirror)
        overrides = {key: value for key, value in (("title", args.title), ("artist", args.artist),
                     ("difficulty", args.difficulty), ("level", args.level), ("full_combo", args.full_combo),
                     ("jacket_path", str(args.jacket) if args.jacket else None)) if value is not None}
        metadata = replace(metadata, **overrides)
        config = Config(track_width=args.track_width, pixels_per_beat=args.pixels_per_beat,
                        pixels_per_second=args.pixels_per_second, spacing=args.spacing,
                        page_target_height=args.page_target_height,
                        show_hidden=args.show_hidden, show_events=not args.no_events)
        if args.font:
            config.font_path = str(args.font)
        from .renderer import render, save_image
        image = render(chart, metadata=metadata, config=config)
        save_image(image, args.output, quality=args.quality)
        print(f"Preview: {args.output.resolve()}")
        print(json.dumps({"width": image.width(), "height": image.height(), "encoding": chart.encoding,
                          "sourceObjects": chart.source_count, "sourceNodes": chart.timeline_count,
                          "warnings": len(chart.warnings), "scoreId": metadata.score_id}))
        if chart.warnings:
            print(f"{len(chart.warnings)} source warning(s) retained; use Chart.warnings for details.", file=sys.stderr)
        return 0
    except (ChartError, OSError, ValueError, RuntimeError, ImportError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
