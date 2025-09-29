# Bug Report and Fix Plan

## Overview
This document captures the current issues slowing down and destabilizing apply-blank/apply-edits, plus a prioritized to‑do plan to address them. References point to concrete code locations for verification.

## Findings
1) Incremental edits ignored in apply-blank
- Evidence: `src/media_processing.py:606` calls `_apply_blank_video_concat_method` which re-derives segments from the full timeline (`src/media_processing.py:901–969`). The passed `speech_segments` are not used to constrain work.
- Impact: Reprocesses whole timeline instead of only changed ranges; hours instead of minutes.

2) Over-broad scope (processes labels beyond 'all')
- Evidence: `src/media_processing.py:934–957` includes EFFECT_CONFIGS-driven mute/black on any labeled segment; legacy path also mutes 'speaking'/'conversation' (`src/commands.py:447–467`).
- Impact: Inflated segment count and work; contradicts “only segments marked for edits” expectation.

3) Slow seeking pattern in concat paths
- Evidence: Extraction uses `-i … -ss …` (decode-time seeking) for many segments (`src/media_processing.py:476–490, 499–518`).
- Impact: For M segments and T-length video, cost ≈ O(M·T/v_seek). With M≈300, T≈5400s, v_seek≈50× → ~9 hours.

4) compare_timelines misses label-only changes
- Evidence: Compares only `type` changes (`src/utils.py:149–155`), not label transitions (e.g., to mute/black).
- Impact: Changed effects may be missed; reuse logic applies stale spans.

5) Timeline schema drift
- Evidence: Enhanced helpers emit `Start/End/Type` (capitalized) (`src/audio_analysis.py:571–616`), while processing expects `start/end/type`.
- Impact: Segments emitted in the enhanced format won’t flow through downstream logic.

6) Unnecessary heavy model load in directory edits
- Evidence: `apply_timeline_edits_command` loads the ML model but doesn’t use it (`src/commands.py:340`).
- Impact: Startup latency and GPU/CPU memory pressure for a purely ffmpeg task.

7) Attribute safety and defaults
- Evidence: `args.effect_labels` used without guards (`src/commands.py:356–364`).
- Impact: Programmatic calls lacking this attribute may raise AttributeError.

8) In-place overwrite risk
- Evidence: `apply-blank` defaults `output` to the input path (`src/commands.py:411–420`).
- Impact: Accidental overwrite of originals when `-o` is omitted.

9) Gap semantics inconsistent
- Evidence: Gaps added as `type: gap, label: NoVideo` (`src/video_merger.py:90–110`) vs `type: silence, label: gap` (`src/video_merger.py:243–265`).
- Impact: Downstream filters keyed on type/label may misclassify.

10) Audio analysis path mismatch (pipeline variant)
- Evidence: Pipeline passes the video path to `analyze_audio_with_timeline` (`src/processing_pipeline.py:91`) instead of the extracted audio path.
- Impact: Torchaudio load may fail or cause redundant extraction; correctness and perf risk.

## To‑Do (Prioritized)
- Respect incremental scope
  - Make `apply_blank_video_to_segments` accept explicit segment lists and effect types; bypass full-timeline rebuild.
  - Acceptance: Only changed segments are processed; unchanged spans are stream-copied.

- Add single‑pass filter mode
  - Build one `filter_complex_script` combining drawbox (video) and volume=0 (audio) for unions of ('all'∪'black') and ('all'∪'mute'). Use NVENC if available.
  - Acceptance: Whole-file pass completes ≪ concat fan‑out for large edit sets; quality acceptable.

- Fix seeking and parallelism
  - Ensure `-ss` precedes `-i` for extractions; raise workers based on I/O; coalesce near-adjacent segments beyond 10ms.
  - Acceptance: Measurable wall‑time reduction on M≥100.

- Improve timeline diffing
  - Include label changes; tolerate key normalization (`start/Start`, etc.).
  - Acceptance: Changed_fraction mirrors manual edits accurately.

- Normalize timeline schema
  - Standardize on lowercase keys; add load-time normalization.
  - Acceptance: Mixed inputs still process correctly.

- Remove unused model loads and guard attrs
  - Don’t load ML for directory edits; guard `effect_labels` with defaults.
  - Acceptance: No model init for ffmpeg-only flows; no AttributeError.

- Safe output behavior
  - Prevent in-place overwrite unless explicitly requested.
  - Acceptance: Default outputs get a suffix; originals preserved.

- Align gap semantics
  - Choose canonical `type/label` and use consistently; document.
  - Acceptance: Downstream consumers see consistent gap markers.

- Tests and telemetry
  - Add unit tests for diffing/normalization and an integration smoke test; log M, unions, and chosen mode.
  - Acceptance: CI passes; logs show correct mode selection and segment counts.

