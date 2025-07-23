"""
Video merging functionality for processing multiple timestamped videos.

This module handles:
- Timestamp extraction from video filenames
- Gap detection between video segments
- Video concatenation with black frame filling for gaps
- Optimized H264 conversion as final processing step
"""

import json
import logging
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple, Optional

from .utils import run_subprocess_with_encoding, is_video_file

# Configure logging
logger = logging.getLogger(__name__)

# Global GPU acceleration settings
GPU_ACCELERATION = {"nvenc_available": False, "encoder": "libx264", "decoder": None, "checked": False}


def check_gpu_acceleration():
    """Check for available GPU acceleration options."""
    global GPU_ACCELERATION

    if GPU_ACCELERATION["checked"]:
        return GPU_ACCELERATION

    try:
        # Check for NVIDIA NVENC encoder
        result = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10)

        if "h264_nvenc" in result.stdout:
            GPU_ACCELERATION["nvenc_available"] = True
            GPU_ACCELERATION["encoder"] = "h264_nvenc"
            logger.info("NVIDIA NVENC acceleration detected and enabled")

        # Check for hardware decoder
        result = subprocess.run(["ffmpeg", "-hide_banner", "-hwaccels"], capture_output=True, text=True, timeout=10)

        if "cuda" in result.stdout:
            GPU_ACCELERATION["decoder"] = "cuda"
            logger.info("CUDA hardware decoding available")

    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
        logger.info("GPU acceleration check failed, using software encoding")

    GPU_ACCELERATION["checked"] = True
    return GPU_ACCELERATION


class VideoSegment:
    """Represents a video segment with timestamp information."""

    def __init__(self, file_path: Path, timestamp: datetime, duration: float):
        self.file_path = file_path
        self.timestamp = timestamp
        self.duration = duration
        self.end_time = timestamp + timedelta(seconds=duration)

    def __repr__(self):
        return f"VideoSegment({self.file_path.name}, {self.timestamp}, {self.duration}s)"


def extract_timestamp_from_filename(filename: str) -> Optional[datetime]:
    """
    Extract timestamp from video filename.

    Expected format: YYYYMMDDHHMMSS_XXXXXX.ext
    Example: 20250703175433_000028.MP4 -> 2025-07-03 17:54:33

    Args:
        filename: Video filename to parse

    Returns:
        datetime object if parsing successful, None otherwise
    """
    # Pattern for YYYYMMDDHHMMSS_XXXXXX.ext format
    pattern = r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})_\d+\."
    match = re.match(pattern, filename)

    if not match:
        return None

    try:
        year, month, day, hour, minute, second = map(int, match.groups())
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def get_video_duration(video_path: Path) -> float:
    """
    Get video duration in seconds using ffprobe.

    Args:
        video_path: Path to video file

    Returns:
        Duration in seconds

    Raises:
        subprocess.CalledProcessError: If ffprobe fails
        ValueError: If duration cannot be parsed
    """
    try:
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_entries", "format=duration", str(video_path)]

        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)

        duration = float(data["format"]["duration"])
        logger.debug(f"Video duration for {video_path.name}: {duration:.2f}s")
        return duration

    except subprocess.CalledProcessError as e:
        logger.error(f"ffprobe failed for {video_path}: {e.stderr}")
        raise
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as e:
        logger.error(f"Could not parse duration from {video_path}: {e}")
        raise ValueError(f"Could not extract duration from {video_path}: {e}")


def analyze_video_directory(directory: Path) -> List[VideoSegment]:
    """
    Analyze directory for timestamped videos and create segment list.

    Args:
        directory: Directory containing video files

    Returns:
        List of VideoSegment objects sorted by timestamp

    Raises:
        ValueError: If no valid timestamped videos found
    """
    video_files = [f for f in directory.iterdir() if f.is_file() and is_video_file(f)]

    if not video_files:
        raise ValueError(f"No video files found in {directory}")

    segments = []

    for video_file in video_files:
        timestamp = extract_timestamp_from_filename(video_file.name)
        if timestamp is None:
            print(f"Warning: Could not extract timestamp from {video_file.name}, skipping")
            continue

        try:
            duration = get_video_duration(video_file)
            segments.append(VideoSegment(video_file, timestamp, duration))
        except (subprocess.CalledProcessError, ValueError) as e:
            print(f"Warning: Could not get duration for {video_file.name}: {e}")
            continue

    if not segments:
        raise ValueError("No valid timestamped videos found with extractable durations")

    # Sort by timestamp
    segments.sort(key=lambda s: s.timestamp)

    return segments


