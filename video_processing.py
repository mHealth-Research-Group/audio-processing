"""
Video processing module for multi-step audio/video processing pipeline.
Integrates video merging with gap detection and the existing audio processing functionality.
"""

from videoprops import get_video_properties
import datetime
import glob
import os
import subprocess
import pandas as pd
from pathlib import Path
import json
import warnings
from typing import List, Tuple, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing


def get_video_metadata(video_path: str) -> Dict:
    """
    Extract metadata from a video file using get_video_properties.

    Args:
        video_path: Path to the video file

    Returns:
        Dictionary containing video metadata
    """
    try:
        props = get_video_properties(video_path)
        return props
    except Exception as e:
        print(f"Warning: Could not extract metadata from {video_path}: {e}")
        return {}


def process_single_video_metadata(file_path: str) -> Tuple[str, Optional[Dict]]:
    """
    Process metadata for a single video file. Used for parallel processing.

    Args:
        file_path: Path to the video file

    Returns:
        Tuple of (filename, metadata_dict or None if error)
    """
    fname = os.path.basename(file_path)

    try:
        props = get_video_metadata(file_path)

        # Extract duration in seconds
        duration_s = float(props.get("duration", 0))

        # Extract frame rate
        frame_rate_str = props.get("avg_frame_rate", "30/1")
        if "/" in frame_rate_str:
            num, denom = frame_rate_str.split("/")
            frame_rate = float(num) / float(denom)
        else:
            frame_rate = float(frame_rate_str)

        # Extract dimensions
        width = int(props.get("width", 1920))
        height = int(props.get("height", 1080))

        # Extract video codec if available
        video_codec = props.get("codec_name", "unknown")

        # Try to get creation time from filename first
        creation_time = parse_creation_time_from_filename(fname)

        # If not in filename, try from metadata
        if creation_time is None:
            if "tags" in props and "creation_time" in props["tags"]:
                creation_time_str = props["tags"]["creation_time"]
                creation_time = parse_creation_time(creation_time_str)
            else:
                # Fallback to file modification time
                modification_time = os.path.getmtime(file_path)
                creation_time = datetime.datetime.fromtimestamp(modification_time)
                warnings.warn(
                    f"Could not determine creation time from filename or metadata for {fname}. "
                    f"Falling back to file modification time."
                )

        # Calculate stop time (creation time is typically the end time)
        stop_time = creation_time
        start_time = stop_time - datetime.timedelta(seconds=duration_s)

        return fname, {
            "fname": fname,
            "start": start_time,
            "stop": stop_time,
            "duration_s": duration_s,
            "frame_rate": frame_rate,
            "width": width,
            "height": height,
            "video_codec": video_codec,
        }

    except Exception as e:
        print(f"❌ Error processing {fname}: {e}")
        return fname, None


def parse_creation_time(creation_time_str: str) -> datetime.datetime:
    """
    Parse creation time string from video metadata.

    Args:
        creation_time_str: Creation time string from video metadata

    Returns:
        Parsed datetime object
    """
    # Handle different creation time formats
    formats = ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"]

    for fmt in formats:
        try:
            return datetime.datetime.strptime(creation_time_str, fmt)
        except ValueError:
            continue

    # If no format works, raise an error
    raise ValueError(f"Unable to parse creation time: {creation_time_str}")


def parse_creation_time_from_filename(filename: str) -> Optional[datetime.datetime]:
    """
    Parse creation time from filename in YYYYMMDDHHMMSS format.
    Example: 20250703175433_000028.MP4 -> 2025-07-03 17:54:33
    """
    try:
        # Assuming format is YYYYMMDDHHMMSS_...
        time_str = os.path.basename(filename).split("_")[0]
        if len(time_str) == 14 and time_str.isdigit():
            return datetime.datetime.strptime(time_str, "%Y%m%d%H%M%S")
    except (IndexError, ValueError):
        # Raised if split fails or strptime fails
        pass  # Will return None
    return None


