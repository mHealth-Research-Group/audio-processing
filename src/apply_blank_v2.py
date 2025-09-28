import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple, Dict, Optional

from .utils import load_timeline, hhmmss_to_seconds, mmss_to_seconds, run_subprocess_with_encoding
from .video_merger import create_gap_video_from_blank, get_video_properties


def _parse_time_str(ts: str) -> float:
    try:
        # Prefer HH:MM:SS parsing when present
        if ts.count(":") >= 2:
            return hhmmss_to_seconds(ts)
        return mmss_to_seconds(ts)
    except Exception:
        try:
            return float(ts)
        except Exception:
            return 0.0


def _collect_all_changes(original: Dict, modified: Dict) -> List[Tuple[float, float]]:
    """Return list of (start,end) seconds where modified.type == 'all' and original differs or missing."""
    orig = original.get("timeline", []) or []
    mod = modified.get("timeline", []) or []

    # Map original by (start,end)
    orig_map = {(
        s.get("start"),
        s.get("end"),
    ): s for s in orig}

    result = []
    for seg in mod:
        if seg.get("type") != "all":
            continue
        key = (seg.get("start"), seg.get("end"))
        oseg = orig_map.get(key)
        if (oseg is None) or (oseg.get("type") != "all"):
            start = _parse_time_str(seg.get("start", "0:00"))
            end = _parse_time_str(seg.get("end", "0:00"))
            if end > start:
                result.append((start, end))
    return sorted(result, key=lambda x: x[0])


def _merge_ranges(ranges: List[Tuple[float, float]], eps: float = 0.01) -> List[Tuple[float, float]]:
    if not ranges:
        return []
    ranges = sorted(ranges, key=lambda x: x[0])
    merged = [list(ranges[0])]
    for s, e in ranges[1:]:
        cs, ce = merged[-1]
        if s <= ce + eps:  # overlap or near-adjacent
            merged[-1][1] = max(ce, e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def _probe_keyframes(video: Path) -> List[float]:
    """Return sorted list of keyframe PTS times in seconds for v:0."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-skip_frame",
        "nokey",
        "-show_frames",
        "-show_entries",
        "frame=pkt_pts_time",
        "-of",
        "json",
        str(video),
    ]
    res = run_subprocess_with_encoding(cmd, capture_output=True, text=True, check=True)
    data = json.loads(res.stdout or "{}")
    frames = data.get("frames", [])
    times = []
    for fr in frames:
        t = fr.get("pkt_pts_time")
        if t is not None:
            try:
                times.append(float(t))
            except Exception:
                pass
    if not times or times[0] > 0.0:
        times = [0.0] + times
    return sorted(times)


def _snap_to_keyframes(ranges: List[Tuple[float, float]], keyframes: List[float], total_duration: float) -> List[Tuple[float, float]]:
    if not ranges:
        return []
    if not keyframes:
        return ranges

    snapped = []
    for start, end in ranges:
        # prev keyframe <= start
        # next keyframe >= end
        # Binary search could be used; linear is acceptable per segment count
        s_snap = 0.0
        e_snap = total_duration
        for i, t in enumerate(keyframes):
            if t <= start:
                s_snap = t
            if t >= end:
                e_snap = t
                break
        s_snap = max(0.0, s_snap)
        e_snap = min(total_duration, e_snap)
        if e_snap > s_snap:
            snapped.append((s_snap, e_snap))
    return _merge_ranges(snapped)


def _extract_span(input_video: Path, start: float, duration: float, out_path: Path) -> None:
    cmd = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.6f}",
        "-i",
        str(input_video),
        "-t",
        f"{duration:.6f}",
        "-c",
        "copy",
        str(out_path),
    ]
    run_subprocess_with_encoding(cmd, check=True)


def _probe_input_audio(input_video: Path) -> Dict[str, Optional[str]]:
    """Probe audio parameters from the input video (codec, sample_rate, channels, layout, bit_rate).

    Returns empty dict if no audio stream is present.
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_name,channels,sample_rate,channel_layout,bit_rate",
        "-of",
        "json",
        str(input_video),
    ]
    try:
        res = run_subprocess_with_encoding(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout or "{}")
        streams = data.get("streams", [])
        if not streams:
            return {}
        s = streams[0]
        return {
            "codec_name": s.get("codec_name"),
            "sample_rate": s.get("sample_rate"),
            "channels": s.get("channels"),
            "channel_layout": s.get("channel_layout"),
            "bit_rate": s.get("bit_rate"),
        }
    except Exception:
        return {}


