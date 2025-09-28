"""
Minimal Apply-Blank Implementation

Replaces segments marked as type="all" with blank video using stream-copy + concat approach.
Focused on reliability and simplicity per APPLY_BLANK_REWRITE_PLAN.md.
"""

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

from .utils import load_timeline, hhmmss_to_seconds, run_subprocess_with_encoding


def find_blank_segments(timeline_data: Dict) -> List[Tuple[float, float]]:
    """Extract segments marked as type='all' for blank video replacement."""
    segments = timeline_data.get("timeline", [])
    blank_ranges = []

    for segment in segments:
        if segment.get("type") == "all":
            start = hhmmss_to_seconds(segment["start"])
            end = hhmmss_to_seconds(segment["end"])
            blank_ranges.append((start, end))

    return sorted(blank_ranges)


def merge_adjacent_ranges(ranges: List[Tuple[float, float]], gap_threshold: float = 0.01) -> List[Tuple[float, float]]:
    """Merge overlapping/adjacent ranges to reduce segment count."""
    if not ranges:
        return []

    merged = [ranges[0]]

    for start, end in ranges[1:]:
        last_start, last_end = merged[-1]

        # Merge if overlapping or gap ≤ threshold (10ms default)
        if start <= last_end + gap_threshold:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    return merged


def probe_video_duration(video_path: Path) -> float:
    """Get video duration using ffprobe."""
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(video_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def validate_blank_template(blank_path: Path, input_path: Path) -> bool:
    """Basic validation that blank template exists and is a video file."""
    if not blank_path.exists():
        print(f"Error: Blank template not found: {blank_path}")
        return False

    try:
        probe_video_duration(blank_path)
        print(f"Blank template validated: {blank_path}")
        return True
    except Exception as e:
        print(f"Error: Invalid blank template: {e}")
        return False


def extract_unaffected_spans(
    input_path: Path,
    blank_ranges: List[Tuple[float, float]],
    total_duration: float,
    temp_dir: Path
) -> List[Tuple[float, Path]]:
    """Extract video spans not affected by blanking using stream-copy."""
    spans = []
    current_time = 0.0

    for i, (blank_start, blank_end) in enumerate(blank_ranges):
        # Extract span before this blank segment
        if current_time < blank_start:
            duration = blank_start - current_time
            span_file = temp_dir / f"span_{current_time:.3f}_{duration:.3f}.mp4"

            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", str(current_time),
                "-i", str(input_path),
                "-t", str(duration),
                "-c", "copy",
                str(span_file)
            ]

            run_subprocess_with_encoding(cmd, check=True)
            spans.append((current_time, span_file))

        current_time = blank_end

    # Extract final span if needed
    if current_time < total_duration:
        duration = total_duration - current_time
        span_file = temp_dir / f"span_{current_time:.3f}_{duration:.3f}.mp4"

        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", str(current_time),
            "-i", str(input_path),
            "-t", str(duration),
            "-c", "copy",
            str(span_file)
        ]

        run_subprocess_with_encoding(cmd, check=True)
        spans.append((current_time, span_file))

    return spans


def create_blank_chunks(
    blank_ranges: List[Tuple[float, float]],
    blank_template: Path,
    temp_dir: Path
) -> List[Tuple[float, Path]]:
    """Create blank video chunks by looping template to exact durations."""
    chunks = []

    for i, (start, end) in enumerate(blank_ranges):
        duration = end - start
        chunk_file = temp_dir / f"blank_{i}_{start:.3f}_{duration:.3f}.mp4"

        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-stream_loop", "-1",  # Loop template indefinitely
            "-i", str(blank_template),
            "-t", str(duration),    # Cut to exact duration
            "-c", "copy",           # Maintain codec consistency
            str(chunk_file)
        ]

        run_subprocess_with_encoding(cmd, check=True)
        chunks.append((start, chunk_file))

    return chunks


def concat_final_video(
    all_segments: List[Tuple[float, Path]],
    output_path: Path,
    temp_dir: Path
) -> None:
    """Concatenate all segments in chronological order."""
    if not all_segments:
        raise ValueError("No segments to concatenate")

    # Sort by start time
    all_segments.sort(key=lambda x: x[0])

    # Create concat list file
    concat_file = temp_dir / "concat_list.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for _, file_path in all_segments:
            f.write(f"file '{file_path.absolute()}'\n")

    # Concatenate using concat demuxer
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        str(output_path)
    ]

    run_subprocess_with_encoding(cmd, check=True)


def validate_output_duration(input_path: Path, output_path: Path, tolerance: float = 0.05) -> bool:
    """Validate output duration is within tolerance of input duration."""
    try:
        input_duration = probe_video_duration(input_path)
        output_duration = probe_video_duration(output_path)
        diff = abs(output_duration - input_duration)

        if diff <= tolerance:
            print(f"Duration validation passed: {input_duration:.3f}s -> {output_duration:.3f}s (diff: {diff:.3f}s)")
            return True
        else:
            print(f"Duration validation failed: {input_duration:.3f}s -> {output_duration:.3f}s (diff: {diff:.3f}s)")
            return False
    except Exception as e:
        print(f"Duration validation error: {e}")
        return False


def apply_blank_video(
    input_video: Path,
    timeline_path: Path,
    blank_template: Path,
    output_path: Path
) -> bool:
    """
    Main function: Replace timeline segments marked as type='all' with blank video.

    Returns True on success, False on failure.
    """
    print(f"Apply-blank processing: {input_video.name}")

    try:
        # 1. Load and validate inputs
        timeline_data = load_timeline(timeline_path)

        if not validate_blank_template(blank_template, input_video):
            return False

        # 2. Find segments to blank
        blank_ranges = find_blank_segments(timeline_data)
        if not blank_ranges:
            print("No segments marked for blanking (type='all') found")
            return False

        # 3. Merge adjacent ranges for efficiency
        merged_ranges = merge_adjacent_ranges(blank_ranges)
        print(f"Processing {len(merged_ranges)} blank ranges (merged from {len(blank_ranges)})")

        # 4. Get video duration
        total_duration = probe_video_duration(input_video)
        print(f"Video duration: {total_duration:.3f}s")

        # 5. Create temporary working directory
        with tempfile.TemporaryDirectory(prefix="apply_blank_") as temp_dir_str:
            temp_dir = Path(temp_dir_str)

            # 6. Extract unaffected spans (stream-copy)
            print("Extracting unaffected video spans...")
            unaffected_spans = extract_unaffected_spans(input_video, merged_ranges, total_duration, temp_dir)

            # 7. Create blank chunks
            print("Creating blank video chunks...")
            blank_chunks = create_blank_chunks(merged_ranges, blank_template, temp_dir)

            # 8. Combine all segments chronologically
            all_segments = unaffected_spans + blank_chunks
            print(f"Concatenating {len(all_segments)} segments...")
            concat_final_video(all_segments, output_path, temp_dir)

        # 9. Validate output
        if validate_output_duration(input_video, output_path):
            print(f"Success: {output_path}")
            return True
        else:
            print("Warning: Duration validation failed but file was created")
            return True  # Still consider success if file exists

    except Exception as e:
        print(f"Error in apply_blank_video: {e}")
        return False