def analyze_video_gaps(video_directory: str, max_workers: Optional[int] = None) -> pd.DataFrame:
    """
    Analyze gaps between video files in a directory using parallel processing.

    Args:
        video_directory: Path to directory containing video files
        max_workers: Maximum number of worker threads for parallel processing.
                    If None, uses min(32, (os.cpu_count() or 1) + 4)

    Returns:
        DataFrame with video file information and gap analysis
    """
    video_path = Path(video_directory)
    if not video_path.exists():
        raise FileNotFoundError(f"Directory does not exist: {video_directory}")

    # Find all video files (MP4, AVI, MOV, etc.) - excluding temporary/generated files
    video_extensions = ["*.MP4", "*.mp4", "*.AVI", "*.avi", "*.MOV", "*.mov", "*.MKV", "*.mkv"]
    video_files = []
    found_files = set()  # Use set to avoid duplicates on case-insensitive filesystems

    for ext in video_extensions:
        files = sorted(glob.glob(str(video_path / ext)))
        # Filter out temporary files (blank videos and processed files)
        filtered_files = [
            f
            for f in files
            if not (
                "_blank" in os.path.basename(f)
                or "merged_video" in os.path.basename(f)
                or "_no_conversations" in os.path.basename(f)
                or "_edited" in os.path.basename(f)
                or "_processed" in os.path.basename(f)
            )
        ]

        # Add files to the list, avoiding duplicates
        for file_path in filtered_files:
            # Normalize path for comparison (handle case-insensitive filesystems)
            normalized_path = os.path.normpath(file_path).lower()
            if normalized_path not in found_files:
                found_files.add(normalized_path)
                video_files.append(file_path)

    # Sort the final list to ensure consistent ordering
    video_files.sort()

    if not video_files:
        raise ValueError(f"No video files found in {video_directory}")

    print(f"Found {len(video_files)} video files to analyze...")

    # Determine optimal number of workers
    if max_workers is None:
        max_workers = min(32, (multiprocessing.cpu_count() or 1) + 4)
        # For I/O bound tasks like video metadata extraction, we can use more threads than CPU cores
        max_workers = min(max_workers, len(video_files))  # Don't use more workers than files

    print(f"Using {max_workers} worker threads for parallel processing...")

    # Process videos in parallel
    video_data = []
    processed_count = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all video processing tasks
        future_to_file = {
            executor.submit(process_single_video_metadata, file_path): file_path for file_path in video_files
        }

        # Collect results as they complete
        for future in as_completed(future_to_file):
            file_path = future_to_file[future]
            try:
                fname, metadata = future.result()
                if metadata is not None:
                    video_data.append(metadata)
                processed_count += 1

                # Progress reporting
                if processed_count % 10 == 0 or processed_count == len(video_files):
                    print(f"Processed {processed_count}/{len(video_files)} videos...")

            except Exception as e:
                print(f"❌ Exception processing {os.path.basename(file_path)}: {e}")

    if not video_data:
        raise ValueError("No valid video files could be processed - all files had errors")

    print(f"✅ Successfully processed {len(video_data)} out of {len(video_files)} video files")

    # Create DataFrame from processed data
    df = pd.DataFrame(video_data)

    # Calculate gaps between videos
    df = df.sort_values("start").reset_index(drop=True)
    df["next_start"] = df["start"].shift(-1)
    df["gap_seconds"] = (df["next_start"] - df["stop"]).dt.total_seconds()
    df["gap_seconds"] = df["gap_seconds"].fillna(0)  # No gap after last video

    return df


