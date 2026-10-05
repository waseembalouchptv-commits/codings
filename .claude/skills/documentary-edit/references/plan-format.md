# plan.json reference

All times are seconds on the **clean** timeline (after cuts). Beats must tile the
timeline: each beat's `end` equals the next beat's `start`.

```jsonc
{
  "duration": 412.6,
  "title": "Quetta",
  "hook_candidates": [{"start": 88.1, "end": 92.4, "text": "...", "score": 4}],
  "sections": [{"start": 0, "end": 61.2, "mood": "discovery", "first_line": "..."}],
  "beats": [
    {"id": "b000", "start": 0, "end": 6.4, "layout": "presenter",
     "camera": {"framing": "medium", "move": "none"},          // wide | medium | close; none | push_in | pull_out
     "text": [{"text": "QUETTA", "start": 2.1, "end": 4.6, "style": "label"}],  // label | callout | title
     "reason": "why this beat exists"},

    {"start": 6.4, "end": 12.9, "layout": "full",                // full | split | pip | presenter
     "visual": {"type": "map", "stops": [
        {"name": "Balochistan", "lon": 65.87, "lat": 28.32, "kind": "province", "t": 6.9},
        {"name": "Quetta", "lon": 67.02, "lat": 30.22, "kind": "city", "t": 9.8}]}},

    {"start": 12.9, "end": 17.0, "layout": "split",
     "visual": {"type": "year", "label": "1935", "caption": "THE EARTHQUAKE", "turning": true,
                "presenter_side": "right"}},                     // optional; default = side the face is on

    {"start": 17.0, "end": 21.0, "layout": "split",
     "visual": {"type": "stat", "value": "44", "unit": "percent", "caption": "OF PAKISTAN'S LAND"}},

    {"start": 21.0, "end": 29.0, "layout": "full",
     "visual": {"type": "timeline", "events": [
        {"label": "1666", "text": "Khanate of Kalat", "t": 0.4},   // t: seconds since the beat began
        {"label": "1876", "text": "Treaty with the British", "t": 2.5},
        {"label": "1947", "text": "Independence", "t": 5.0}]}},

    {"start": 29.0, "end": 34.0, "layout": "split",
     "visual": {"type": "quote", "text": "...", "attribution": "Gul Khan Naseer"}},

    {"start": 34.0, "end": 40.0, "layout": "pip", "pip_corner": "right",
     "visual": {"type": "image", "file": "kalat_1890s.jpg", "path": "assets/kalat_1890s.jpg",
                "license": "public-domain", "move": "push_in"}},  // push_in | pull_out | pan_left | pan_right
    {"start": 40.0, "end": 46.0, "layout": "full",
     "visual": {"type": "video", "file": "quetta_valley_drone.mp4", "path": "assets/quetta_valley_drone.mp4",
                "license": "own"}}
  ],
  "music": [{"start": 4.0, "end": 61.2, "mood": "discovery", "gain_db": 0}],  // mood "silence" = no music
  "sfx": [{"t": 6.4, "kind": "whoosh"}, {"t": 12.9, "kind": "impact"}],     // whoosh | impact | page
  "requests": [{"at": 88, "until": 104, "need": "b-roll", "narration": "...",
                "suggest": {"search": "...", "ai_prompt": "..."}, "why": "..."}]
}
```

Map stop `t` values are on the clean timeline, like beat times; the renderer
converts them. Timeline event `t` values count from the start of the beat.

Moods: `documentary`, `journey`, `discovery`, `emotional`, `tension`, `cultural`,
`silence`.

Rules the renderer enforces no matter what the plan says:

* Text that cannot be placed clear of the protected face zone is **dropped and logged**.
* In `split`, the presenter keeps their own half. The panel never overlaps the face.
* Captions over 4 words (titles: 6) are dropped. Captions are editorial, not subtitles.
* AI images with `depicts: historical` carry an "AI RECONSTRUCTION" badge.
