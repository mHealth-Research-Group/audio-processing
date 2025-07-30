"""
Video merging functionality for processing multiple timestamped videos.
"""

import json
import logging
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

from .utils import (
    run_subprocess_with_encoding,
    is_video_file,
    mmss_to_seconds,
    seconds_to_mmss,
    load_timeline,
    save_yaml,
    AUDIO_CODEC,
    SILENT_AUDIO_MONO_CAMERA,
)

logger = logging.getLogger(__name__)


class VideoSegment:
    """
    Represents a video segment with its associated metadata.
    """

    def __init__(self, file_path: Path, timestamp: datetime, duration: float):
        self.file_path = file_path
        self.timestamp = timestamp
        self.duration = duration
        self.end_time = timestamp + timedelta(seconds=duration)

    def __repr__(self) -> str:
        return f"VideoSegment(path={self.file_path.name}, timestamp={self.timestamp}, duration={self.duration:.2f}s)"


def _parse_frame_rate(fr_string: str) -> float:
    """Safely parse frame rate string like '30/1' or '29.97'."""
    if "/" in fr_string:
        num, den = fr_string.split("/")
        try:
            return float(num) / float(den)
        except ZeroDivisionError:
            return 0.0
    return float(fr_string)


def format_duration_mmss(seconds: float) -> str:
    """Format duration in seconds to MM:SS.mmm format."""
    minutes = int(seconds // 60)
    seconds = seconds % 60
    return f"{minutes}:{seconds:06.3f}"


def add_video_gaps_to_timeline(
    timeline_path: Path, segments: List[VideoSegment], gaps: List[Tuple[datetime, float]]
) -> None:
    """
    Add video gap information to existing timeline JSON file.

    Args:
        timeline_path: Path to existing timeline JSON file
        segments: List of video segments
        gaps: List of (gap_start_time, gap_duration) tuples
    """
    try:
        # Load existing timeline
        timeline_data = load_timeline(timeline_path)

        if not segments or not gaps:
            return

        # Start from the first video segment timestamp
        base_time = segments[0].timestamp

        # Create gap segments to insert
        gap_segments = []
        for gap_start, gap_duration in gaps:
            start_offset = (gap_start - base_time).total_seconds()
            gap_segments.append(
                {
                    "start": seconds_to_mmss(start_offset),
                    "end": seconds_to_mmss(start_offset + gap_duration),
                    "duration": seconds_to_mmss(gap_duration),
                    "type": "gap",
                    "speakers": 0,
                    "label": "NoVideo",
                    "video_content": "missing_video",
                    "note": "Missing video segment filled with blank video",
                }
            )

        # Insert gap segments into timeline, maintaining chronological order
        existing_timeline = timeline_data.get("timeline", [])
        for gap_segment in gap_segments:
            gap_start_seconds = mmss_to_seconds(gap_segment["start"])

            # Find insertion point
            insert_index = 0
            for i, segment in enumerate(existing_timeline):
                segment_start = mmss_to_seconds(segment["start"])
                if segment_start > gap_start_seconds:
                    insert_index = i
                    break
                insert_index = i + 1

            existing_timeline.insert(insert_index, gap_segment)

        # Save updated timeline
        save_yaml(timeline_data, timeline_path)
        logger.info(f"Added {len(gaps)} NoVideo segments to timeline: {timeline_path}")

    except Exception as e:
        logger.error(f"Failed to update timeline with video gaps: {e}")


def save_gap_info(segments: List[VideoSegment], gaps: List[Tuple[datetime, float]], gap_info_path: Path) -> None:
    """
    Save gap information to a JSON file for later addition to timeline.

    Args:
        segments: List of video segments
        gaps: List of (gap_start_time, gap_duration) tuples
        gap_info_path: Path to save the gap information
    """
    if not segments or not gaps:
        return

    base_time = segments[0].timestamp
    gap_data = {"base_timestamp": base_time.isoformat(), "gaps": []}

    for gap_start, gap_duration in gaps:
        start_offset = (gap_start - base_time).total_seconds()
        gap_data["gaps"].append({"start_offset": start_offset, "duration": gap_duration})

    try:
        save_yaml(gap_data, gap_info_path)
        logger.info(f"Gap information saved: {gap_info_path}")
    except Exception as e:
        logger.error(f"Failed to save gap information: {e}")


def apply_saved_gaps_to_timeline(timeline_path: Path, gap_info_path: Path) -> None:
    """
    Apply saved gap information to existing timeline.

    Args:
        timeline_path: Path to existing timeline JSON file
        gap_info_path: Path to gap information JSON file
    """
    try:
        if not gap_info_path.exists():
            return

        # Load gap information
        gap_data = load_timeline(gap_info_path)

        # Load existing timeline
        timeline_data = load_timeline(timeline_path)

        existing_timeline = timeline_data.get("timeline", [])

        # Add gap segments
        for gap in gap_data["gaps"]:
            start_offset = gap["start_offset"]
            duration = gap["duration"]
            end_offset = start_offset + duration

            gap_segment = {
                "start": seconds_to_mmss(start_offset),
                "end": seconds_to_mmss(end_offset),
                "duration": seconds_to_mmss(duration),
                "type": "silence",
                "speakers": 0,
                "label": "no_video",
                "audio_content": "gap",
            }

            # Find insertion point to maintain chronological order
            insert_index = 0
            for i, segment in enumerate(existing_timeline):
                segment_start = mmss_to_seconds(segment["start"])
                if segment_start > start_offset:
                    insert_index = i
                    break
                insert_index = i + 1

            existing_timeline.insert(insert_index, gap_segment)

        # Save updated timeline
        save_yaml(timeline_data, timeline_path)
        logger.info(f"Added {len(gap_data['gaps'])} NoVideo segments to timeline: {timeline_path}")

        # Clean up gap info file
        gap_info_path.unlink()

    except Exception as e:
        logger.error(f"Failed to apply gaps to timeline: {e}")


def add_video_removed_to_timeline(timeline_path: Path, removed_segments: List[Tuple[float, float]]) -> None:
    """
    Add VideoRemoved labels to existing timeline JSON file for segments where blank video was applied.

    Args:
        timeline_path: Path to existing timeline JSON file
        removed_segments: List of (start_time, end_time) tuples for removed video segments
    """
    try:
        # Load existing timeline
        timeline_data = load_timeline(timeline_path)

        existing_timeline = timeline_data.get("timeline", [])

        # Add VideoRemoved segments to timeline
        for start_time, end_time in removed_segments:
            duration = end_time - start_time
            removed_segment = {
                "start": seconds_to_mmss(start_time),
                "end": seconds_to_mmss(end_time),
                "duration": seconds_to_mmss(duration),
                "type": "silence",
                "speakers": 0,
                "label": "VideoRemoved",
                "audio_content": "speech_removed",
                "note": "Original video replaced with blank video",
            }

            # Find insertion point to maintain chronological order
            insert_index = 0
            for i, segment in enumerate(existing_timeline):
                segment_start = mmss_to_seconds(segment["start"])
                if segment_start > start_time:
                    insert_index = i
                    break
                insert_index = i + 1

            existing_timeline.insert(insert_index, removed_segment)

        # Save updated timeline
        save_yaml(timeline_data, timeline_path)
        logger.info(f"Added {len(removed_segments)} VideoRemoved segments to timeline: {timeline_path}")

    except Exception as e:
        logger.error(f"Failed to update timeline with video removed segments: {e}")


def extract_timestamp_from_filename(filename: str) -> Optional[datetime]:
    """
    Extracts the timestamp from a video filename.
    Expected format: YYYYMMDDHHMMSS_*.
    """
    match = re.match(r"(\d{14})_", filename)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y%m%d%H%M%S")
        except ValueError:
            return None
    return None


def get_video_properties(video_path: Path) -> Dict[str, Any]:
    """
    Retrieves video properties using ffprobe.

    Args:
        video_path: The path to the video file.

    Returns:
        A dictionary containing video properties like width, height, fps, etc.
    """
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-select_streams",
        "v:0",
        str(video_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        if not data.get("streams"):
            raise ValueError("No video stream found.")
        stream = data["streams"][0]
        r_frame_rate = stream.get("r_frame_rate", "30/1")
        return {
            "width": stream.get("width"),
            "height": stream.get("height"),
            "fps": _parse_frame_rate(r_frame_rate),
            "codec": stream.get("codec_name"),
            "pixel_format": stream.get("pix_fmt", "yuv420p"),
            "duration": float(stream.get("duration", 0)),
        }
    except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError) as e:
        logger.error(f"Failed to get properties for {video_path.name}: {e}")
        raise


def analyze_video_directory(directory: Path) -> List[VideoSegment]:
    """
    Analyzes a directory for valid video files and extracts their properties.
    """
    segments = []
    for f in sorted(directory.iterdir()):
        if f.is_file() and is_video_file(f):
            timestamp = extract_timestamp_from_filename(f.name)
            if timestamp:
                try:
                    properties = get_video_properties(f)
                    segments.append(VideoSegment(f, timestamp, properties["duration"]))
                except Exception as e:
                    logger.warning(f"Skipping {f.name} due to error: {e}")
    if not segments:
        raise ValueError("No valid video segments found in the directory.")
    return segments


def detect_gaps(
    segments: List[VideoSegment], min_gap_threshold: float = 2.0, max_gap_threshold: Optional[float] = None
) -> List[Tuple[datetime, float]]:
    """
    Detects gaps between video segments that meet the threshold criteria.

    Args:
        segments: List of video segments sorted by timestamp
        min_gap_threshold: Minimum gap duration to fill (default 2.0s)
        max_gap_threshold: Maximum gap duration to fill (None = no limit)
    """
    gaps = []
    for i in range(len(segments) - 1):
        gap_duration = (segments[i + 1].timestamp - segments[i].end_time).total_seconds()
        if gap_duration > min_gap_threshold:
            if max_gap_threshold is None or gap_duration <= max_gap_threshold:
                gaps.append((segments[i].end_time, gap_duration))
                logger.info(f"Gap of {gap_duration:.2f}s detected after {segments[i].file_path.name} (will be filled)")
            else:
                logger.info(
                    f"Gap of {gap_duration:.2f}s detected after {segments[i].file_path.name} (too large, skipping)"
                )
        elif gap_duration > 0.1:  # Log smaller gaps but don't fill them
            logger.debug(
                f"Small gap of {gap_duration:.2f}s detected after {segments[i].file_path.name} (below threshold)"
            )
    return gaps


def create_gap_video_from_blank(blank_video_path: Path, output_path: Path, duration: float) -> None:
    """
    Creates a gap video by trimming the blank video to the specified duration.
    Adds silent audio track matching the sample rate of source videos to ensure
    proper concatenation and muting of gap segments.
    """
    logger.info(f"Creating {duration:.2f}s gap video from blank video with silent audio")

    # If duration is longer than blank video, we need to loop it
    blank_properties = get_video_properties(blank_video_path)
    blank_duration = blank_properties["duration"]

    if duration <= blank_duration:
        # Simple trim - cut the blank video and add silent audio
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(blank_video_path),
            "-f",
            "lavfi",
            "-i",
            SILENT_AUDIO_MONO_CAMERA,  # Silent audio matching camera specs
            "-t",
            str(duration),
            "-c:v",
            "copy",  # Video stream copy for speed
            "-c:a",
            AUDIO_CODEC,  # Add silent audio track
            "-shortest",  # Match shortest stream duration
            str(output_path),
        ]
    else:
        # Need to loop the blank video to get the required duration
        loops = int(duration / blank_duration) + 1
        temp_list = output_path.parent / f"temp_blank_list_{output_path.stem}.txt"

        try:
            with open(temp_list, "w") as f:
                for _ in range(loops):
                    f.write(f"file '{blank_video_path.resolve()}'\n")

            cmd = [
                "ffmpeg",
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
                SILENT_AUDIO_MONO_CAMERA,  # Silent audio matching camera specs
                "-t",
                str(duration),
                "-c:v",
                "copy",  # Video stream copy for speed
                "-c:a",
                AUDIO_CODEC,  # Add silent audio track
                "-shortest",  # Match shortest stream duration
                str(output_path),
            ]

            run_subprocess_with_encoding(cmd, check=True)
            logger.info(
                f"Created gap video: {output_path} ({duration:.2f}s) from blank video (looped, with silent audio)"
            )
            return

        finally:
            if temp_list.exists():
                temp_list.unlink()

    run_subprocess_with_encoding(cmd, check=True)
    logger.info(f"Created gap video: {output_path} ({duration:.2f}s) from blank video (trimmed, with silent audio)")


