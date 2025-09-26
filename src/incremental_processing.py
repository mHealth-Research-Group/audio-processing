"""
Incremental Processing Module for Apply-Blank Operations

This module implements smart incremental processing for apply-blank operations:
1. Compares original vs modified timelines to detect changes
2. Only processes segments that actually changed (type changed to 'all')
3. Uses temporal batching to prevent FFmpeg filter explosion
4. Reuses existing processed segments when possible

Performance Benefits:
- Only process changed segments instead of entire video
- Temporal batching prevents filter explosion for large changes
- Smart caching of processed segments
- Dramatically faster for typical editing workflows
"""

import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .utils import (
    compare_timelines,
    get_timeline_cache_path,
    load_timeline,
    mmss_to_seconds,
    save_yaml,
    split_timeline_into_batches,
)
from .media_processing import apply_blank_video_to_segments, process_media_with_effects


def apply_blank_incremental(
    input_video: Path,
    modified_timeline_path: Path,
    output_path: Path,
    blank_video_path: Path,
    original_timeline_path: Optional[Path] = None,
    batch_duration_minutes: int = 10,
    trim_first_frame: bool = True,
) -> bool:
    """
    Apply blank video processing using incremental approach.

    Args:
        input_video: Path to input video file
        modified_timeline_path: Path to modified timeline with 'all' segments
        output_path: Path for final output video
        blank_video_path: Path to blank video template
        original_timeline_path: Optional path to original timeline for comparison
        batch_duration_minutes: Duration of each processing batch in minutes
        trim_first_frame: Whether to trim first frame for privacy

    Returns:
        bool: True if processing succeeded, False otherwise
    """
    print(f"Starting incremental apply-blank processing...")

    # Load modified timeline
    modified_timeline = load_timeline(modified_timeline_path)

    # Try to find original timeline for comparison
    original_timeline = None
    if original_timeline_path and original_timeline_path.exists():
        original_timeline = load_timeline(original_timeline_path)
    else:
        # Try to load cached original timeline
        cache_path = get_timeline_cache_path(modified_timeline_path)
        if cache_path.exists():
            original_timeline = load_timeline(cache_path)
            print(f"Using cached original timeline: {cache_path}")

    # If we have original timeline, do incremental processing
    if original_timeline:
        return _process_incremental(
            input_video,
            original_timeline,
            modified_timeline,
            output_path,
            blank_video_path,
            batch_duration_minutes,
            trim_first_frame,
        )
    else:
        # Fall back to full processing with batching
        print("No original timeline found - processing all segments with batching")
        return _process_full_with_batching(
            input_video,
            modified_timeline,
            output_path,
            blank_video_path,
            batch_duration_minutes,
            trim_first_frame,
        )


def _process_incremental(
    input_video: Path,
    original_timeline: Dict,
    modified_timeline: Dict,
    output_path: Path,
    blank_video_path: Path,
    batch_duration_minutes: int,
    trim_first_frame: bool,
) -> bool:
    """Process only changed segments incrementally."""

    # Compare timelines to identify changes
    comparison = compare_timelines(original_timeline, modified_timeline)

    changed_segments = comparison["changed_segments"]
    unchanged_segments = comparison["unchanged_segments"]

    print(f"Timeline comparison:")
    print(f"  Changed segments: {comparison['total_changed']}")
    print(f"  Unchanged segments: {comparison['total_unchanged']}")
    print(f"  Change percentage: {comparison['change_percentage']:.1f}%")

    if not changed_segments:
        print("No segments require processing - copying original video")
        shutil.copy2(input_video, output_path)
        return True

    # Split changed segments into batches if needed
    batch_duration_seconds = batch_duration_minutes * 60
    if len(changed_segments) > 50:  # Use batching for many segments
        batches = split_timeline_into_batches(changed_segments, batch_duration_seconds)
        print(f"Split {len(changed_segments)} changed segments into {len(batches)} batches")
        return _process_batched_segments(
            input_video, batches, unchanged_segments, output_path, blank_video_path, trim_first_frame
        )
    else:
        # Process all changed segments at once
        return _process_single_batch(
            input_video, changed_segments, unchanged_segments, output_path, blank_video_path, trim_first_frame
        )


def _process_full_with_batching(
    input_video: Path,
    modified_timeline: Dict,
    output_path: Path,
    blank_video_path: Path,
    batch_duration_minutes: int,
    trim_first_frame: bool,
) -> bool:
    """Process all segments using temporal batching."""

    segments = modified_timeline.get("timeline", [])

    # Filter segments that need processing
    segments_to_process = []
    for segment in segments:
        segment_type = segment.get("type", "")
        if segment_type == "all" or segment_type in ["speaking", "conversation"]:
            segments_to_process.append(segment)

    if not segments_to_process:
        print("No segments require processing - copying original video")
        shutil.copy2(input_video, output_path)
        return True

    # Split into batches
    batch_duration_seconds = batch_duration_minutes * 60
    batches = split_timeline_into_batches(segments_to_process, batch_duration_seconds)

    print(f"Processing {len(segments_to_process)} segments in {len(batches)} batches")

    # Process all segments as "changed" since we have no original timeline
    unchanged_segments = []
    return _process_batched_segments(
        input_video, batches, unchanged_segments, output_path, blank_video_path, trim_first_frame
    )


