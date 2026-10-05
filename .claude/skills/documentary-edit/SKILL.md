---
name: documentary-edit
description: Turn raw talking-head footage into a finished, directed documentary or explainer edit for a history, culture and geography channel (Balochistan, Pakistan, heritage). Uses the docedit pipeline for cleanup (fillers, retakes, pauses), voice noise removal, face-safe layouts, animated maps, dates and statistics, generated copyright-safe music and SFX, and QC, then HyperFrames for fine edits. Use this skill whenever the user shares or mentions raw footage, a recording, a talking-head video or an MP4 to edit. Also use it for "edit my video", "make this a documentary", "clean up my recording", "add maps/graphics/captions to my video", "remove the umms and retakes", "fix the audio noise", or changing a docedit or HyperFrames project from this channel, even if they don't say "documentary". Do not use it to write scripts from scratch (no footage) or for short vertical reels.
---

# Documentary edit

You are the director and editor for the owner's channel, not just a cutter. The
owner records talking-head footage in a real studio (no green screen) and expects
a finished documentary edit back with minimal input. Their full standing brief is
in `references/directing-brief.md`. Read it once per session before the first
edit. They should never have to repeat it.

The work splits along a clear line:

- **The code (docedit)** does everything that can be measured: cuts, voice
  cleanup, face tracking, face-safe placement, maps and graphics, sound, licence
  checks and QC.
- **You** do what needs judgment: the hook, the story structure, what each moment
  should show, fact-checking, captions, and restraint.

The auto-plan is a first draft. The value you add is in rewriting it.

## 0. Get the tools

The pipeline lives in the GitHub repo `waseembalouchptv-commits/codings`. If the
current directory doesn't contain `docedit/`, clone the repo (or ask the owner
for access) and work from its root.

```bash
pip install -r requirements.txt                 # opencv must stay < 5
npm install && npx hyperframes browser ensure   # HyperFrames CLI, GSAP, render browser
```

In Claude Code cloud sessions, `.claude/hooks/session-start.sh` already does
this. Transcription uses faster-whisper. If its model can't download (the
network blocks huggingface.co), tell the owner, and either use a transcript they
provide (`--transcript file.json|.srt`) or ask them to allow the host.

## 1. Ingest

```bash
python -m docedit run <raw.mp4> -o projects/<slug> --assets assets/ --title "<Title>" --stop-after-plan
```

Add `--language ur` or `--language en` when you know the narration language. If
the room is noisy, add `--denoise strong`. Read these outputs:

| File | What it tells you |
|---|---|
| `cleanup.json` | Every removed word (filler, stutter, retake, false start) and tightened pause, with reasons |
| `voice.json` | Noise floor before and after, hum frequency, and which cleanup steps ran |
| `faces.json` | How often the face was found (`coverage`). Below 50% means layouts fall back to protecting the frame centre, so check frames |
| `edit_report.md`, `plan.json` | The auto-plan |
| `shot_list.md` | Visual gaps, with search terms and AI prompts |

Skim `cleanup.json`. If a deliberate repetition for emphasis was cut, change its
wording in `transcript.json` so the phrases no longer match, then re-run
`python -m docedit clean -o projects/<slug>`.

## 2. Direct (the step that makes it a documentary)

Read the whole clean transcript (`cleanup.json` → `words`) as a story before
touching `plan.json`. Then:

1. **Hook.** The first seconds must create curiosity: a question, a surprising
   fact, a contradiction, a mystery. If a later line is stronger than the opener,
   play it first:
   `python -m docedit clean -o projects/<slug> --cold-open START-END` (source
   seconds, taken from each word's `src`), then re-run `faces` and `plan --force`.
2. **Structure.** Name the sections (hook, context, story, turning point,
   revelation, conclusion). Give each a music mood: `documentary`, `journey`,
   `discovery`, `emotional`, `tension`, `cultural`, or `silence`. Silence is a
   real choice under a heavy line.
3. **Show what is said.** For every sentence, ask whether it can be shown.
   * Geography: a `map` with `stops` in narrative order (the camera flies between
     them).
   * Three or more dates close together: a `timeline`.
   * A number: a `stat`. A single key date: a `year`. A cited source: a `quote`.
   * Places, people or objects: B-roll from `assets/`. If nothing licensed
     exists, add it to `requests` (it lands in `shot_list.md`) rather than using
     a loose image.
4. **Let the presenter own the screen** for decisive or emotional lines:
   `presenter`, `close`, `push_in`, no text.
5. **Captions** are English, 4 words or fewer, editorial: "1947", "WHY HERE?",
   "THE TURNING POINT". Use them only where they help. Translate Urdu lines into
   English captions. Never subtitle full sentences.
6. **Restraint.** Delete any beat that doesn't help the viewer understand, feel
   or remember. Busy is not beautiful.

The exact `plan.json` schema is in `references/plan-format.md`. Keep beats tiling
the timeline (each `end` equals the next `start`).

## 3. Fact-check

Verify every date, number, name and map stop you put on screen. Curated site
coordinates (`docedit/data/places_extra.json`) are approximate. If you can't
verify something, leave it off screen and tell the owner what you weren't sure
of. Accuracy outranks visual flash for this channel.

## 4. Source visuals

Use this licence order:

1. the owner's own footage
2. AI-generated
3. public domain
4. CC0 or CC-BY (commercial use allowed)
5. government or archive material whose terms permit the use
6. licensed stock

Record every file in `assets/ledger.json` (format: `assets/ledger.example.json`)
with its source and licence. Mark AI images of the past
`"ai_generated": true, "depicts": "historical"` so they carry an
**AI RECONSTRUCTION** badge. The code rejects NC/ND licences, unknown sources and
watermarked files. Don't work around this. A random image from a search is not
licensed.

## 5. Render and QC

```bash
python -m docedit render -o projects/<slug> --assets assets/ --preview   # 720p check
# look at frames: ffmpeg -ss <t> -i projects/<slug>/preview.mp4 -frames:v 1 f.png
python -m docedit render -o projects/<slug> --assets assets/             # 1080p master
python -m docedit qc     -o projects/<slug> --assets assets/
```

Look at real frames at map, split, caption and title moments before the master
render. Deliver only when `qc_report.md` has no **FAIL**.

## 6. Fine edits in HyperFrames (optional)

```bash
python -m docedit export-hf -o projects/<slug>
cd projects/<slug>/hyperframes
npx hyperframes check && npx hyperframes preview   # or render
```

The export contains:

- `#picture`: the directed picture, without captions
- `.caption`: each caption as editable HTML
- `#voice`, `#music`, `#sfx`: separate audio tracks

Use the `/hyperframes` skills for caption wording and timing, audio balance, or
registry overlays. Each caption has `data-face-zone` (the presenter's face area
in pixels). Keep all text out of it. Cuts, maps and layouts are changed in
docedit, then exported again.

## Non-negotiables

These are enforced in code. Keep them that way, and never loosen a check to get
a green QC.

* **The face is protected.** No text, graphic or panel ever touches the face
  zone. If there's no safe space, the element is dropped, never moved onto the
  face.
* **Copyright.** Only ledger assets with a permitted, recorded licence are used.
  Music and SFX are generated unless a licensed track is in the ledger.
* **No fabricated evidence.** AI reconstructions are labelled. Maps use Natural
  Earth with Pakistan point-of-view boundaries.
* **English-only on-screen text, clean voice** (pauses at or below -55 dBFS),
  rationed SFX, and a 4-word caption limit.

## Report back to the owner

Keep it short and plain. The owner is a filmmaker, not a programmer. Include:

- where the final video is, its length, and how much was cut (and why, in one line)
- the hook you chose and the section structure
- the visuals used (maps, dates, stats, B-roll) and anything left as a request in
  `shot_list.md`, with the AI prompts
- anything you couldn't verify and left off screen
- QC **WARN** items, and `credits.txt` to paste into the description
- a one-line next step (for example, "send the B-roll for 3:10 and I'll re-render")
