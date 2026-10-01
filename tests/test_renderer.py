"""Behavioral regression tests using synthetic SS, without game assets."""
import contextlib
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import skia
from PIL import Image
from on_chart import ChartError, Config, Note, Palette, RenderMetadata, parse_chart, parse_file, render, save_image
from on_chart.cli import main
from on_chart.layout import axis, contains_note, make_layout, measure_lines, y_for
from on_chart.metadata import find_resource, verify_resource_bytes
from on_chart.notes import curve_anchors, edges_at, note_color, note_kind, sample_segment
from on_chart.renderer import RenderContext, draw_note
from on_chart.ss_format import TimingMap


def raw(notes=(), events=None):
    return json.dumps({"score": {"notes": list(notes), "events": events or {}}}).encode()


def chart(notes=(), events=None, **kwargs):
    return parse_chart(raw(notes, events), **kwargs)


class ParseTests(unittest.TestCase):
    def test_plain_gzip_bom_and_text_have_identical_models(self):
        data = b'\xef\xbb\xbf' + raw([{"t": 480}])
        plain, compressed = parse_chart(data), parse_chart(gzip.compress(data))
        self.assertEqual(plain.notes, compressed.notes)
        self.assertEqual(plain.events, compressed.events)
        self.assertEqual(parse_chart(data.decode("utf-8-sig")).notes, plain.notes)
        self.assertEqual((plain.encoding, compressed.encoding), ("json", "gzip-json"))

    def test_defaults_and_native_timing(self):
        model = chart([{}, {"t": 480}, {"t": 2400}], {"bpm": [{"t": 1920, "bpm": 240}]})
        self.assertEqual([(n.pos, n.width) for n in model.notes], [(0, 6)] * 3)
        self.assertEqual([n.time_ms for n in model.notes], [0, 500, 2250])
        self.assertEqual(model.source_count, 3)

    def test_native_round_even_segment_origin_and_floor(self):
        timing = TimingMap([{"t": t, "bpm": 250.0} for t in (0, 1, 2, 3, 4)])
        self.assertEqual([s["timeMs"] for s in timing.bpm_segments], [0, 0, 1, 2, 2])
        self.assertEqual(TimingMap().time_ms(-1), -2)

    def test_guide_overlap_only_draws_one_head_and_inherits_critical(self):
        model = chart([{"type": "flick", "pos": 4, "size": 2, "dir": "left", "crit": True},
                       {"type": "guide", "node": [{"pos": 4, "size": 2}, {"t": 960, "pos": 6, "size": 2}]}])
        self.assertTrue(model.notes[0].consumed)
        head = model.slides[0].nodes[0]
        self.assertEqual((head.operation, head.direction, head.critical), (102, "left", True))
        self.assertEqual(model.timeline_count, 3)  # source count remains intact

    def test_native_auto_geometry_and_independent_edge_ease(self):
        model = chart([{"type": "long", "node": [
            {"pos": 0, "size": 4, "ease": ["in", "out"]},
            {"t": 480, "pos": "auto", "size": 999}, {"t": 960, "pos": 12, "size": 6}]}])
        middle = model.slides[0].nodes[1]
        self.assertEqual((middle.pos, middle.width, middle.auto, middle.operation), (3, 11.5, True, 21))

    def test_mirror_reflects_continuous_asymmetric_curve(self):
        data = raw([{"type": "long", "node": [
            {"pos": 1, "size": 4, "ease": ["in", "out"]},
            {"type": "flick", "t": 960, "pos": 12, "size": 6, "dir": "left"}]}])
        a, b = parse_chart(data).slides[0].nodes
        am, bm = parse_chart(data, mirror=True).slides[0].nodes
        self.assertEqual(am.ease, ("out", "in"))
        self.assertEqual(bm.direction, "right")
        for tick in (0, 120, 480, 700, 960):
            left, right = edges_at(a, b, tick)
            ml, mr = edges_at(am, bm, tick)
            self.assertAlmostEqual(ml, 24 - right)
            self.assertAlmostEqual(mr, 24 - left)

    def test_down_flick_uses_native_normal_direction(self):
        self.assertEqual(chart([{"type": "flick", "dir": "down"}]).notes[0].direction, "up")

    def test_hidden_and_guide_branches(self):
        model = chart([{"type": "long", "node": [
            {"visible": False}, {"t": 1, "type": "trace"}, {"t": 2, "visible": False},
            {"t": 3, "pos": "auto", "visible": False}, {"t": 4, "visible": False}]}])
        self.assertEqual([n.operation for n in model.slides[0].nodes], [80, 63, 122, 21, 82])
        self.assertEqual([note_kind(n) for n in model.slides[0].nodes], ["hidden", "trace", "hidden", "connection", "hidden"])

    def test_group_source_order_is_preserved(self):
        model = chart([{"type": "long", "node": [{"t": 480}, {"t": 0}, {"t": 960}]}])
        self.assertEqual([n.tick for n in model.slides[0].nodes], [480, 0, 960])
        self.assertTrue(any("not ordered" in warning for warning in model.warnings))

    def test_short_and_top_level_node_are_not_converted(self):
        model = chart([{"type": "long", "node": [{}]}, {"type": "node"}])
        self.assertEqual(model.all_nodes, [])
        self.assertEqual(model.source_count, 2)

    def test_bounded_decompression_and_corrupt_input(self):
        data = b'{"score":{},"padding":"' + b'x' * 10000 + b'"}'
        for value, message in ((data, "input exceeds"), (gzip.compress(data), "decompressed")):
            with self.assertRaisesRegex(ChartError, message):
                parse_chart(value, max_bytes=1000)
        for value in (gzip.compress(data)[:-4], b'\xff\x81', b'UnityFS\0bundle', b'{"score":{},"x":NaN}'):
            with self.assertRaises(ChartError):
                parse_chart(value)

    def test_event_extents_and_call_mask(self):
        model = chart(events={"fever": [[480, 4800]], "call": [{"t": 0, "timing": [1, 0, 1, 0]}]})
        self.assertEqual(model.end_tick, 4800)
        self.assertEqual(model.events["call"][0]["activeFractions"], [0.25, 0.75])