def _make_blank_chunk(blank_template: Path, duration: float, out_path: Path, audio_params: Dict[str, Optional[str]]) -> None:
    """Create a blank chunk of exact duration using camera template.

    - If duration <= template: copy-trim and add silent audio
    - Else: loop via concat demuxer and then trim to duration
    """
    props = get_video_properties(blank_template)
    template_dur = props["duration"] or 0.0

    # Simple case: one trim
    if duration <= template_dur + 1e-3:
        # Add silent audio matching input when present; else keep video-only
        if audio_params:
            sample_rate = audio_params.get("sample_rate") or "32000"
            channel_layout = audio_params.get("channel_layout") or ("mono" if str(audio_params.get("channels")) == "1" else "stereo")
            bit_rate = audio_params.get("bit_rate") or "96000"
            cmd = [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(blank_template),
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=channel_layout={channel_layout}:sample_rate={sample_rate}",
                "-t",
                f"{duration:.6f}",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                bit_rate,
                "-shortest",
                str(out_path),
            ]
        else:
            cmd = [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(blank_template),
                "-t",
                f"{duration:.6f}",
                "-c:v",
                "copy",
                str(out_path),
            ]
        run_subprocess_with_encoding(cmd, check=True)
        return

    # Looping case
    loops = int(math.ceil(duration / max(0.001, template_dur)))
    temp_list = out_path.parent / f"blank_loop_{out_path.stem}.txt"
    with open(temp_list, "w", encoding="utf-8") as f:
        for _ in range(loops):
            f.write(f"file '{blank_template.resolve()}'\n")

    try:
        if audio_params:
            sample_rate = audio_params.get("sample_rate") or "32000"
            channel_layout = audio_params.get("channel_layout") or ("mono" if str(audio_params.get("channels")) == "1" else "stereo")
            bit_rate = audio_params.get("bit_rate") or "96000"
            cmd = [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(temp_list),
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=channel_layout={channel_layout}:sample_rate={sample_rate}",
                "-t",
                f"{duration:.6f}",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                bit_rate,
                "-shortest",
                str(out_path),
            ]
        else:
            cmd = [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(temp_list),
                "-t",
                f"{duration:.6f}",
                "-c:v",
                "copy",
                str(out_path),
            ]
        run_subprocess_with_encoding(cmd, check=True)
    finally:
        try:
            temp_list.unlink()
        except Exception:
            pass


def apply_blank_v2(
    input_video: Path,
    original_timeline_path: Path,
    modified_timeline_path: Path,
    blank_template: Path,
    output_path: Path,
) -> int:
    """Drift‑proof, quality‑preserving apply‑blank for 'all' segments only."""
    input_video = Path(input_video)
    original_timeline_path = Path(original_timeline_path)
    modified_timeline_path = Path(modified_timeline_path)
    blank_template = Path(blank_template)
    output_path = Path(output_path)

    if not input_video.exists():
        print(f"Error: Input video not found: {input_video}")
        return 1
    if not original_timeline_path.exists() or not modified_timeline_path.exists():
        print("Error: Both original and modified timeline files are required")
        return 1
    if not blank_template.exists():
        print(f"Error: Blank template not found: {blank_template}")
        return 1

    # Load timelines (normalized)
    original = load_timeline(original_timeline_path)
    modified = load_timeline(modified_timeline_path)

    # Collect changed 'all' ranges
    all_ranges = _merge_ranges(_collect_all_changes(original, modified))
    if not all_ranges:
        # Nothing to do: copy original
        run_subprocess_with_encoding(["ffmpeg", "-y", "-i", str(input_video), "-c", "copy", str(output_path)], check=True)
        print("No 'all' changes detected. Copied input to output.")
        return 0

    # Probe total duration and keyframes
    props = get_video_properties(input_video)
    total_dur = props.get("duration", 0.0) or 0.0
    keyframes = _probe_keyframes(input_video)
    audio_params = _probe_input_audio(input_video)

    # Snap to keyframes, merge
    snapped = _snap_to_keyframes(all_ranges, keyframes, total_dur)

    # Plan parts: unaffected and blank chunks
    temp_dir = input_video.parent / "temp_apply_blank_v2"
    temp_dir.mkdir(exist_ok=True)

    concat_list: List[str] = []
    current = 0.0
    seg_index = 0

    try:
        for s, e in snapped:
            if current < s:
                dur = s - current
                out_seg = temp_dir / f"orig_{seg_index:05d}.mp4"
                _extract_span(input_video, current, dur, out_seg)
                concat_list.append(str(out_seg.resolve()))
                seg_index += 1

            dur_blank = e - s
            blank_out = temp_dir / f"blank_{seg_index:05d}.mp4"
            _make_blank_chunk(blank_template, dur_blank, blank_out, audio_params)
            concat_list.append(str(blank_out.resolve()))
            seg_index += 1
            current = e

        if current < total_dur:
            dur = total_dur - current
            out_seg = temp_dir / f"orig_{seg_index:05d}.mp4"
            _extract_span(input_video, current, dur, out_seg)
            concat_list.append(str(out_seg.resolve()))

        # Write concat file
        concat_file = temp_dir / "concat_list.txt"
        with open(concat_file, "w", encoding="utf-8") as f:
            for p in concat_list:
                f.write(f"file '{p}'\n")

        # Final concat with stream copy
        cmd = [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-fflags",
            "+genpts",
            "-avoid_negative_ts",
            "make_zero",
            "-c",
            "copy",
            str(output_path),
        ]
        run_subprocess_with_encoding(cmd, check=True)
        print(f"Created edited file: {output_path}")
        return 0

    finally:
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass
