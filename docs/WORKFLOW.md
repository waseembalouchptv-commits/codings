# Workflow: from raw footage to the final cut

`docedit` does the parts that can be measured: cutting, face tracking, layout
safety, maps, graphics, sound, licences, and QC. Directing (choosing the hook,
deciding what each moment needs, checking facts) is a judgment step. You or Claude
do it by editing `plan.json` between `plan` and `render`.

```
raw.mp4 ──analyze──► transcript.json ──clean──► clean.mp4 + cleanup.json
                                                   │
                                     faces ◄───────┘──► faces.json
                                                   │
                                     plan ─────────► plan.json, edit_report.md, shot_list.md
                                                   │      ▲
                                        (review / direct / source assets)
                                                   │
                                     render ───────► final.mp4, render_log.json, credits.txt
                                                   │
                                     qc ───────────► qc_report.md
```

## 1. Ingest

```bash
python -m docedit run raw.mp4 -o projects/<slug> --assets assets/ --title "<Title>" --stop-after-plan
```

* Transcription runs locally with faster-whisper (`--language ur` or `en` helps).
  If you already have a transcript, pass `--transcript file.json|file.srt`.
  SRT timing is per line, not per word, so retake detection is weaker with it.
* `cleanup.json` lists every removed word with a reason (`filler`, `stutter`,
  `retake`, `false start`) and every tightened pause. If a cut removed something
  worth keeping (for example, a deliberate repetition for emphasis), fix the
  wording in `transcript.json` so the phrases no longer match, then re-run `clean`.

## 2. Direct (the step that makes it a documentary)

Read `transcript.json` (or `cleanup.json` → `words`) start to finish, then
`edit_report.md`. Then make these decisions in `plan.json`:

1. **Hook.** Is the opening line the strongest? `hook_candidates` lists the
   questions, numbers and contradictions. If a later line is stronger, play it
   first as a cold open: `python -m docedit clean -o projects/<slug> --cold-open 312.4-318.9`
   (source seconds; take them from the `src` field of the words in
   `cleanup.json`), then re-run `faces` and `plan --force`.
2. **Structure.** Name the sections (hook, context, story, turning point,
   revelation, conclusion). Set each section's music `mood`, or use `"silence"`
   where silence is stronger.
3. **Show, don't tell.** For each sentence, ask whether it can be shown.
   * Geography goes to a `map` with stops (the order of `stops` is the camera's
     journey).
   * Three or more dates in one passage go to a `timeline`.
   * Numbers go to a `stat`. Dates go to a `year`. A quoted source goes to a
     `quote`.
   * Places, people and objects go to B-roll from `assets/`. If nothing is
     licensed, add an entry to `requests` (it lands in `shot_list.md` with an AI
     prompt).
4. **Let the presenter own the screen** for emotional or decisive lines: use
   `presenter` with `close` + `push_in`, no text.
5. **Restraint.** Delete any beat that does not help the viewer understand,
   feel or remember. Keep captions to four words or fewer, in English.
6. **Accuracy.** Verify every date, number, name and map stop. Curated site
   coordinates are approximate (see `docedit/data/places_extra.json`).

Re-run `edit_report.md` generation with `python -m docedit plan ... --force` only
if you want to discard the edits. Otherwise go straight to render.

## 3. Source visuals

Use this order: own footage, then AI-generated, then public domain, CC0/CC-BY
(commercial use allowed), licensed stock.
Add every file to `assets/ledger.json` (format in `assets/ledger.example.json`).
For AI images of the past, set `"ai_generated": true, "depicts": "historical"`.
The renderer then labels them **AI RECONSTRUCTION**. Files without a permitted,
recorded licence are rejected automatically.

## 4. Render and QC

```bash
python -m docedit render -o projects/<slug> --assets assets/ --preview   # fast 720p check
python -m docedit render -o projects/<slug> --assets assets/             # 1080p master
python -m docedit qc     -o projects/<slug> --assets assets/
```

`qc_report.md` covers editing, visuals, face safety, audio and copyright.
**FAIL** items block delivery. **WARN** items need a human look. Paste
`credits.txt` into the video description.