def detect_gaps(
    segments: List[VideoSegment], max_gap_threshold: Optional[float] = None, min_gap_threshold: float = 0.5
) -> List[Tuple[datetime, float]]:
    """
    Detect gaps between video segments that need to be filled.

    Args:
        segments: List of sorted video segments
        max_gap_threshold: Maximum gap duration to consider (None = no limit)
        min_gap_threshold: Minimum gap duration to fill (default: 0.5 seconds)

    Returns:
        List of (start_time, duration) tuples for gaps to fill
    """
    if len(segments) < 2:
        return []

    gaps = []
    for i in range(len(segments) - 1):
        current_end = segments[i].timestamp + timedelta(seconds=segments[i].duration)
        next_start = segments[i + 1].timestamp

        gap_duration = (next_start - current_end).total_seconds()

        # Fill gap if it's positive, above minimum threshold, and within max threshold (or no max threshold)
        if gap_duration >= min_gap_threshold and (max_gap_threshold is None or gap_duration <= max_gap_threshold):
            gaps.append((current_end, gap_duration))
            logger.info(
                f"Gap detected: {gap_duration:.1f}s between {segments[i].file_path.name} and {segments[i + 1].file_path.name}"
            )
        elif gap_duration > 0 and gap_duration < min_gap_threshold:
            logger.debug(
                f"Gap too small ({gap_duration:.1f}s < {min_gap_threshold:.1f}s), skipping between {segments[i].file_path.name} and {segments[i + 1].file_path.name}"
            )
        elif gap_duration > 0 and max_gap_threshold is not None:
            logger.warning(
                f"Gap too large ({gap_duration:.1f}s > {max_gap_threshold:.1f}s), skipping between {segments[i].file_path.name} and {segments[i + 1].file_path.name}"
            )

    return gaps


def get_video_properties(video_path: Path) -> dict:
    """
    Get video properties (resolution, frame rate, codec) using ffprobe.

    Args:
        video_path: Path to video file

    Returns:
        Dictionary with video properties

    Raises:
        subprocess.CalledProcessError: If ffprobe fails
        ValueError: If video properties cannot be extracted
    """
    try:
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

        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)

        if not data.get("streams"):
            raise ValueError(f"No video stream found in {video_path}")

        stream = data["streams"][0]

        properties = {
            "width": stream.get("width"),
            "height": stream.get("height"),
            "fps": eval(stream.get("r_frame_rate", "30/1")),  # Convert fraction to float
            "codec": stream.get("codec_name"),
            "pixel_format": stream.get("pix_fmt", "yuv420p"),
        }

        logger.debug(f"Video properties for {video_path.name}: {properties}")
        return properties

    except subprocess.CalledProcessError as e:
        logger.error(f"ffprobe failed for {video_path}: {e.stderr}")
        raise
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as e:
        logger.error(f"Could not parse video properties from {video_path}: {e}")
        raise ValueError(f"Could not extract video properties from {video_path}: {e}")


