"""
Video merging functionality for processing multiple timestamped videos.
"""

import json
import logging
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

from .utils import run_subprocess_with_encoding, normalize_path_for_ffmpeg, is_video_file

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
        return {
            "width": stream.get("width"),
            "height": stream.get("height"),
            "fps": eval(stream.get("r_frame_rate", "30/1")),
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
    segments: List[VideoSegment], min_gap_threshold: float = 0.5, max_gap_threshold: Optional[float] = None
) -> List[Tuple[datetime, float]]:
    """
    Detects gaps between video segments that meet the threshold criteria.

    Args:
        segments: List of video segments sorted by timestamp
        min_gap_threshold: Minimum gap duration to fill (default 0.5s)
        max_gap_threshold: Maximum gap duration to fill (None = no limit)
    """
    gaps = []
    for i in range(len(segments) - 1):
        gap_duration = (segments[i + 1].timestamp - segments[i].end_time).total_seconds()
        if gap_duration >= min_gap_threshold:
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
    Uses stream copy for maximum speed - no encoding required.
    Automatically removes audio to ensure gaps are silent.
    """
    logger.info(f"Creating {duration:.2f}s gap video from blank video (stream copy)")

    # If duration is longer than blank video, we need to loop it
    blank_properties = get_video_properties(blank_video_path)
    blank_duration = blank_properties["duration"]

    if duration <= blank_duration:
        # Simple trim - just cut the blank video to the needed duration
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(blank_video_path),
            "-t",
            str(duration),
            "-c:v",
            "copy",  # Video stream copy for speed
            "-an",  # Remove audio to ensure silence
            str(output_path),
        ]
    else:
        # Need to loop the blank video to get the required duration
        loops = int(duration / blank_duration) + 1
        temp_list = output_path.parent / f"temp_blank_list_{output_path.stem}.txt"

        try:
            with open(temp_list, "w") as f:
                for _ in range(loops):
                    f.write(f"file '{normalize_path_for_ffmpeg(blank_video_path)}'\n")

            cmd = [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(temp_list),
                "-t",
                str(duration),
                "-c:v",
                "copy",  # Video stream copy for speed
                "-an",  # Remove audio to ensure silence
                str(output_path),
            ]

            run_subprocess_with_encoding(cmd, check=True)
            logger.info(f"Created gap video: {output_path} ({duration:.2f}s) from blank video (looped, muted)")
            return

        finally:
            if temp_list.exists():
                temp_list.unlink()

    run_subprocess_with_encoding(cmd, check=True)
    logger.info(f"Created gap video: {output_path} ({duration:.2f}s) from blank video (trimmed, muted)")


def merge_videos(
    input_dir: Path,
    output_path: Path,
    blank_video: Path,
    min_gap_threshold: float = 0.5,
    max_gap_threshold: Optional[float] = None,
) -> None:
    """
    Merges all videos in a directory, filling gaps with black frames using a blank video file.

    Args:
        input_dir: Directory containing timestamped video files
        output_path: Path for the merged output video
        blank_video: Path to blank video file to use for gap filling
        min_gap_threshold: Minimum gap duration to fill (default 0.5s)
        max_gap_threshold: Maximum gap duration to fill (None = no limit)
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
        with open(concat_list_path, "w") as f:
            gap_index = 0
            for item_type, _, item_path in timeline:
                if item_type == "video":
                    f.write(f"file '{normalize_path_for_ffmpeg(item_path)}'\n")
                elif item_type == "gap":
                    gap_video = gap_videos[gap_index]
                    f.write(f"file '{normalize_path_for_ffmpeg(gap_video)}'\n")
                    gap_index += 1

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

    finally:
        import shutil

        shutil.rmtree(temp_dir)
