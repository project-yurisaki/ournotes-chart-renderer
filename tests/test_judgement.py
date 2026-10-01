"""Gameplay Combo rules, including generated points and grid label ownership."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import skia
from on_chart import Config, build_judgements, parse_chart
from on_chart.renderer import RenderContext, _grid


def chart(notes, events=None):
    return parse_chart(json.dumps({'score': {'notes': notes, 'events': events or {}}}))


def long(nodes):
    return {'type': 'long', 'node': nodes}


class JudgementTests(unittest.TestCase):
    def test_eighth_note_hold_points_and_strict_combo_query(self):
        model = chart([long([{'t': 0}, {'t': 1920}])])
        timeline = build_judgements(model)
        self.assertEqual(timeline.times_ms, tuple(range(0, 2001, 250)))
        self.assertEqual(timeline.full_combo, 9)
        self.assertEqual((timeline.combo_at(0), timeline.combo_at(1000),
                          timeline.combo_at(1000, inclusive=True), timeline.combo_at(2001)), (0, 4, 5, 9))

    def test_auto_judged_nodes_reset_grid_but_hidden_nodes_do_not(self):
        nodes = [{'t': 0}, {'t': 300, 'pos': 'auto'}, {'t': 960}]
        auto = build_judgements(chart([long(nodes)]))
        self.assertEqual(auto.times_ms, (0, 312, 562, 812, 1000))
        hidden = build_judgements(chart([long([nodes[0], {**nodes[1], 'pos': 0, 'visible': False}, nodes[2]])]))
        self.assertEqual(hidden.times_ms, (0, 250, 500, 750, 1000))

    def test_endpoint_merges_respect_easing_and_simultaneous_points(self):
        first = long([{'t': 0, 'pos': 0}, {'t': 960, 'pos': 6}])
        second = long([{'t': 240, 'pos': 12}, {'t': 960, 'pos': 6}])
        timeline = build_judgements(chart([first, second]))
        self.assertEqual(timeline.full_combo, 8)
        self.assertEqual(timeline.times_ms.count(1000), 1)
        self.assertEqual(timeline.times_ms.count(500), 2)
        second['node'][-1]['ease'] = 'in'
        different = build_judgements(chart([first, second]))
        self.assertEqual(different.full_combo, 9)
        self.assertEqual(different.times_ms.count(1000), 2)

    def test_guides_absorb_singles_without_adding_hold_combo(self):
        timeline = build_judgements(chart([{'t': 0}, {'type': 'guide', 'node': [{'t': 0}, {'t': 1920}]}]))
        self.assertEqual(timeline.times_ms, (0,))
        self.assertEqual(timeline.entries[0].operation, 101)
        pure = build_judgements(chart([{'type': 'guide', 'node': [{'t': 0}, {'t': 1920}]}]))
        self.assertEqual(pure.full_combo, 0)

    def test_runtime_float_timing_is_not_source_tick_timing(self):
        # Native Create recomputes milliseconds from bar + float progress.
        model = chart([{'t': 70224}], {'bpm': [{'t': 0, 'bpm': 137}]})
        timeline = build_judgements(model)
        self.assertEqual(timeline.times_ms, (64073,))
        self.assertEqual(model.notes[0].time_ms, 64072)

    def test_tempo_and_signature_changes_keep_independent_judgements(self):
        model = chart([long([{'t': 0}, {'t': 3840}])],
                      {'bpm': [{'t': 0, 'bpm': 120}, {'t': 1920, 'bpm': 240}],
                       'sig': [{'t': 0, 'sig': [4, 4]}, {'t': 1920, 'sig': [2, 4]}]})
        timeline = build_judgements(model)
        self.assertEqual(timeline.full_combo, 17)
        self.assertEqual(timeline.times_ms, (*range(0, 2001, 250), *range(2125, 3001, 125)))

    def test_grid_displays_combo_and_preserves_time_labels(self):
        model = chart([{'t': t} for t in (0, 480, 960, 1440, 1920)])
        ctx = RenderContext(model, Config())
        surface = skia.Surface(ctx.layout.width, ctx.layout.height)
        with patch.object(ctx, 'text') as text:
            for column in ctx.layout.columns:
                _grid(ctx, surface.getCanvas(), column)
        labels = [call.args[1] for call in text.call_args_list]
        self.assertEqual(labels[::2], ['0', '4', '5'])
        self.assertEqual(labels[1::2], ['0:00.0', '0:02.0', '0:04.0'])


if __name__ == '__main__':
    unittest.main()
