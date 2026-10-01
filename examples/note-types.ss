{
  "comment": "Handwritten synthetic chart illustrating renderer branches; not extracted game data.",
  "score": {
    "events": {
      "bpm": [{"t": 0, "bpm": 150}, {"t": 7680, "bpm": 180}],
      "sig": [{"t": 0, "sig": [4, 4]}, {"t": 11520, "sig": [3, 4]}],
      "skill": [3840], "fever": [[5760, 9600]],
      "call": [{"t": 1920, "timing": [1, 0, 1, 0]}]
    },
    "notes": [
      {"t": 480, "pos": 0, "size": 6},
      {"t": 960, "pos": 6, "size": 6, "crit": true},
      {"type": "flick", "t": 1440, "pos": 12, "size": 6},
      {"type": "flick", "t": 1920, "pos": 0, "size": 9, "dir": "left"},
      {"type": "flick", "t": 1920, "pos": 15, "size": 9, "dir": "right"},
      {"type": "trace", "t": 2400, "pos": 3, "size": 6},
      {"type": "trace", "t": 2640, "pos": 9, "size": 6},
      {"type": "trace", "t": 2880, "pos": 15, "size": 6},
      {"type": "long", "node": [
        {"t": 3360, "pos": 0, "size": 4, "ease": ["in", "out"]},
        {"type": "node", "t": 3840, "pos": "auto"},
        {"type": "trace", "t": 4320, "pos": 12, "size": 9},
        {"type": "flick", "t": 4800, "pos": 18, "size": 6, "dir": "right"}
      ]},
      {"type": "long", "node": [
        {"t": 5280, "pos": 0, "size": 6, "crit": true},
        {"t": 8160, "pos": 9, "size": 6, "crit": true, "ease": "out"},
        {"type": "flick", "t": 8640, "pos": 18, "size": 6, "crit": true}
      ]},
      {"type": "flick", "t": 9120, "pos": 0, "size": 6, "dir": "left"},
      {"type": "guide", "node": [
        {"t": 9120, "pos": 0, "size": 6, "alpha": "in", "ease": "out"},
        {"type": "trace", "t": 10080, "pos": 12, "size": 6},
        {"type": "trace", "t": 10560, "pos": 18, "size": 6}
      ]},
      {"type": "long", "node": [
        {"t": 11040, "pos": 0, "size": 6, "visible": false},
        {"type": "trace", "t": 12000, "pos": 6, "size": 12},
        {"t": 12480, "pos": "auto", "visible": false},
        {"t": 12960, "pos": 18, "size": 6, "visible": false}
      ]},
      {"type": "guide", "node": [
        {"t": 13440, "pos": 18, "size": 6, "alpha": "out"},
        {"type": "trace", "t": 14880, "pos": 0, "size": 6}
      ]}
    ]
  }
}