def create_black_video(output_path: Path, duration: float, width: int, height: int, fps: float) -> None:
    """
    Create a black video file with specified duration and properties.

    Args:
        output_path: Output path for black video
        duration: Duration in seconds
        width: Video width
        height: Video height
        fps: Frame rate

    Raises:
        subprocess.CalledProcessError: If ffmpeg fails
        ValueError: If parameters are invalid
    """
    if duration <= 0:
        raise ValueError(f"Invalid duration: {duration}")
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid dimensions: {width}x{height}")
    if fps <= 0:
        raise ValueError(f"Invalid frame rate: {fps}")

    try:
        # Check GPU acceleration
        gpu_settings = check_gpu_acceleration()

        # Optimized command for black video generation with GPU acceleration
        cmd = [
            "ffmpeg",
            "-f",
            "lavfi",
            "-i",
            f"color=color=black:size={width}x{height}:rate={fps}",
            "-t",
            str(duration),
        ]

        # Use GPU encoder if available
        if gpu_settings["nvenc_available"]:
            cmd.extend(
                [
                    "-c:v",
                    "h264_nvenc",
                    "-preset",
                    "p1",  # Fastest NVENC preset
                    "-tune",
                    "ll",  # Low latency
                    "-rc",
                    "constqp",
                    "-qp",
                    "0",  # Lossless for intermediate
                ]
            )
        else:
            cmd.extend(
                [
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",  # Fastest CPU encoding
                    "-crf",
                    "0",  # Lossless for intermediate files
                ]
            )

        cmd.extend(
            [
                "-pix_fmt",
                "yuv420p",
                "-threads",
                "0",  # Use all available CPU cores
                str(output_path),
                "-y",
            ]
        )

        logger.info(f"Creating black video: {duration:.1f}s at {width}x{height}@{fps}fps")
        run_subprocess_with_encoding(cmd, check=True)

    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to create black video {output_path}: {e}")
        raise


