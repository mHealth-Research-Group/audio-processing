"""
Optimized Apply-Blank Implementation

This module implements the two-mode engine described in implementation-plan.md:
1. Single-pass GPU filter (Mode 1) - Default for most cases
2. Incremental stream-copy reuse (Mode 2) - For small changed fractions

Key improvements:
- Only processes changed regions by comparing timelines
- Single ffmpeg command with filter_complex_script for efficiency
- Auto-selects optimal mode based on change characteristics
- Uses GPU acceleration when available
"""

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .utils import (
    compare_timelines,
    load_timeline,
    hhmmss_to_seconds,
)


def build_edit_sets(timeline_data: Dict) -> Dict[str, List[Tuple[float, float]]]:
    """
    Build edit sets from timeline data according to implementation plan.

    Returns:
        dict with 'black_ranges' and 'mute_ranges' as unions of time ranges
    """
    timeline_segments = timeline_data.get("timeline", [])

    black_ranges = []
    mute_ranges = []

    for segment in timeline_segments:
        segment_type = segment.get("type", "")
        segment_label = segment.get("label", "")

        # Convert time strings to seconds
        start_seconds = hhmmss_to_seconds(segment["start"])
        end_seconds = hhmmss_to_seconds(segment["end"])
        time_range = (start_seconds, end_seconds)

        # black_ranges = union(type='all') ∪ union(label='black')
        if segment_type == "all" or segment_label == "black":
            black_ranges.append(time_range)

        # mute_ranges = union(type='all') ∪ union(label='mute')
        if segment_type == "all" or segment_label == "mute":
            mute_ranges.append(time_range)

    # Merge overlapping ranges to reduce filter count
    black_ranges = merge_overlapping_ranges(black_ranges)
    mute_ranges = merge_overlapping_ranges(mute_ranges)

    return {"black_ranges": black_ranges, "mute_ranges": mute_ranges}