def create_gap_labels(gaps_df: pd.DataFrame, output_path: str) -> List[Dict]:
    """
    Create label entries for video gaps in the timeline format.

    Args:
        gaps_df: DataFrame with gap analysis
        output_path: Path where the merged video will be saved

    Returns:
        List of label dictionaries for gaps
    """
    gap_labels = []

    for i, row in gaps_df.iterrows():
        gap_seconds = row["gap_seconds"]

        if gap_seconds > 0:  # Only create labels for actual gaps
            # Calculate cumulative time offset in the merged video
            start_offset = gaps_df.loc[:i, "duration_s"].sum()
            end_offset = start_offset + gap_seconds

            gap_label = {
                "start": f"{int(start_offset // 60)}:{start_offset % 60:06.3f}",
                "end": f"{int(end_offset // 60)}:{end_offset % 60:06.3f}",
                "duration": f"{int(gap_seconds // 60)}:{gap_seconds % 60:06.3f}",
                "type": "missing_video",
                "label": "video_gap",
                "gap_duration_seconds": gap_seconds,
                "before_file": row["fname"],
                "after_file": gaps_df.loc[i + 1, "fname"] if i + 1 < len(gaps_df) else None,
            }
            gap_labels.append(gap_label)

    return gap_labels


def merge_videos_with_gaps(
    video_directory: str, output_path: Optional[str] = None, create_black_videos: bool = True
) -> Tuple[str, str, List[Dict]]:
    """
    Merge videos in a directory, filling gaps with black video and generating gap labels.
    Uses the exact proven approach from video-process that works correctly.

    Args:
        video_directory: Path to directory containing video files
        output_path: Optional output path for merged video
        create_black_videos: Whether to create black videos for gaps

    Returns:
        Tuple of (merged_video_path, metadata_csv_path, gap_labels)
    """
    video_path = Path(video_directory)
    gaps_df = analyze_video_gaps(video_directory)

    # Create temporary directory for processing
    tmp_dir = video_path / "tmp"
    tmp_dir.mkdir(exist_ok=True)

    # Create file list for ffmpeg concatenation
    file_list_path = tmp_dir / "file_list.txt"

    # Get video properties for consistent output format
    frame_rate = int(gaps_df.iloc[0]["frame_rate"])  # Convert to int like working code
    width = int(gaps_df.iloc[0]["width"])
    height = int(gaps_df.iloc[0]["height"])

    # Detect if the source videos carry an audio stream so that we can
    # create *silent* gap fillers when needed. Having the same stream layout
    # (video + audio) across all concatenated parts is mandatory when we rely
    # on `-c copy` for stream-copy concatenation. Otherwise, many players will
    # appear to "freeze" during the gap because the audio stream disappears.
    first_video_file = Path(video_directory) / gaps_df.iloc[0]["fname"]
    include_silent_audio = _has_audio_stream(first_video_file)

    # For consistency we always create synthetic clips with H.264 so that the
    # final merged file is guaranteed to be H.264, regardless of the original
    # camera codec. (Re-encoding happens in the concat step below.)
    synthetic_encoder = "libx264"

    gap_labels = []

    with open(file_list_path, "w") as file_list:
        cumulative_duration = 0
        num_rows = len(gaps_df)

        for i, row in gaps_df.iterrows():
            # Add the actual video file
            video_file_path = video_path / row["fname"]
            file_list.write(f"file '{video_file_path.absolute()}'\n")

            cumulative_duration += row["duration_s"]

            # Add black video for gap if there is one (but not after the last video)
            if i != num_rows - 1:  # Don't create gap after last video
                gap_seconds = row["gap_seconds"]
                if gap_seconds > 0 and create_black_videos:
                    gap_seconds_int = int(gap_seconds)
                    if gap_seconds_int > 0:  # Only create if gap is at least 1 second
                        blank_video_path = tmp_dir / f"{gap_seconds_int}_seconds_blank.MP4"

                        # Create black video if it doesn't exist
                        if not blank_video_path.exists():
                            print(f"CREATING BLANK VIDEO {blank_video_path}")

                            if include_silent_audio:
                                # Create a silent audio track that matches the duration so the
                                # gap clip has the same stream layout (video + audio) as the
                                # real footage. We intentionally re-encode the gap clip using
                                # libx264 + AAC because most consumer footage already uses
                                # those codecs, and they work well with stream-copy concatenation
                                # in MP4 containers.
                                cmd = (
                                    "ffmpeg -hide_banner -loglevel error "
                                    f"-f lavfi -i color=c=black:s={width}x{height}:r={frame_rate} "
                                    f"-f lavfi -i anullsrc=channel_layout=stereo:sample_rate=48000 "
                                    f"-t {gap_seconds_int} -shortest "
                                    f"-c:v {synthetic_encoder} -preset ultrafast -crf 28 -pix_fmt yuv420p "
                                    "-c:a aac -b:a 128k "
                                    f"{blank_video_path}"
                                )
                            else:
                                # Original behaviour (video‐only gap clip)
                                cmd = (
                                    f"ffmpeg -f lavfi -i color=c=black:s={width}x{height}:r={frame_rate}:d={gap_seconds_int} "
                                    f"-c:v {synthetic_encoder} -preset ultrafast -crf 28 -pix_fmt yuv420p "
                                    f"{blank_video_path}"
                                )

                            result = subprocess.run(cmd, capture_output=True, shell=True)
                            if result.returncode != 0:
                                print("Failed!")
                                print(f"Error: {result.stderr}")
                                raise subprocess.CalledProcessError(result.returncode, cmd)
                            print("DONE CREATING BLANK VIDEO.")

                        file_list.write(f"file '{blank_video_path.absolute()}'\n")

                        # Create gap label
                        gap_start = cumulative_duration
                        gap_end = cumulative_duration + gap_seconds_int

                        gap_label = {
                            "start": f"{int(gap_start // 60)}:{gap_start % 60:06.3f}",
                            "end": f"{int(gap_end // 60)}:{gap_end % 60:06.3f}",
                            "duration": f"{int(gap_seconds_int // 60)}:{gap_seconds_int % 60:06.3f}",
                            "type": "missing_video",
                            "label": "video_gap",
                            "gap_duration_seconds": gap_seconds_int,
                            "before_file": row["fname"],
                            "after_file": gaps_df.iloc[i + 1]["fname"] if i + 1 < len(gaps_df) else "None",
                        }
                        gap_labels.append(gap_label)

                        cumulative_duration += gap_seconds_int

    # Define output path if not provided
    if output_path is None:
        output_path_obj = video_path.parent / "merged_video.mp4"
    else:
        output_path_obj = Path(output_path)

    # Get creation time from first video for metadata (like working code)
    first_video_start = gaps_df.iloc[0]["start"]
    # Subtract 1 second like in working code
    creation_time = first_video_start - datetime.timedelta(seconds=1)
    creation_time_str = creation_time.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    # ------------------------------------------------------------------
    # Concatenate **and re-encode** the entire stream so the final file
    # is H.264/AAC (broadest compatibility).
    # ------------------------------------------------------------------
    print("COMBINING VIDEOS (re-encoding to H.264)…")

    cmd = (
        "ffmpeg -hide_banner -loglevel error "
        f"-f concat -safe 0 -i {file_list_path} "
        f'-metadata creation_time="{creation_time_str}" '
        "-threads 0 "
        "-c:v libx264 -preset fast -crf 22 -pix_fmt yuv420p "
        "-c:a aac -b:a 128k -ac 2 "
        "-movflags +faststart "
        f"{output_path_obj}"
    )

    try:
        result = subprocess.run(cmd, capture_output=True, shell=True)
        if result.returncode != 0:
            print("❌ Concatenation failed:")
            print(f"Command: {cmd}")
            print(f"Error: {result.stderr}")
            raise subprocess.CalledProcessError(result.returncode, cmd)
        print("✅ Video concatenation completed successfully")
    except subprocess.CalledProcessError as e:
        print("❌ Concatenation failed:")
        print(f"Command: {cmd}")
        print(f"Error: {e}")
        raise

    print("✓ Video merge complete.")

    # Save metadata to CSV
    metadata_csv_path = output_path_obj.parent / f"{output_path_obj.stem}_metadata.csv"
    gaps_df.to_csv(metadata_csv_path, index=False)
    print(f"✓ Gap analysis saved to: {metadata_csv_path}")

    return str(output_path_obj), str(metadata_csv_path), gap_labels