def merge_videos_with_gaps(
    segments: List[VideoSegment],
    gaps: List[Tuple[datetime, float]],
    output_path: Path,
    convert_to_h264: bool = True,
    h264_preset: str = "medium",
    h264_crf: int = 23,
) -> None:
    """
    Merge videos with black frame gap filling.

    Args:
        segments: List of video segments to merge
        gaps: List of gaps to fill with black frames
        output_path: Output path for merged video
        convert_to_h264: Whether to convert final output to H264
        h264_preset: H264 encoding preset (ultrafast, fast, medium, slow, veryslow)
        h264_crf: H264 Constant Rate Factor (0-51, lower = better quality)

    Raises:
        ValueError: If parameters are invalid
        subprocess.CalledProcessError: If ffmpeg operations fail
    """
    if not segments:
        raise ValueError("No video segments to merge")

    if not (0 <= h264_crf <= 51):
        raise ValueError(f"Invalid H264 CRF value: {h264_crf} (must be 0-51)")

    valid_presets = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
    if h264_preset not in valid_presets:
        raise ValueError(f"Invalid H264 preset: {h264_preset}")

    # Check GPU acceleration at start
    gpu_settings = check_gpu_acceleration()

    try:
        # Get properties from first video
        reference_props = get_video_properties(segments[0].file_path)
        width = reference_props["width"]
        height = reference_props["height"]
        fps = reference_props["fps"]

        logger.info(f"Merging {len(segments)} video segments with {len(gaps)} gaps")
        logger.info(f"Reference properties: {width}x{height}@{fps}fps")

        # Create local temp directory in input folder for better cleanup control
        input_dir = segments[0].file_path.parent
        local_temp_dir = input_dir / "tmp"
        local_temp_dir.mkdir(exist_ok=True)

        try:
            temp_path = local_temp_dir

            # Create black videos for gaps
            gap_files = []
            gap_index = 0

            for gap_start, gap_duration in gaps:
                gap_file = temp_path / f"gap_{gap_index:03d}.mp4"
                try:
                    create_black_video(gap_file, gap_duration, width, height, fps)
                    gap_files.append((gap_start, gap_file))
                    gap_index += 1
                except Exception as e:
                    logger.error(f"Failed to create gap video {gap_index}: {e}")
                    raise

            # Create concat list file
            concat_file = temp_path / "concat_list.txt"

            # Build timeline combining videos and gaps
            timeline = []
            for segment in segments:
                timeline.append(("video", segment.timestamp, segment.file_path))

            for gap_start, gap_file in gap_files:
                timeline.append(("gap", gap_start, gap_file))

            # Sort by timestamp
            timeline.sort(key=lambda x: x[1])

            # Write concat file with proper escaping
            try:
                with open(concat_file, "w") as f:
                    for item_type, timestamp, file_path in timeline:
                        # Use absolute path and proper escaping for ffmpeg concat
                        abs_path = file_path.resolve() if hasattr(file_path, "resolve") else Path(file_path).resolve()
                        f.write(f"file '{abs_path}'\n")

                logger.info(f"Created concat list with {len(timeline)} items")

            except IOError as e:
                logger.error(f"Failed to create concat file: {e}")
                raise

            # Ensure output directory exists
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Debug: Check if output_path is being treated as directory
            if output_path.exists() and output_path.is_dir():
                logger.warning(f"Output path {output_path} exists as directory, removing it")
                import shutil

                shutil.rmtree(output_path)

            # Determine if we need intermediate processing
            if convert_to_h264:
                # Use intermediate file for conversion
                intermediate_path = temp_path / "merged_intermediate.mp4"
                final_output = output_path
            else:
                # Direct output
                intermediate_path = output_path
                final_output = None

            # Concatenate videos with optimized settings
            try:
                concat_cmd = [
                    "ffmpeg",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                ]

                # For HEVC inputs, avoid hardware decoding during concatenation
                # as it can cause NAL unit issues. Use CPU decoding instead.
                concat_cmd.extend(
                    [
                        "-i",
                        str(concat_file),
                        "-c",
                        "copy",  # Copy streams without re-encoding for speed
                        "-avoid_negative_ts",
                        "make_zero",  # Handle timestamp issues
                        "-fflags",
                        "+genpts",  # Generate presentation timestamps
                        "-max_muxing_queue_size",
                        "1024",  # Handle large queues
                        str(intermediate_path),
                        "-y",
                    ]
                )

                logger.info(f"Concatenating {len(segments)} videos with {len(gaps)} gaps...")
                logger.debug(f"Concat command: {' '.join(concat_cmd)}")

                _ = subprocess.run(concat_cmd, capture_output=True, text=True, check=True)
                logger.info("Video concatenation completed successfully")

            except subprocess.CalledProcessError as e:
                logger.error(f"Video concatenation failed: {e}")
                logger.error(f"FFmpeg stderr: {e.stderr}")

                # Fallback: Try concatenation with re-encoding to avoid codec issues
                logger.info("Trying fallback concatenation with re-encoding...")
                try:
                    fallback_cmd = [
                        "ffmpeg",
                        "-f",
                        "concat",
                        "-safe",
                        "0",
                        "-i",
                        str(concat_file),
                        "-c:v",
                        "libx264",  # Re-encode to H264 to avoid HEVC issues
                        "-preset",
                        "faster",
                        "-crf",
                        "23",
                        "-avoid_negative_ts",
                        "make_zero",
                        "-fflags",
                        "+genpts",
                        "-max_muxing_queue_size",
                        "1024",
                        str(intermediate_path),
                        "-y",
                    ]

                    _ = subprocess.run(fallback_cmd, capture_output=True, text=True, check=True)
                    logger.info("Fallback concatenation completed successfully")

                except subprocess.CalledProcessError as fallback_error:
                    logger.error(f"Fallback concatenation also failed: {fallback_error}")
                    logger.error(f"Fallback stderr: {fallback_error.stderr}")
                    return False

            # Convert to H264 if requested
            if convert_to_h264 and final_output:
                try:
                    logger.info(f"Converting to H264 (preset: {h264_preset}, CRF: {h264_crf})...")

                    # Build optimized H264 conversion command
                    h264_cmd = ["ffmpeg"]

                    # Add hardware decoding (must be before input)
                    if gpu_settings["decoder"]:
                        h264_cmd.extend(["-hwaccel", gpu_settings["decoder"]])

                    h264_cmd.extend(["-i", str(intermediate_path)])

                    # Configure encoder
                    if gpu_settings["nvenc_available"]:
                        # NVENC settings
                        nvenc_preset_map = {
                            "ultrafast": "p1",
                            "superfast": "p1",
                            "veryfast": "p2",
                            "faster": "p3",
                            "fast": "p4",
                            "medium": "p5",
                            "slow": "p6",
                            "slower": "p7",
                            "veryslow": "p7",
                        }
                        nvenc_preset = nvenc_preset_map.get(h264_preset, "p4")

                        h264_cmd.extend(
                            [
                                "-c:v",
                                "h264_nvenc",
                                "-preset",
                                nvenc_preset,
                                "-rc",
                                "vbr",
                                "-cq",
                                str(h264_crf),
                                "-b:v",
                                "0",  # Use CQ mode
                                "-maxrate",
                                "50M",
                                "-bufsize",
                                "100M",
                            ]
                        )
                    else:
                        # CPU libx264 settings
                        h264_cmd.extend(
                            [
                                "-c:v",
                                "libx264",
                                "-preset",
                                h264_preset,
                                "-crf",
                                str(h264_crf),
                                "-tune",
                                "film",  # Optimize for video content
                            ]
                        )

                    h264_cmd.extend(
                        [
                            "-pix_fmt",
                            "yuv420p",
                            "-movflags",
                            "+faststart",  # Optimize for streaming
                            "-threads",
                            "0",  # Use all available CPU cores
                            str(final_output),
                            "-y",
                        ]
                    )

                    run_subprocess_with_encoding(h264_cmd, check=True)
                    logger.info(f"H264 conversion complete: {final_output}")

                except subprocess.CalledProcessError as e:
                    logger.error(f"H264 conversion failed: {e}")
                    raise
            else:
                logger.info(f"Video merging complete: {intermediate_path}")

        except Exception as e:
            logger.error(f"Error in merge_videos_with_gaps: {e}")
            raise
        finally:
            # Clean up local temp directory
            if local_temp_dir.exists():
                import shutil

                try:
                    shutil.rmtree(local_temp_dir)
                    logger.debug(f"Cleaned up temp directory: {local_temp_dir}")
                except Exception as cleanup_error:
                    logger.warning(f"Failed to clean up temp directory {local_temp_dir}: {cleanup_error}")

    except Exception as e:
        logger.error(f"Error in merge_videos_with_gaps: {e}")
        raise