class LayoutTests(unittest.TestCase):
    def test_empty_and_negative_preroll_are_renderable(self):
        self.assertEqual(len(make_layout(chart(), Config()).columns), 1)
        model = chart([{"t": -480}, {"t": 480}])
        self.assertEqual(measure_lines(model, Config())[0], -1920)

    def test_pages_cut_at_last_measure_within_target_height(self):
        model = chart([{"t": 75 * 1920}])
        layout = make_layout(model, Config(spacing="beat"))
        self.assertEqual([(c.start // 1920, c.end // 1920) for c in layout.columns], [(0, 16), (16, 32), (32, 48), (48, 64), (64, 76)])
        self.assertEqual(layout.track_height, 2560)
        self.assertEqual(len({c.bottom for c in layout.columns}), 1)
        self.assertEqual(layout.columns[-1].bottom - layout.columns[-1].top, 1920)
        self.assertGreater(layout.columns[-1].top, layout.columns[0].top)
        small = make_layout(model, Config(spacing="beat", page_target_height=530))
        self.assertEqual(small.columns[0].end, 3 * 1920)  # 480 pixels; a fourth bar would exceed 530

    def test_time_pages_include_different_bar_counts_at_different_tempos(self):
        model = chart([{"t": 12 * 1920}], {"bpm": [{"t": 0, "bpm": 120}, {"t": 4 * 1920, "bpm": 240}]})
        config = Config(spacing="time", pixels_per_second=160, page_target_height=640)
        layout = make_layout(model, config)
        lengths = [(c.end - c.start) // 1920 for c in layout.columns]
        self.assertEqual(lengths, [2, 2, 4, 4, 1])
        self.assertTrue(all(axis(model, config, c.end) - axis(model, config, c.start) <= 640 for c in layout.columns))

    def test_single_tall_measure_expands_page_like_reference(self):
        model = chart([{"t": 500}], {"sig": [{"t": 0, "sig": [32, 4]}]})
        layout = make_layout(model, Config(spacing="beat", page_target_height=100))
        self.assertEqual(len(layout.columns), 1)
        self.assertEqual(layout.track_height, 1280)

    def test_fractional_time_offsets_do_not_add_a_spurious_pixel(self):
        model = chart([{"t": 300000}], {"bpm": [{"t": 10, "bpm": 260}]})
        config = Config(spacing="time", pixels_per_second=160, page_target_height=3840)
        layout = make_layout(model, config)
        # These decimal offsets contain a 3840.000000000001 subtraction.
        self.assertEqual(layout.track_height, config.page_target_height)
        self.assertEqual([(c.end - c.start) // 1920 for c in layout.columns], [25, 26, 26, 26, 26, 26, 2])
        self.assertTrue(all(c.bottom - c.top <= config.page_target_height for c in layout.columns))

    def test_cut_line_labels_have_one_owner(self):
        layout = make_layout(chart([{"t": 10 * 1920}]), Config(page_target_height=480))
        for tick in (0, *[c.end for c in layout.columns]):
            self.assertEqual(sum(contains_note(c, tick) for c in layout.columns), 1)
        self.assertTrue(contains_note(layout.columns[0], layout.columns[0].end))
        self.assertFalse(contains_note(layout.columns[1], layout.columns[1].start))

    def test_signature_change_adds_partial_measure(self):
        model = chart([{"t": 5000}], {"sig": [{"t": 2400, "sig": [3, 4]}]})
        self.assertEqual(measure_lines(model, Config()), [0, 1920, 2400, 3840, 5280])

    def test_long_chart_has_no_fixed_measure_or_page_count_limit(self):
        model = chart([{"t": 500 * 1920}])
        layout = make_layout(model, Config(spacing="beat"))
        self.assertGreater(len(layout.columns), 8)
        self.assertEqual(len(layout.measures), 502)
        self.assertTrue(contains_note(layout.columns[-1], model.end_tick))
        self.assertEqual(layout.columns[0].start, layout.measures[0])
        self.assertEqual(layout.columns[-1].end, layout.measures[-1])
        self.assertTrue(all(a.end == b.start for a, b in zip(layout.columns, layout.columns[1:])))
        self.assertEqual(len({c.bottom for c in layout.columns}), 1)

    def test_time_spacing_changes_at_bpm_breakpoint(self):
        model = chart([{"t": 1920}], {"bpm": [{"t": 480, "bpm": 240}]})
        config = Config()
        self.assertEqual((config.spacing, config.pixels_per_second), ("time", 320))
        c = make_layout(model, config).columns[0]
        self.assertEqual(y_for(model, config, c, 0) - y_for(model, config, c, 480), 160)
        self.assertEqual(y_for(model, config, c, 480) - y_for(model, config, c, 960), 80)

    def test_allocation_and_grid_guards(self):
        with self.assertRaisesRegex(ChartError, "pixels"):
            make_layout(chart(), Config(max_pixels=100))
        with self.assertRaisesRegex(ChartError, "grid limit"):
            make_layout(chart([{"t": 2**30}]), Config())

    def test_invalid_config_has_clear_errors(self):
        for changes in ({"pixels_per_beat": float("nan")}, {"track_width": 2}, {"max_grid_lines": 0},
                        {"page_target_height": 0}, {"spacing": "bad"}):
            with self.assertRaises(ChartError):
                make_layout(chart(), Config(**changes))

    def test_grid_guard_counts_all_signature_subdivisions(self):
        model = chart([{"t": 1920 * 3}], {"sig": [{"t": 0, "sig": [20000, 20000]}]})
        with self.assertRaisesRegex(ChartError, "subdivisions"):
            make_layout(model, Config())

    def test_sampled_curve_sections_are_continuous(self):
        a, b = Note(0, 0, 0, 6, "tap", ease=("in", "out")), Note(3840, 4000, 18, 6, "tap")
        first = sample_segment(a, b, 0, 1920, 160)
        second = sample_segment(a, b, 1920, 3840, 160)
        self.assertEqual(first[-1], second[0])
        self.assertEqual(first[-1], (1920, 4.5, 19.5))

    def test_auto_nodes_do_not_restart_asymmetric_edge_easing(self):
        model = chart([{"type": "long", "node": [
            {"pos": 0, "size": 4, "ease": ["in", "out"]},
            {"t": 480, "pos": "auto"}, {"t": 960, "pos": 12, "size": 6}]}])
        nodes = model.slides[0].nodes
        first, second = curve_anchors(nodes, 0), curve_anchors(nodes, 1)
        self.assertEqual(first, (nodes[0], nodes[2]))
        self.assertEqual(first, second)
        self.assertEqual(edges_at(*first, 240), (0.75, 10.125))
        self.assertEqual(edges_at(*first, 480), (nodes[1].pos, nodes[1].pos + nodes[1].width))

    def test_auto_nodes_do_not_change_rendered_curve(self):
        nodes = [{"pos": 0, "size": 4, "ease": ["in", "out"]}, {"t": 960, "pos": 12, "size": 6}]
        # Compare ribbons alone; visible auto connection plates are intentional.
        with patch("on_chart.renderer.draw_note"):
            a = render(chart([{"type": "long", "node": nodes}]))
            b = render(chart([{"type": "long", "node": [nodes[0], {"t": 480, "pos": "auto"}, nodes[1]]}]))
        pa = Image.open(io.BytesIO(bytes(a.encodeToData()))).convert("RGB")
        pb = Image.open(io.BytesIO(bytes(b.encodeToData()))).convert("RGB")
        # Subdivision can shift edge antialiasing slightly, but must not reshape
        # the ribbon. Compare its whole interior, excluding endpoint plates.
        from PIL import ImageChops, ImageStat
        model = chart([{"type": "long", "node": nodes}])
        col = make_layout(model, Config()).columns[0]
        region = (round(col.left), round(y_for(model, Config(), col, 850)),
                  round(col.left + Config().track_width), round(y_for(model, Config(), col, 100)))
        difference = ImageChops.difference(pa.crop(region), pb.crop(region))
        self.assertLess(max(ImageStat.Stat(difference).mean), 0.5)


class DrawingTests(unittest.TestCase):
    def test_palette_matches_reference_note_roles(self):
        p = Palette()
        notes = [Note(0, 0, 0, 6, "tap"), Note(0, 0, 0, 6, "tap", critical=True),
                 *[Note(0, 0, 0, 6, "flick", direction=d, operation=40) for d in ("up", "left", "right")],
                 Note(0, 0, 0, 6, "trace", operation=60)]
        self.assertEqual([note_color(n, p) for n in notes], [p.tap, p.critical, p.flick_up, p.flick_left, p.flick_right, p.trace])

    def test_hidden_and_consumed_heads_are_suppressed(self):
        for note in (Note(0, 0, 0, 6, "node", operation=122), Note(0, 0, 0, 6, "node", auto=True, visible=False, operation=21),
                     Note(0, 0, 0, 6, "tap", consumed=True)):
            surface = skia.Surface(100, 100)
            surface.getCanvas().clear(skia.ColorBLACK)
            before = bytes(surface.makeImageSnapshot().encodeToData())
            draw_note(surface.getCanvas(), note, 20, 50, 60, Config())
            self.assertEqual(before, bytes(surface.makeImageSnapshot().encodeToData()))

    def test_visible_auto_connection_draws_a_full_width_thin_plate(self):
        model = chart([{"type": "long", "node": [
            {"t": 71520, "pos": 0, "size": 6},
            {"t": 71640, "pos": "auto"},
            {"t": 71760, "pos": 6, "size": 6}]}])
        node = model.slides[0].nodes[1]
        self.assertEqual((node.operation, node.auto, node.visible), (21, True, True))
        surface = skia.Surface(100, 100)
        surface.getCanvas().clear(skia.ColorBLACK)
        draw_note(surface.getCanvas(), node, 20, 50, 60, Config())
        pil = Image.open(io.BytesIO(bytes(surface.makeImageSnapshot().encodeToData())))
        # Away from the center diamond: a plate spans the width, with no arrows.
        for x in (25, 35, 65, 75):
            self.assertGreater(pil.getpixel((x, 50))[2], 180)
        self.assertEqual(pil.getpixel((35, 40))[:3], (0, 0, 0))
        self.assertGreater(pil.getpixel((50, 50))[0], 230)

    def test_note_heads_are_on_top_of_ribbons(self):
        model = chart([{"t": 480, "pos": 0}, {"type": "long", "node": [{"t": 0, "pos": 0}, {"t": 960, "pos": 0}]}])
        config = Config()
        image = render(model, config=config)
        col = make_layout(model, config).columns[0]
        pil = Image.open(io.BytesIO(bytes(image.encodeToData())))
        center = pil.getpixel((round(col.left + config.track_width / 8), round(y_for(model, config, col, 480))))
        self.assertEqual(center[:3], (76, 225, 244))
        ribbon = pil.getpixel((round(col.left + config.track_width / 8), round(y_for(model, config, col, 720))))
        self.assertNotEqual(ribbon, center)
        self.assertGreater(ribbon[2], ribbon[0])

    def test_missing_cjk_glyphs_use_system_fallback(self):
        ctx = RenderContext(chart(), Config())
        if ctx.font_manager.matchFamilyStyleCharacter("", skia.FontStyle.Normal(), ["ja"], ord("星")) is None:
            self.skipTest("system has no CJK font")
        self.assertTrue(all(all(ctx.font(16, face).textToGlyphs(run)) for run, face in ctx.text_runs("迷星叫")))

    def test_unavailable_glyph_fails_with_actionable_message(self):
        ctx = RenderContext(chart(), Config())
        ctx.font_manager = Mock()
        ctx.font_manager.matchFamilyStyleCharacter.return_value = None
        with self.assertRaisesRegex(ChartError, "supply --font"):
            ctx.text_runs("\U0010ffff")

    def test_synthetic_all_note_types_render(self):
        model = parse_file(Path(__file__).resolve().parents[1] / "examples/note-types.ss")
        self.assertEqual(model.warnings, [])
        self.assertTrue(any(slide.kind == "guide" for slide in model.slides))
        image = render(model)
        self.assertGreaterEqual(image.width(), 800)
        self.assertGreater(image.height(), 1000)

    def test_no_per_page_number_or_time_range_is_drawn(self):
        model = chart([{"t": 20 * 1920}])
        config = Config(page_target_height=640)
        captured = []
        original = RenderContext.text
        def record(ctx, canvas, text, *args, **kwargs):
            captured.append(str(text))
            return original(ctx, canvas, text, *args, **kwargs)
        with patch.object(RenderContext, "text", record):
            render(model, config=config, metadata=RenderMetadata(title="Test", difficulty="expert", level="25", score_id=12345678))
        self.assertNotIn("01", captured)
        self.assertNotIn("02", captured)
        self.assertFalse(any(" – " in text for text in captured))
        self.assertFalse(any("12345678" in text or "Score " in text for text in captured))
        self.assertFalse(any("SPACING" in text or "bottom → top" in text or "left → right" in text for text in captured))
        self.assertTrue(any("EXPERT" in text and "Lv. 25" in text for text in captured))

    def test_ribbons_and_cut_line_flick_arrows_continue_between_pages(self):
        model = chart([{"type": "flick", "t": 1920, "pos": 6, "dir": "left"},
                       {"type": "long", "node": [{"t": 0, "pos": 0, "size": 6}, {"t": 3840, "pos": 12, "size": 6}]}])
        config = Config(spacing="beat", page_target_height=160)
        layout = make_layout(model, config)
        image = render(model, config=config)
        pil = Image.open(io.BytesIO(bytes(image.encodeToData()))).convert("RGB")
        a, b = layout.columns[:2]
        # Flick arrows lie just above the cut, so they appear at the bottom of
        # the next page; its plate remains visible at the preceding page top.
        head_center = 9 * config.track_width / 24
        arrow_pixel = pil.getpixel((round(b.left + head_center - 4), round(b.bottom - 15)))
        self.assertGreater(arrow_pixel[1], 100)
        plate_pixel = pil.getpixel((round(a.left + head_center), round(a.top + 1)))
        self.assertGreater(plate_pixel[1], 100)
        # A ribbon reaches both sides of the page cut, with matching geometry.
        c = round(a.left + 6.5 * config.track_width / 24)
        d = round(b.left + 6.5 * config.track_width / 24)
        self.assertGreater(pil.getpixel((c, round(a.top + 3)))[2], 70)
        self.assertGreater(pil.getpixel((d, round(b.bottom - 3)))[2], 70)

    def test_png_jpeg_overwrite_and_encoder_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / "preview.png"
            save_image(render(chart()), dest)
            with Image.open(dest) as existing:
                size = existing.size
            save_image(render(chart([{"t": 1920 * 60}])), dest)
            with Image.open(dest) as img:
                self.assertNotEqual(img.size, size)
                self.assertEqual(img.format, "PNG")
            saved = dest.read_bytes()
            fake = Mock()
            fake.encodeToData.return_value = None
            with self.assertRaises(ChartError):
                save_image(fake, dest)
            self.assertEqual(dest.read_bytes(), saved)
            jpeg = dest.with_suffix(".jpg")
            save_image(render(chart()), jpeg)
            with Image.open(jpeg) as img:
                self.assertEqual(img.format, "JPEG")
            self.assertEqual(list(Path(directory).glob(".preview-*.tmp")), [])


class MetadataAndCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "chart").mkdir()
        (self.root / "cover").mkdir()
        self.data = gzip.compress(raw([{"t": 480}]))
        self.source = self.root / "chart/123.ss"
        self.source.write_bytes(self.data)
        self.cover = self.root / "cover/jacket.png"
        Image.new("RGB", (16, 16), "blue").save(self.cover)
        song = {"title": "Test", "bandIds": [1], "cover": {"path": "cover/jacket.png"},
                "difficulties": {"expert": {"id": 123, "path": "chart/123.ss", "displayLevel": 25,
                                             "fullComboCount": 789, "sha256": hashlib.sha256(self.data).hexdigest()}}}
        self.songs = {"format": "OurNotes.bot.songs", "songs": [song]}
        self.write_songs()
        (self.root / "bands.json").write_text(json.dumps({"format": "OurNotes.bot.bands", "bands": [{"id": 1, "name": "Band"}]}))

    def write_songs(self):
        (self.root / "songs.json").write_text(json.dumps(self.songs), encoding="utf-8")

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([str(a) for a in args])
        return code, out.getvalue(), err.getvalue()

    def test_id_metadata_and_hash_validation(self):
        entry = find_resource(self.root, 123)
        self.assertEqual((entry.metadata.title, entry.metadata.artist, entry.metadata.level, entry.metadata.full_combo), ("Test", "Band", "25", 789))
        verify_resource_bytes(entry, self.data)
        with self.assertRaisesRegex(ChartError, "SHA256"):
            verify_resource_bytes(entry, b"wrong")

    def test_resource_paths_cannot_escape_root(self):
        self.songs["songs"][0]["cover"]["path"] = "../outside.png"
        self.write_songs()
        with self.assertRaisesRegex(ChartError, "outside"):
            find_resource(self.root, 123)

    def test_cli_id_and_plain_gzip_inputs(self):
        dest = self.root / "out.png"
        code, out, err = self.cli(123, dest, "--resources", self.root, "--mirror")
        self.assertEqual((code, err), (0, ""))
        self.assertIn('"scoreId": 123', out)
        self.assertTrue(dest.exists())
        self.source.write_bytes(raw([{"t": 480}]))
        code, out, err = self.cli(self.source, dest)
        self.assertEqual((code, err), (0, ""))
        self.assertIn('"encoding": "json"', out)

    def test_cli_mismatched_version_does_not_create_output(self):
        self.source.write_bytes(raw([{"t": 960}]))
        dest = self.root / "out.png"
        code, _, err = self.cli(123, dest, "--resources", self.root)
        self.assertEqual(code, 1)
        self.assertIn("SHA256", err)
        self.assertFalse(dest.exists())

    def test_cli_protects_inputs_and_checks_suffix(self):
        protected = [self.source, self.cover, self.root / "songs.json", self.root / "bands.json"]
        for dest in protected:
            saved = dest.read_bytes()
            code, _, err = self.cli(123, dest, "--resources", self.root)
            self.assertEqual(code, 1)
            self.assertIn("overwrite", err)
            self.assertEqual(dest.read_bytes(), saved)
        dest = self.root / "out.webp"
        code, _, err = self.cli(self.source, dest)
        self.assertEqual(code, 1)
        self.assertIn("suffix", err)
        self.assertFalse(dest.exists())


if __name__ == "__main__":
    unittest.main()