def check_videos_compatible_for_stream_copy(gaps_df: pd.DataFrame) -> bool:
    """
    Check if all videos have compatible formats for stream copying during concatenation.

    Args:
        gaps_df: DataFrame with video metadata

    Returns:
        bool: True if stream copy can be used, False if re-encoding is needed
    """
    if len(gaps_df) == 0:
        return True

    # Get reference video properties
    ref_row = gaps_df.iloc[0]
    ref_width = ref_row["width"]
    ref_height = ref_row["height"]
    ref_fps = ref_row["frame_rate"]
    ref_codec = ref_row.get("video_codec", "unknown")

    # Check if all videos match reference properties
    for _, row in gaps_df.iterrows():
        if (
            row["width"] != ref_width
            or row["height"] != ref_height
            or abs(row["frame_rate"] - ref_fps) > 0.1  # Allow small FPS differences
            or row.get("video_codec", "unknown") != ref_codec
        ):
            print(
                f"⚠️  Incompatible format detected in {row['fname']}: "
                f"{row['width']}x{row['height']} @ {row['frame_rate']}fps "
                f"codec:{row.get('video_codec', 'unknown')}"
            )
            return False

    return True


# -----------------------------------------------------------------------------
# Utility helpers for audio handling
# -----------------------------------------------------------------------------