def merge_directory_videos(
    input_dir: Path,
    output_path: Path,
    max_gap_threshold: Optional[float] = None,
    convert_to_h264: bool = True,
    h264_preset: str = "faster",
    h264_crf: int = 28,
) -> bool:
    """
    Merge multiple timestamped videos in a directory with gap filling.

    Args:
        input_dir: Directory containing timestamped video files
        output_path: Path for merged output video
        max_gap_threshold: Maximum gap duration to fill (None = fill all gaps)
        convert_to_h264: Whether to convert final output to H264
        h264_preset: H264 encoding preset for speed/quality balance
        h264_crf: H264 constant rate factor for quality control

    Returns:
        True if successful, False otherwise
    """
    try:
        logger.info(f"Analyzing videos in {input_dir}")
        segments = analyze_video_directory(input_dir)

        if len(segments) == 1:
            logger.info("Only one video found, no merging needed")

            try:
                if convert_to_h264:
                    # Still convert single video to H264
                    logger.info("Converting single video to H264...")
                    h264_cmd = [
                        "ffmpeg",
                        "-i",
                        str(segments[0].file_path),
                        "-c:v",
                        "libx264",
                        "-preset",
                        h264_preset,
                        "-crf",
                        str(h264_crf),
                        "-pix_fmt",
                        "yuv420p",
                        "-movflags",
                        "+faststart",
                        str(output_path),
                        "-y",
                    ]
                    run_subprocess_with_encoding(h264_cmd, check=True)
                else:
                    # Just copy the file
                    logger.info("Copying single video file...")
                    subprocess.run(
                        ["ffmpeg", "-i", str(segments[0].file_path), "-c", "copy", str(output_path), "-y"], check=True
                    )
                return True

            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to process single video: {e}")
                return False

        logger.info(f"Found {len(segments)} video segments:")
        for segment in segments:
            logger.info(f"  {segment.file_path.name}: {segment.timestamp} ({segment.duration:.1f}s)")

        # Use all gaps if max_gap_threshold is None
        if max_gap_threshold is None:
            gaps = detect_gaps(segments)
        else:
            gaps = detect_gaps(segments, max_gap_threshold)

        if gaps:
            logger.info(f"Detected {len(gaps)} gaps to fill:")
            for gap_start, gap_duration in gaps:
                logger.info(f"  {gap_start}: {gap_duration:.1f}s")
        else:
            logger.info("No gaps detected between video segments")

        merge_videos_with_gaps(segments, gaps, output_path, convert_to_h264, h264_preset, h264_crf)

        return True

    except Exception as e:
        logger.error(f"Error merging videos: {e}")
        print(f"Error merging videos: {e}")
        return False