def merge_overlapping_ranges(ranges: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Merge overlapping time ranges to reduce filter count."""
    if not ranges:
        return []

    # Sort by start time
    sorted_ranges = sorted(ranges, key=lambda x: x[0])
    merged = [sorted_ranges[0]]

    for current_start, current_end in sorted_ranges[1:]:
        last_start, last_end = merged[-1]

        # If ranges overlap or are adjacent, merge them
        if current_start <= last_end + 0.1:  # Small tolerance for adjacent ranges
            merged[-1] = (last_start, max(last_end, current_end))
        else:
            merged.append((current_start, current_end))

    return merged


def probe_video_params(input_video: Path) -> Dict:
    """Probe video parameters with ffprobe."""
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(input_video)]

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    probe_data = json.loads(result.stdout)

    # Extract video and audio stream info
    video_stream = None
    audio_stream = None

    for stream in probe_data["streams"]:
        if stream["codec_type"] == "video" and not video_stream:
            video_stream = stream
        elif stream["codec_type"] == "audio" and not audio_stream:
            audio_stream = stream

    return {
        "duration": float(probe_data["format"]["duration"]),
        "video": video_stream,
        "audio": audio_stream,
        "width": int(video_stream["width"]) if video_stream else None,
        "height": int(video_stream["height"]) if video_stream else None,
        "fps": eval(video_stream["r_frame_rate"]) if video_stream else None,
    }


def create_filter_complex_script(
    edit_sets: Dict[str, List[Tuple[float, float]]], video_params: Dict, script_path: Path
) -> None:
    """Create filter_complex_script for single-pass GPU processing."""

    black_ranges = edit_sets["black_ranges"]
    mute_ranges = edit_sets["mute_ranges"]

    width = video_params["width"]
    height = video_params["height"]

    filters = []

    # Video chain: drawbox filters for black ranges
    video_input = "[0:v]"
    if black_ranges:
        for i, (start, end) in enumerate(black_ranges):
            enable_expr = f"between(t,{start},{end})"
            filter_name = f"drawbox=w={width}:h={height}:color=black:t=fill:enable='{enable_expr}'"
            filters.append(f"{video_input}{filter_name}[v{i}]")
            video_input = f"[v{i}]"

    # Final video output
    if video_input == "[0:v]":
        # No black ranges, copy video directly
        filters.append(f"{video_input}copy[vout]")
    else:
        # Rename the last video filter output
        filters[-1] = filters[-1].replace(f"[v{len(black_ranges) - 1}]", "[vout]")

    # Audio chain: volume filters for mute ranges
    audio_input = "[0:a]"
    if mute_ranges:
        for i, (start, end) in enumerate(mute_ranges):
            enable_expr = f"between(t,{start},{end})"
            filter_name = f"volume=enable='{enable_expr}':volume=0"
            filters.append(f"{audio_input}{filter_name}[a{i}]")
            audio_input = f"[a{i}]"

    # Final audio output
    if audio_input == "[0:a]":
        # No mute ranges, copy audio directly
        filters.append(f"{audio_input}copy[aout]")
    else:
        # Rename the last audio filter output
        filters[-1] = filters[-1].replace(f"[a{len(mute_ranges) - 1}]", "[aout]")

    # Write filter script
    filter_script = ";\n".join(filters)
    with open(script_path, "w") as f:
        f.write(filter_script)


def detect_gpu_capability() -> Dict[str, bool]:
    """Detect available GPU encoding capabilities."""
    capabilities = {"nvenc": False, "vaapi": False, "videotoolbox": False}

    try:
        # Check for NVENC (NVIDIA)
        result = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10)
        if "h264_nvenc" in result.stdout:
            capabilities["nvenc"] = True
        if "h264_vaapi" in result.stdout:
            capabilities["vaapi"] = True
        if "h264_videotoolbox" in result.stdout:
            capabilities["videotoolbox"] = True
    except (subprocess.SubprocessError, subprocess.TimeoutExpired):
        pass

    return capabilities


def apply_blank_mode1_single_pass(
    input_video: Path, output_path: Path, edit_sets: Dict[str, List[Tuple[float, float]]], trim_first_frame: bool = True
) -> bool:
    """
    Mode 1: Single-pass GPU filter implementation.

    Uses one FFmpeg command with filter_complex_script for maximum efficiency.
    """
    print("Mode 1: Single-pass GPU filter processing...")

    # Probe video parameters
    video_params = probe_video_params(input_video)
    print(f"Video: {video_params['width']}x{video_params['height']} @ {video_params['fps']:.2f}fps")

    black_count = len(edit_sets["black_ranges"])
    mute_count = len(edit_sets["mute_ranges"])
    print(f"Processing {black_count} black ranges, {mute_count} mute ranges")

    # Create filter script
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        script_path = Path(f.name)

    try:
        create_filter_complex_script(edit_sets, video_params, script_path)

        # Detect GPU capabilities
        gpu_caps = detect_gpu_capability()

        # Build FFmpeg command
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_video),
            "-filter_complex_script",
            str(script_path),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
        ]

        # GPU encoding if available
        if gpu_caps["nvenc"]:
            cmd.extend(
                [
                    "-c:v",
                    "h264_nvenc",
                    "-preset",
                    "fast",
                    "-profile:v",
                    "high",
                    "-pix_fmt",
                    "yuv420p",
                    "-rc",
                    "vbr",
                    "-cq",
                    "23",
                    "-g",
                    "60",
                ]
            )
            print("Using NVENC GPU encoding")
        elif gpu_caps["vaapi"]:
            cmd.extend(["-c:v", "h264_vaapi", "-vaapi_device", "/dev/dri/renderD128"])
            print("Using VAAPI GPU encoding")
        elif gpu_caps["videotoolbox"]:
            cmd.extend(["-c:v", "h264_videotoolbox"])
            print("Using VideoToolbox GPU encoding")
        else:
            # CPU fallback
            cmd.extend(["-c:v", "libx264", "-preset", "veryfast", "-threads", "0"])
            print("Using CPU encoding (no GPU detected)")

        # Audio encoding
        cmd.extend(["-c:a", "aac", "-b:a", "128k"])

        # Output path
        if trim_first_frame:
            temp_output = output_path.parent / f"temp_{output_path.name}"
            cmd.append(str(temp_output))
        else:
            cmd.append(str(output_path))

        # Execute FFmpeg
        print(f"Executing: {' '.join(cmd[:10])}...")
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"FFmpeg error: {result.stderr}")
            return False

        # Apply privacy trimming if requested
        if trim_first_frame:
            from .media_processing import trim_first_frame_func

            print("Applying privacy trimming...")
            trim_first_frame_func(temp_output, output_path, first_frame_duration=0.033)
            temp_output.unlink()

        print(f"Mode 1 processing completed: {output_path}")
        return True

    except Exception as e:
        print(f"Error in Mode 1 processing: {e}")
        return False
    finally:
        if script_path.exists():
            script_path.unlink()


def apply_blank_mode2_incremental(
    input_video: Path,
    output_path: Path,
    original_timeline: Dict,
    modified_timeline: Dict,
    blank_video_path: Path,
    trim_first_frame: bool = True,
) -> bool:
    """
    Mode 2: Incremental stream-copy reuse implementation.

    Only processes changed segments, reuses unchanged portions from original processed video.
    """
    print("Mode 2: Incremental stream-copy processing...")

    # Compare timelines to identify what actually changed
    comparison = compare_timelines(original_timeline, modified_timeline)
    changed_segments = comparison["changed_segments"]
    unchanged_segments = comparison["unchanged_segments"]

    print(f"Change analysis: {comparison['change_percentage']:.1f}% changed")
    print(f"  Changed segments: {len(changed_segments)}")
    print(f"  Unchanged segments: {len(unchanged_segments)}")

    if not changed_segments:
        print("No segments changed - copying original video")
        import shutil

        shutil.copy2(input_video, output_path)
        return True

    # Create temporary working directory
    temp_dir = output_path.parent / f"temp_incremental_{output_path.stem}"
    temp_dir.mkdir(exist_ok=True)

    try:
        # Extract unchanged segments from original processed video (stream copy)
        unchanged_files = _extract_unchanged_segments(input_video, unchanged_segments, temp_dir)

        # Process changed segments with appropriate effects
        changed_files = _process_changed_segments(input_video, changed_segments, blank_video_path, temp_dir)

        # Create chronological segment list for concatenation
        all_segments = _create_chronological_segment_list(
            unchanged_segments, changed_segments, unchanged_files, changed_files
        )

        # Concatenate all segments in chronological order
        success = _concatenate_segments_chronologically(all_segments, output_path, temp_dir)

        if success and trim_first_frame:
            # Apply privacy trimming
            from .media_processing import trim_first_frame_func

            print("Applying privacy trimming...")
            temp_output = output_path.parent / f"temp_{output_path.name}"
            # Move the concatenated output to temp location for trimming
            output_path.rename(temp_output)
            trim_first_frame_func(temp_output, output_path, first_frame_duration=0.033)
            if temp_output.exists():
                temp_output.unlink()

        return success

    except Exception as e:
        print(f"Mode 2 processing failed: {e}")
        print("Falling back to Mode 1...")
        edit_sets = build_edit_sets(modified_timeline)
        return apply_blank_mode1_single_pass(input_video, output_path, edit_sets, trim_first_frame)
    finally:
        # Clean up temporary directory
        if temp_dir.exists():
            import shutil

            shutil.rmtree(temp_dir, ignore_errors=True)


def auto_select_mode(original_timeline: Optional[Dict], modified_timeline: Dict) -> Tuple[int, str]:
    """
    Auto-select optimal processing mode based on change characteristics.

    Returns:
        (mode_number, reason)
    """
    # Always use Mode 1 by default as per implementation plan
    default_mode = (1, "Single-pass GPU filter (default)")

    if not original_timeline:
        return default_mode

    # Compare timelines to analyze changes
    comparison = compare_timelines(original_timeline, modified_timeline)
    change_percentage = comparison["change_percentage"]

    # Check for black-only edits
    has_black_only = False
    for segment in comparison["changed_segments"]:
        if segment.get("label") == "black" and segment.get("type") != "all":
            has_black_only = True
            break

    # Mode 2 criteria: changed_fraction < ~20% and no 'black-only' edits
    if change_percentage < 20 and not has_black_only:
        return (2, f"Incremental mode ({change_percentage:.1f}% changed, no black-only edits)")

    return default_mode


def _extract_unchanged_segments(input_video: Path, unchanged_segments: List[Dict], temp_dir: Path) -> Dict[str, Path]:
    """
    Extract unchanged segments from original video using stream copy (-ss before -i).

    Returns mapping of segment_key -> extracted_file_path
    """
    print(f"Extracting {len(unchanged_segments)} unchanged segments...")

    unchanged_files = {}

    for i, segment in enumerate(unchanged_segments):
        start_str = segment.get("start", "0:00.000")
        end_str = segment.get("end", "0:00.000")

        # Convert to seconds for duration calculation
        start_seconds = hhmmss_to_seconds(start_str)
        end_seconds = hhmmss_to_seconds(end_str)
        duration_seconds = end_seconds - start_seconds

        if duration_seconds <= 0.1:
            continue  # Skip very short segments

        # Create segment key for chronological ordering
        segment_key = f"{start_seconds:010.3f}_{i:04d}_unchanged"

        # Output file for this segment
        segment_file = temp_dir / f"unchanged_{i:04d}.mp4"

        # Extract using stream copy with input seeking for efficiency
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start_seconds),  # Input seeking for efficiency
            "-i",
            str(input_video),
            "-t",
            str(duration_seconds),
            "-c",
            "copy",  # Stream copy - no re-encoding
            "-avoid_negative_ts",
            "make_zero",
            str(segment_file),
        ]

        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            unchanged_files[segment_key] = segment_file

            if i % 10 == 0:
                print(f"  Extracted unchanged segment {i + 1}/{len(unchanged_segments)}")

        except subprocess.CalledProcessError as e:
            print(f"Failed to extract unchanged segment {i}: {e}")
            continue

    print(f"Successfully extracted {len(unchanged_files)} unchanged segments")
    return unchanged_files


def _process_changed_segments(
    input_video: Path, changed_segments: List[Dict], blank_video_path: Path, temp_dir: Path
) -> Dict[str, Path]:
    """
    Process changed segments with appropriate effects based on their new type/label.

    Returns mapping of segment_key -> processed_file_path
    """
    print(f"Processing {len(changed_segments)} changed segments...")

    changed_files = {}
    gpu_caps = detect_gpu_capability()

    for i, segment in enumerate(changed_segments):
        start_str = segment.get("start", "0:00.000")
        end_str = segment.get("end", "0:00.000")
        segment_type = segment.get("type", "")
        segment_label = segment.get("label", "")

        # Convert to seconds
        start_seconds = hhmmss_to_seconds(start_str)
        end_seconds = hhmmss_to_seconds(end_str)
        duration_seconds = end_seconds - start_seconds

        if duration_seconds <= 0.1:
            continue  # Skip very short segments

        # Create segment key for chronological ordering
        segment_key = f"{start_seconds:010.3f}_{i:04d}_changed"

        # Output file for this segment
        segment_file = temp_dir / f"changed_{i:04d}.mp4"

        # Determine what effects to apply
        needs_black = segment_type == "all" or segment_label == "black"
        needs_mute = segment_type == "all" or segment_label == "mute"

        if needs_black and needs_mute:
            # Full blank replacement - use blank video template
            _process_segment_full_blank(input_video, start_seconds, duration_seconds, blank_video_path, segment_file)
        elif needs_black:
            # Video blackout only - use drawbox filter with video stream copy for audio
            _process_segment_video_black(input_video, start_seconds, duration_seconds, segment_file, gpu_caps)
        elif needs_mute:
            # Audio mute only - use volume filter with video stream copy
            _process_segment_audio_mute(input_video, start_seconds, duration_seconds, segment_file)
        else:
            # No effects needed - stream copy
            _process_segment_stream_copy(input_video, start_seconds, duration_seconds, segment_file)

        changed_files[segment_key] = segment_file

        if i % 5 == 0:
            print(f"  Processed changed segment {i + 1}/{len(changed_segments)}")

    print(f"Successfully processed {len(changed_files)} changed segments")
    return changed_files


def _process_segment_full_blank(
    input_video: Path, start_seconds: float, duration_seconds: float, blank_video_path: Path, output_file: Path
):
    """Process segment with full blank replacement using blank template with looping."""
    if duration_seconds <= 0.1:
        raise ValueError(f"Invalid duration: {duration_seconds}")

    cmd = [
        "ffmpeg",
        "-y",
        "-stream_loop",
        "-1",  # Loop blank template indefinitely
        "-i",
        str(blank_video_path),
        "-t",
        str(duration_seconds),  # Cut to exact duration needed
        "-c",
        "copy",  # Maintain exact codec match
        str(output_file),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)


def _process_segment_video_black(
    input_video: Path, start_seconds: float, duration_seconds: float, output_file: Path, gpu_caps: Dict[str, bool]
):
    """Process segment with video blackout only (preserve original audio)."""
    # Get video params to create proper black overlay
    video_params = probe_video_params(input_video)
    width = video_params["width"]
    height = video_params["height"]

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start_seconds),
        "-i",
        str(input_video),
        "-t",
        str(duration_seconds),
        "-vf",
        f"drawbox=w={width}:h={height}:color=black:t=fill",
        "-map",
        "0:v",
        "-map",
        "0:a",
        "-c:a",
        "copy",  # Copy audio unchanged
    ]

    # Add video encoding
    if gpu_caps["nvenc"]:
        cmd.extend(["-c:v", "h264_nvenc", "-preset", "fast"])
    else:
        cmd.extend(["-c:v", "libx264", "-preset", "veryfast", "-threads", "0"])

    cmd.append(str(output_file))
    subprocess.run(cmd, capture_output=True, text=True, check=True)


def _process_segment_audio_mute(input_video: Path, start_seconds: float, duration_seconds: float, output_file: Path):
    """Process segment with audio mute only (preserve original video)."""
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start_seconds),
        "-i",
        str(input_video),
        "-t",
        str(duration_seconds),
        "-af",
        "volume=0",  # Mute audio
        "-map",
        "0:v",
        "-map",
        "0:a",
        "-c:v",
        "copy",  # Copy video unchanged
        "-c:a",
        "aac",
        "-b:a",
        "128k",  # Re-encode audio with volume=0
        str(output_file),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)


def _process_segment_stream_copy(input_video: Path, start_seconds: float, duration_seconds: float, output_file: Path):
    """Process segment with no effects - stream copy only."""
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start_seconds),
        "-i",
        str(input_video),
        "-t",
        str(duration_seconds),
        "-c",
        "copy",
        str(output_file),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)


def _create_chronological_segment_list(
    unchanged_segments: List[Dict],
    changed_segments: List[Dict],
    unchanged_files: Dict[str, Path],
    changed_files: Dict[str, Path],
) -> List[Tuple[float, Path]]:
    """
    Create chronological list of all segments for concatenation.

    Returns list of (start_time, file_path) tuples sorted by start_time
    """
    all_segments = []

    # Add unchanged segments
    for segment in unchanged_segments:
        start_seconds = hhmmss_to_seconds(segment.get("start", "0:00.000"))
        # Find corresponding file
        for key, file_path in unchanged_files.items():
            if key.startswith(f"{start_seconds:010.3f}"):
                all_segments.append((start_seconds, file_path))
                break

    # Add changed segments
    for segment in changed_segments:
        start_seconds = hhmmss_to_seconds(segment.get("start", "0:00.000"))
        # Find corresponding file
        for key, file_path in changed_files.items():
            if key.startswith(f"{start_seconds:010.3f}"):
                all_segments.append((start_seconds, file_path))
                break

    # Sort by start time
    all_segments.sort(key=lambda x: x[0])

    print(f"Created chronological segment list with {len(all_segments)} segments")
    return all_segments


def _concatenate_segments_chronologically(
    all_segments: List[Tuple[float, Path]], output_path: Path, temp_dir: Path
) -> bool:
    """
    Concatenate all segments in chronological order using FFmpeg concat demuxer.

    Uses concat demuxer for efficiency when possible, falls back to filter for stream mismatches.
    """
    if not all_segments:
        print("No segments to concatenate")
        return False

    if len(all_segments) == 1:
        # Single segment - just copy/move the file
        import shutil

        shutil.copy2(all_segments[0][1], output_path)
        print("Single segment - copied directly")
        return True

    # Create concat list file
    concat_list_path = temp_dir / "concat_list.txt"

    try:
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for start_time, file_path in all_segments:
                f.write(f"file '{file_path.absolute()}'\n")

        # Try concat demuxer first (fast, no re-encoding)
        print(f"Concatenating {len(all_segments)} segments using concat demuxer...")
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list_path),
            "-c",
            "copy",  # No re-encoding
            str(output_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            print("Concatenation successful using demuxer")
            return True
        else:
            # Concat demuxer failed - try concat filter (slower but handles stream mismatches)
            print("Concat demuxer failed, trying concat filter...")
            return _concatenate_with_filter(all_segments, output_path)

    except Exception as e:
        print(f"Concatenation failed: {e}")
        return False


def _concatenate_with_filter(all_segments: List[Tuple[float, Path]], output_path: Path) -> bool:
    """
    Concatenate segments using concat filter (handles stream mismatches but slower).
    """
    if len(all_segments) > 50:
        print(f"Too many segments ({len(all_segments)}) for filter concat - this may be slow")

    # Build filter command
    inputs = []
    filter_parts = []

    for i, (start_time, file_path) in enumerate(all_segments):
        inputs.extend(["-i", str(file_path)])
        filter_parts.append(f"[{i}:v][{i}:a]")

    filter_complex = f"{''.join(filter_parts)}concat=n={len(all_segments)}:v=1:a=1[vout][aout]"

    cmd = (
        ["ffmpeg", "-y"]
        + inputs
        + [
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-threads",
            "0",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output_path),
        ]
    )

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print("Concatenation successful using filter")
            return True
        else:
            print(f"Filter concatenation failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"Filter concatenation error: {e}")
        return False


def apply_blank_optimized(
    input_video: Path,
    timeline_path: Path,
    output_path: Path,
    blank_video_path: Path,
    original_timeline_path: Optional[Path] = None,
    trim_first_frame: bool = True,
) -> bool:
    """
    Main entry point for optimized apply-blank processing.

    Implements the two-mode engine from implementation-plan.md:
    - Mode 1: Single-pass GPU filter (default)
    - Mode 2: Incremental stream-copy reuse (for small changes)
    """
    print("=== Optimized Apply-Blank Processing ===")

    # Load timelines
    modified_timeline = load_timeline(timeline_path)
    original_timeline = None

    if original_timeline_path and original_timeline_path.exists():
        original_timeline = load_timeline(original_timeline_path)

    # Auto-select processing mode
    mode, reason = auto_select_mode(original_timeline, modified_timeline)
    print(f"Selected Mode {mode}: {reason}")

    if mode == 1:
        # Mode 1: Single-pass GPU filter
        edit_sets = build_edit_sets(modified_timeline)
        return apply_blank_mode1_single_pass(input_video, output_path, edit_sets, trim_first_frame)
    else:
        # Mode 2: Incremental processing
        return apply_blank_mode2_incremental(
            input_video, output_path, original_timeline, modified_timeline, blank_video_path, trim_first_frame
        )