def _has_audio_stream(video_file: Path) -> bool:
    """Return True if the provided video file contains at least one audio stream."""
    probe_cmd = [
        "ffprobe",
        "-hide_banner",
        "-loglevel",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        str(video_file),
    ]

    try:
        result = subprocess.run(probe_cmd, capture_output=True, text=True, check=False)
        return bool(result.stdout.strip())
    except Exception:
        # On any probing error, assume there *is* an audio stream to be safe.
        return True


# (Removed dynamic encoder mapping – we always target H.264 now)


def save_gap_labels(gap_labels: List[Dict], output_path: str) -> str:
    """
    Save the generated gap labels to a JSON file.

    Args:
        gap_labels: List of label dictionaries for gaps
        output_path: Path of the merged video, used to name the label file

    Returns:
        Path to the saved label file
    """
    output_path = Path(output_path)
    label_path = output_path.parent / f"{output_path.stem}_gap_labels.json"

    with open(label_path, "w", encoding="utf-8") as f:
        json.dump(gap_labels, f, indent=2, ensure_ascii=False)

    return str(label_path)


def get_video_duration(video_path: str) -> float:
    """
    Get the duration of a video file in seconds.
    Args:
        video_path: Path to the video file
    Returns:
        Duration in seconds, or 0.0 if unable to determine
    """
    duration_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        result = subprocess.run(duration_cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError) as e:
        print(f"Warning: Could not get duration for {video_path}: {e}")
        return 0.0


def merge_labels_with_timeline(timeline_data: Dict, gap_labels: List[Dict]) -> Dict:
    """
    Merge video gap labels with the audio timeline.

    Args:
        timeline_data: Dictionary containing the audio timeline
        gap_labels: List of label dictionaries for gaps

    Returns:
        Updated timeline data with merged labels
    """

    def time_to_seconds(time_str: str) -> float:
        """Convert MM:SS.sss format to seconds."""
        parts = time_str.split(":")
        if len(parts) == 2:
            minutes, seconds = parts
            return int(minutes) * 60 + float(seconds)
        return float(time_str)

    # Add gap labels to the timeline segments
    if "segments" in timeline_data:
        all_segments = timeline_data["segments"] + gap_labels

        # Sort all segments by start time
        all_segments.sort(key=lambda x: time_to_seconds(x["start"]))

        timeline_data["segments"] = all_segments

    return timeline_data