def merge_videos(
    input_dir: Path,
    output_path: Path,
    blank_video: Path,
    min_gap_threshold: float = 2.0,
    max_gap_threshold: Optional[float] = None,
    merge_list_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """
    Merges all videos in a directory, filling gaps with black frames using a blank video file.

    Args:
        input_dir: Directory containing timestamped video files
        output_path: Path for the merged output video
        blank_video: Path to blank video file to use for gap filling
        min_gap_threshold: Minimum gap duration to fill (default 2.0s)
        max_gap_threshold: Maximum gap duration to fill (None = no limit)
        merge_list_path: Optional custom path for saving the merge list file
    """
    segments = analyze_video_directory(input_dir)
    if not segments:
        return

    gaps = detect_gaps(segments, min_gap_threshold, max_gap_threshold)
    _ = get_video_properties(segments[0].file_path)

    temp_dir = input_dir / "temp_merge_files"
    temp_dir.mkdir(exist_ok=True)

    try:
        timeline = []
        for segment in segments:
            timeline.append(("video", segment.timestamp, segment.file_path))

        for gap_start, gap_duration in gaps:
            timeline.append(("gap", gap_start, gap_duration))

        timeline.sort(key=lambda x: x[1])

        # Create gap videos from blank video file
        gap_videos = {}

        if gaps:
            if not blank_video.exists():
                raise FileNotFoundError(f"Blank video file not found: {blank_video}")

            logger.info(f"Using blank video file {blank_video.name} for {len(gaps)} gaps (stream copy)")
            for i, (gap_start, gap_duration) in enumerate(gaps):
                gap_video = temp_dir / f"gap_{i}.mp4"
                logger.info(
                    f"Creating gap video {i + 1}/{len(gaps)} with duration {gap_duration:.2f}s from blank video"
                )
                create_gap_video_from_blank(blank_video, gap_video, gap_duration)
                gap_videos[i] = gap_video

        # Use concat demuxer for stream copying (no re-encoding) with blank video
        concat_list_path = temp_dir / "concat_list.txt"

        # Also create a permanent copy of the concat list in the output directory
        if merge_list_path:
            permanent_concat_list = merge_list_path
        else:
            permanent_concat_list = output_path.parent / f"{output_path.stem}_merge_list.txt"

        with open(concat_list_path, "w") as f:
            gap_index = 0
            for item_type, _, item_path in timeline:
                if item_type == "video":
                    f.write(f"file '{item_path.resolve()}'\n")
                elif item_type == "gap":
                    gap_video = gap_videos[gap_index]
                    f.write(f"file '{gap_video.resolve()}'\n")
                    gap_index += 1

        # Copy the concat list to permanent location
        shutil.copy2(concat_list_path, permanent_concat_list)
        logger.info(f"Merge list saved: {permanent_concat_list}")

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
            "copy",  # Stream copy - NO re-encoding!
            str(output_path),
        ]
        logger.info("Using concat demuxer with stream copy (no re-encoding) - blank video method")
        logger.info("Starting video merge process...")
        logger.info(f"FFmpeg command: {' '.join(cmd)}")
        logger.info(f"Output path: {output_path} (type: {type(output_path)})")
        logger.info(f"Output path exists: {output_path.exists()}")
        if output_path.exists():
            logger.info(f"Output path is directory: {output_path.is_dir()}")
        run_subprocess_with_encoding(cmd, check=True)
        logger.info(f"Successfully merged videos to {output_path}")

        # Return gap information for integration into timeline
        if gaps and segments:
            base_time = segments[0].timestamp
            gap_data = []
            for gap_start, gap_duration in gaps:
                start_offset = (gap_start - base_time).total_seconds()
                gap_data.append({"start_offset": start_offset, "duration": gap_duration})
            logger.debug(f"Returning {len(gaps)} gaps for timeline integration")
            return {"gaps": gap_data}

        return None

    finally:
        shutil.rmtree(temp_dir)
