Summary
  Apply-blank takes hours because it reprocesses far more than necessary, chains many small ffmpeg jobs with slow seeks, and often ignores the “only changed segments” intent. It also processes label-based effects (mute/black) across the entire timeline, not just 'all', and the incremental path calls a function that rebuilds segments from the full timeline
  instead of the changed set.

  Root Causes

  - Full-timeline work: Re-derives segments from the entire timeline even in “incremental” mode (src/media_processing.py:606, src/media_processing.py:901).
  - Over-broad scope: Also processes EFFECT_CONFIGS labels (mute/black) beyond 'all' (src/media_processing.py:934, src/commands.py:447).
  - Inefficient extraction: Many small segments, slow seeking (-ss after -i), limited parallelism (2 workers), heavy I/O churn (src/media_processing.py:1122, src/media_processing.py:1145).
  - Concat fragility: Mixing stream-copy and re-encoded segments forces fallbacks and retries.

  Goals

  - Use all available resources (GPU if present, all CPU threads).
  - Only touch changed regions: compare og vs edited timelines; reuse prior “process --complete” output for unchanged regions.
  - Produce one final video that applies:
      - 'all' → black video + muted audio
      - label 'mute' → mute audio only
      - label 'black' → black video only

  Proposed Strategy

  - Two-mode engine that auto-selects for speed and reliability:
      1. Single-pass GPU filter (default for most cases; fastest end-to-end, consistent)
          - Build one filter_complex_script with:
              - Video: chain of drawbox=w=iw:h=ih:color=black:t=fill enabled on union of ('all' ∪ 'black') time ranges
              - Audio: chain of volume=0 enabled on union of ('all' ∪ 'mute') time ranges
          - Use NVENC when available; otherwise libx264 with -threads 0.
          - Pros: One pass, maximum parallelism in encoder, robust. Cons: Re-encodes entire file (touches unchanged areas).
      2. Incremental stream-copy reuse (when changed_fraction < ~20% and no 'black-only' edits)
          - Compare og vs edited with utils.compare_timelines (src/utils.py:120).
          - Unchanged spans: copy from the previously processed output from “process --complete”.
          - Changed spans:
              - 'all': overlay black + mute audio on extracted chunk (re-encode, NVENC).
              - 'mute' only: audio volume filter with video stream copy (no re-encode).
          - Concatenate with concat filter (safe) or demuxer only if all streams match exactly.
          - Use input-seeking (-ss before -i) and raise parallelism to N=min(cores, fast storage capacity).
          - Pros: Avoids re-encoding most of the file. Cons: Complex stream matching; falls back to 1).

  We will choose Mode 1 by default; Mode 2 only when safe (no black-only changes and small changed_fraction).

  Implementation Plan

  - Build edit sets
      - Parse both timelines in data/VIDEO-7-7-25/ (og vs edited).
      - Time-union per effect:
          - black_ranges = union(type='all') ∪ union(label='black')
          - mute_ranges = union(type='all') ∪ union(label='mute')
      - Merge overlapping ranges to reduce filter count.
  - Single-pass GPU filter (preferred)
      - Probe width/height/fps/audio params with ffprobe.
      - Write edit_filter.txt:
          - Video chain: [0:v] drawbox(...,enable='between(t,a,b)'),drawbox(...),... [vout]
          - Audio chain: [0:a] volume=enable='between(t,a,b)':volume=0, ... [aout]
      - Command example:
          - ffmpeg -y -i INPUT -filter_complex_script edit_filter.txt -map [vout] -map [aout] -c:v h264_nvenc -preset fast -profile:v high -pix_fmt yuv420p -rc vbr -cq 23 -g 60 -c:a aac -b:a 128k OUTPUT
      - CPU fallback: -c:v libx264 -preset veryfast -threads 0.
  - Incremental reuse mode
      - Compute changed vs unchanged with utils.compare_timelines (src/utils.py:120).
      - Extract unchanged ranges from prior processed video via stream copy (-ss before -i, -c copy).
      - For changed ranges:
          - 'all': re-encode chunk with drawbox + volume filters; 'mute': audio-only filter with video copy.
      - Concat with filter (re-encodes final) if any chunk differs; demuxer only if bitstreams match exactly.
  - Fix current pitfalls (design)
      - Make apply_blank accept explicit segments; don’t rescan full timeline (src/media_processing.py:606).
      - Stop loading ML for directory apply-edits (src/commands.py:342).
      - Move to filter_complex_script for big graphs (already used for audio; extend to video).
      - Use input seeking for extraction tasks; increase parallel workers based on IO/CPU.

  For data/VIDEO-7-7-25/

  - Inputs: data/VIDEO-7-7-25/og_timeline.yaml, data/VIDEO-7-7-25/edited_timeline.yaml (names illustrative).
  - Recommended: Mode 1 one-pass GPU filter for predictable minutes-scale runtime, even with many edits.
  - Output: .../VIDEO-7-7-25/VIDEO_edited.mp4 with:
      - 'all' segments fully black + muted
      - 'mute' segments muted
      - 'black' segments blacked
      - Everything else unchanged visually and audibly (within re-encode transparency).

  Validation

  - Spot-check at boundaries; verify durations and A/V sync.
  - Confirm unions of ranges match edits; log segment counts before/after merge.
  - If needed, export a small preview around heavy-edit windows to sanity-check.
