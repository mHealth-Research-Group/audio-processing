# Apply‑Blank Rewrite Plan (All‑Segments Only)

## Goal
Replace current apply‑blank with a faster, quality‑preserving implementation that:
- Loads original and modified timelines, sorts, and compares them.
- Processes only changed regions where modified type == `all`.
- Replaces those regions with a black template (looped if needed).
- Stream‑copies everything else bit‑for‑bit to preserve quality.

## High‑Level Flow (Drift‑Proof)
1) Inputs: `input_video`, `original_timeline.yaml`, `modified_timeline.yaml`, `blank_template.mp4`, `output_path`.
2) Load + normalize timelines (accept `Timeline`/`timeline`; normalize keys to lowercase; parse HH:MM:SS.mm to seconds).
3) Sort by start; diff: select segments where modified.type == `all` and (original.type != `all` or segment is new).
4) Merge overlapping/adjacent `all` ranges (≤10ms epsilon) to reduce ops.
5) Keyframe snapping (drift‑proofing):
   - Probe input keyframes once with ffprobe (`-skip_frame nokey`), build a sorted list of `pkt_pts_time`.
   - For each `all` range, snap start to previous keyframe, end to next keyframe; clamp to [0, total_duration].
   - Merge again after snapping.
6) Blank preparation:
   - The blank template comes from the same camera (codec/params match). For each snapped range, create a looped blank chunk:
     - If duration ≤ template duration: trim with stream copy; add silent AAC if needed (video copy only).
     - If longer: loop via concat demuxer, then `-t` to snapped duration (video copy), add silent AAC when needed.
7) Build output via concat with stream‑copy only:
   - Extract unaffected spans at keyframe boundaries using input‑seek (`-ss` before `-i`) + `-c copy`.
   - Concatenate in order with `-f concat -c copy -fflags +genpts -avoid_negative_ts make_zero`.
8) Validate: output duration matches input ±50ms; A/V continuous; log coverage metrics.

## Performance Strategy
- Stream‑copy for all original spans (no re‑encode → preserves quality).
- Blank chunks use camera template (video copy); add silent audio for concat compatibility.
- Modest parallelism (2–4 workers) for extractions; input‑seek to minimize decode.
- Merge adjacent/overlapping `all` to cut job count.

## Edge Cases
- `all` at file start/end; fully `all` → output becomes one looped blank.
- Zero/negative/overlapping times → sanitize and merge.
- Template shorter than segment → loop with concat + `-shortest`, clamp to duration.

## Tasks (Checklist)
- [ ] Timeline loader/normalizer (lowercase keys, seconds parsing)
- [ ] Diff original vs modified, select changed `all` segments
- [ ] Merge/normalize `all` ranges
- [ ] Probe keyframes; snap ranges to keyframes; re‑merge
- [ ] Unaffected span extraction (input‑seek + `-c copy`)
- [ ] Blank chunk generation (trim/loop; video copy; add silent audio if missing)
- [ ] Concat list builder + final `-c copy` mux
- [ ] Duration/stream validation + logs
- [ ] Optional: update timeline with `VideoRemoved`
- [ ] CLI wiring (`apply-blank` v2) + smoke tests

## Acceptance Criteria
- Only modified `all` ranges are changed; other spans are bit‑identical.
- Output duration matches input ±50ms; no timestamp drift.
- For segment counts > 200, completes substantially faster than legacy approach.
- Safe defaults: never overwrite input; suffix `_edited` when `-o` not set.

## Notes
- Future: add optional single‑pass GPU mode for extreme coverage; default remains stream‑copy to minimize quality loss.
