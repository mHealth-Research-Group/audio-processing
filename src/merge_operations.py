"""
Video merging operations for timestamped video files.
Handles merging logic, output path generation, and batch processing.
"""

from pathlib import Path
from typing import List, Optional
from .utils import generate_merged_filename
from .video_merger import extract_timestamp_from_filename, merge_videos


def detect_timestamped_videos(video_files: List[Path]) -> tuple[bool, int]:
    """
    Detect if videos have timestamps and count them.

    Args:
        video_files: List of video file paths

    Returns:
        Tuple of (has_timestamped_videos, timestamped_count)
    """
    timestamped_count = sum(1 for vf in video_files if extract_timestamp_from_filename(vf.name) is not None)

    has_timestamped_videos = timestamped_count > 1
    return has_timestamped_videos, timestamped_count


def setup_merge_operation(args, video_files: List[Path], output_dir: Path) -> tuple[Path, bool]:
    """
    Set up merge operation paths and check for existing files.

    Args:
        args: Command arguments
        video_files: List of video files to merge
        output_dir: Output directory

    Returns:
        Tuple of (merged_output_path, should_skip)
    """
    # Set output filename for merged video
    if args.output:
        merged_output = Path(args.output)
    else:
        merged_filename = generate_merged_filename(video_files)
        merged_output = output_dir / merged_filename

    # Check if merged video already exists
    if merged_output.exists() and not getattr(args, "force_overwrite", False):
        print(f"Merged video already exists: {merged_output}. Use --force-overwrite to replace it.")
        return merged_output, True  # Skip merge operation

    return merged_output, False


def perform_video_merge(input_dir: Path, merged_output: Path, args) -> Optional[dict]:
    """
    Perform the actual video merging operation.

    Args:
        input_dir: Input directory containing videos
        merged_output: Output path for merged video
        args: Command arguments with merge settings

    Returns:
        Gap information dictionary or None
    """
    try:
        # Get merge parameters
        min_gap = getattr(args, "min_gap_threshold", 2.0)
        max_gap = getattr(args, "max_gap_threshold", None)
        blank_video = Path(args.blank_video)

        print(f"Merging videos with gap thresholds: min={min_gap}s, max={max_gap}")

        # Perform merge operation
        gap_info = merge_videos(
            input_dir=input_dir,
            output_path=merged_output,
            blank_video=blank_video,
            min_gap_threshold=min_gap,
            max_gap_threshold=max_gap,
        )

        print(f"Video merge completed: {merged_output}")
        return gap_info

    except Exception as e:
        print(f"Error during video merge: {e}")
        return None


def create_processed_video_args(args, merged_output: Path) -> object:
    """
    Create arguments for processing the merged video.

    Args:
        args: Original command arguments
        merged_output: Path to merged video

    Returns:
        New arguments object for processing
    """
    import argparse
    from .utils import generate_processed_filename

    file_args = argparse.Namespace(**vars(args))
    file_args.input_path = str(merged_output)

    # Determine output path for processed file
    if args.output:
        base_output_path = Path(args.output)
        processed_filename = generate_processed_filename(base_output_path)
        file_args.output = str(base_output_path.parent / processed_filename)
    else:
        processed_filename = generate_processed_filename(merged_output)
        file_args.output = str(merged_output.parent / processed_filename)

    return file_args


def should_process_after_merge(args) -> bool:
    """
    Determine if we should process the video after merging.

    Args:
        args: Command arguments

    Returns:
        True if processing should continue after merge
    """
    print(f"generate_timeline: {args.generate_timeline}, analyze_speakers: {args.analyze_speakers}, merge_only: {getattr(args, 'merge_only', False)}")
    return (args.generate_timeline or args.analyze_speakers) and not getattr(args, "merge_only", False)
