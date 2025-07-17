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


def analyze_video_gaps(video_directory: str) -> pd.DataFrame:
    """
    Analyze gaps between video files in a directory.

    Args:
        video_directory: Path to directory containing video files

    Returns:
        DataFrame with video file information and gap analysis
    """
    video_path = Path(video_directory)
    if not video_path.exists():
        raise FileNotFoundError(f"Directory does not exist: {video_directory}")

    # Find all MP4 files (can be extended for other formats)
    video_files = sorted(glob.glob(str(video_path / "*.MP4")))
    video_files.extend(sorted(glob.glob(str(video_path / "*.mp4"))))

    if not video_files:
        raise ValueError(f"No video files found in {video_directory}")

    df = pd.DataFrame(columns=["fname", "start", "stop", "duration_s", "frame_rate", "width", "height"])

    for i, file_path in enumerate(video_files):
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

            # Try to get creation time from metadata
            creation_time = None
            if "tags" in props and "creation_time" in props["tags"]:
                creation_time_str = props["tags"]["creation_time"]
                creation_time = parse_creation_time(creation_time_str)
            else:
                # Fallback to file modification time
                modification_time = os.path.getmtime(file_path)
                creation_time = datetime.datetime.fromtimestamp(modification_time)
                warnings.warn(f"Using file modification time for {fname} as creation time not found in metadata")

            # Calculate stop time (creation time is typically the end time)
            stop_time = creation_time
            start_time = stop_time - datetime.timedelta(seconds=duration_s)

            df.loc[i] = [fname, start_time, stop_time, duration_s, frame_rate, width, height]

        except Exception as e:
            print(f"Error processing {fname}: {e}")
            continue

    if df.empty:
        raise ValueError("No valid video files could be processed")

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
    frame_rate = gaps_df.iloc[0]["frame_rate"]
    width = gaps_df.iloc[0]["width"]
    height = gaps_df.iloc[0]["height"]

    gap_labels = []

    with open(file_list_path, "w") as file_list:
        cumulative_duration = 0

        for i, row in gaps_df.iterrows():
            # Add the actual video file
            video_file_path = video_path / row["fname"]
            file_list.write(f"file '{video_file_path.absolute()}'\n")

            cumulative_duration += row["duration_s"]

            # Add black video for gap if there is one
            gap_seconds = row["gap_seconds"]
            if gap_seconds > 0 and create_black_videos:
                gap_duration_int = max(1, int(gap_seconds))  # Minimum 1 second
                blank_video_path = tmp_dir / f"{gap_duration_int}_seconds_blank.mp4"

                # Create black video if it doesn't exist
                if not blank_video_path.exists():
                    print(f"Creating blank video: {blank_video_path}")
                    cmd = [
                        "ffmpeg",
                        "-f",
                        "lavfi",
                        "-i",
                        f"color=c=black:s={width}x{height}:r={frame_rate}:d={gap_duration_int}",
                        "-c:v",
                        "libx264",
                        "-pix_fmt",
                        "yuv420p",
                        str(blank_video_path),
                    ]

                    result = subprocess.run(cmd, capture_output=True, text=True)
                    if result.returncode != 0:
                        raise subprocess.CalledProcessError(result.returncode, cmd, result.stderr)
                    print(f"Created blank video: {blank_video_path}")

                file_list.write(f"file '{blank_video_path.absolute()}'\n")

                # Create gap label
                gap_start = cumulative_duration
                gap_end = cumulative_duration + gap_seconds

                gap_label = {
                    "start": f"{int(gap_start // 60)}:{gap_start % 60:06.3f}",
                    "end": f"{int(gap_end // 60)}:{gap_end % 60:06.3f}",
                    "duration": f"{int(gap_seconds // 60)}:{gap_seconds % 60:06.3f}",
                    "type": "missing_video",
                    "label": "video_gap",
                    "gap_duration_seconds": gap_seconds,
                    "before_file": row["fname"],
                    "after_file": gaps_df.loc[i + 1]["fname"] if i + 1 < len(gaps_df) else "None",
                }
                gap_labels.append(gap_label)

                cumulative_duration += gap_seconds

    # Define output path if not provided
    if output_path is None:
        output_path = video_path.parent / "merged_video.mp4"
    else:
        output_path = Path(output_path)

    # Run ffmpeg to concatenate videos
    print(f"Merging videos to: {output_path}")
    concat_cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(file_list_path),
        "-c",
        "copy",
        str(output_path),
    ]

    result = subprocess.run(concat_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, concat_cmd, result.stderr)

    print("✓ Video merge complete.")

    # Save metadata to CSV
    metadata_csv_path = output_path.parent / f"{output_path.stem}_metadata.csv"
    gaps_df.to_csv(metadata_csv_path, index=False)
    print(f"✓ Gap analysis saved to: {metadata_csv_path}")

    return str(output_path), str(metadata_csv_path), gap_labels


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