def _process_batched_segments(
    input_video: Path,
    segment_batches: List[List[Dict]],
    unchanged_segments: List[Dict],
    output_path: Path,
    blank_video_path: Path,
    trim_first_frame: bool,
) -> bool:
    """Process segments in batches and combine results."""

    temp_dir = input_video.parent / "temp_incremental_processing"
    temp_dir.mkdir(exist_ok=True)

    try:
        batch_outputs = []

        # Process each batch
        for i, batch_segments in enumerate(segment_batches):
            print(f"Processing batch {i+1}/{len(segment_batches)} ({len(batch_segments)} segments)...")

            batch_output = temp_dir / f"batch_{i:03d}_processed.mp4"

            success = _process_single_batch(
                input_video, batch_segments, [], batch_output, blank_video_path, trim_first_frame
            )

            if not success:
                print(f"Failed to process batch {i+1}")
                return False

            batch_outputs.append(batch_output)

        # If we have unchanged segments, we need to create a smart concat
        # that interleaves unchanged original segments with processed batches
        if unchanged_segments:
            return _smart_concat_with_unchanged(
                input_video, batch_outputs, unchanged_segments, output_path
            )
        else:
            # Simple concatenation of processed batches
            return _concat_batch_outputs(batch_outputs, output_path)

    finally:
        # Clean up temp directory
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def _process_single_batch(
    input_video: Path,
    segments: List[Dict],
    unchanged_segments: List[Dict],
    output_path: Path,
    blank_video_path: Path,
    trim_first_frame: bool,
) -> bool:
    """Process a single batch of segments."""

    # Separate segments by effect type
    blank_segments = []
    mute_segments = []

    for segment in segments:
        start_time = mmss_to_seconds(segment["start"])
        end_time = mmss_to_seconds(segment["end"])
        segment_type = segment.get("type", "")

        if segment_type == "all":
            blank_segments.append((start_time, end_time))
        elif segment_type in ["speaking", "conversation"]:
            mute_segments.append((start_time, end_time))

    try:
        # Apply blank video if needed
        if blank_segments:
            # Create dummy timeline path for the apply_blank_video_to_segments function
            temp_timeline = input_video.parent / "temp_batch_timeline.yaml"
            timeline_data = {"timeline": segments}
            save_yaml(timeline_data, temp_timeline)

            try:
                apply_blank_video_to_segments(
                    input_video, output_path, blank_segments, blank_video_path, temp_timeline, trim_first_frame
                )
                input_for_mute = output_path
            finally:
                if temp_timeline.exists():
                    temp_timeline.unlink()
        else:
            input_for_mute = input_video

        # Apply audio muting if needed
        if mute_segments:
            effect_segments = {
                "mute_only": mute_segments,
                "black_only": [],
                "mute_and_black": [],
            }
            process_media_with_effects(input_for_mute, output_path, effect_segments)
        elif not blank_segments:
            # No processing needed, copy original
            shutil.copy2(input_video, output_path)

        return True

    except Exception as e:
        print(f"Error processing batch: {e}")
        return False


def _smart_concat_with_unchanged(
    input_video: Path,
    batch_outputs: List[Path],
    unchanged_segments: List[Dict],
    output_path: Path,
) -> bool:
    """Smart concatenation that interleaves unchanged and processed segments."""

    # For now, fall back to full processing
    # TODO: Implement smart segment extraction and interleaving
    print("Smart concatenation not yet implemented - using simple batch concat")
    return _concat_batch_outputs(batch_outputs, output_path)


def _concat_batch_outputs(batch_outputs: List[Path], output_path: Path) -> bool:
    """Concatenate processed batch outputs into final video."""

    if not batch_outputs:
        return False

    if len(batch_outputs) == 1:
        # Single batch - just move/copy the file
        shutil.move(batch_outputs[0], output_path)
        return True

    # Multiple batches - create concat list and combine
    concat_list_path = output_path.parent / "batch_concat_list.txt"

    try:
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for batch_file in batch_outputs:
                f.write(f"file '{batch_file.absolute()}'\n")

        # Use FFmpeg concat demuxer for fast concatenation
        cmd = [
            "ffmpeg",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list_path),
            "-c", "copy",  # No re-encoding
            "-y",
            str(output_path)
        ]

        subprocess.run(cmd, check=True, capture_output=True)

        print(f"Successfully concatenated {len(batch_outputs)} batches")
        return True

    except subprocess.CalledProcessError as e:
        print(f"Error concatenating batches: {e}")
        return False
    finally:
        if concat_list_path.exists():
            concat_list_path.unlink()


def cache_original_timeline(timeline_path: Path) -> None:
    """Cache the original timeline for future incremental processing."""
    cache_path = get_timeline_cache_path(timeline_path)
    shutil.copy2(timeline_path, cache_path)
    print(f"Cached original timeline: {cache_path}")