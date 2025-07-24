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

from .utils import run_subprocess_with_encoding, is_video_file

logger = logging.getLogger(__name__)

GPU_ACCELERATION: Dict[str, Any] = {
    "nvenc_available": False,
    "hevc_nvenc_available": False,
    "encoder": "libx264",
    "decoder": None,
    "checked": False,
}


def check_gpu_acceleration() -> Dict[str, Any]:
    """
    Checks for available GPU acceleration options and updates the global settings.

    Returns:
        A dictionary with the detected GPU acceleration settings.
    """
    global GPU_ACCELERATION
    if GPU_ACCELERATION["checked"]:
        return GPU_ACCELERATION

    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        if "h264_nvenc" in result.stdout:
            GPU_ACCELERATION["nvenc_available"] = True
            GPU_ACCELERATION["encoder"] = "h264_nvenc"
            logger.info("NVIDIA H264 NVENC acceleration detected.")
        if "hevc_nvenc" in result.stdout:
            GPU_ACCELERATION["hevc_nvenc_available"] = True
            logger.info("NVIDIA H265/HEVC NVENC acceleration detected.")

        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-hwaccels"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        if "cuda" in result.stdout:
            GPU_ACCELERATION["decoder"] = "cuda"
            logger.info("CUDA hardware decoding available.")
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning(f"GPU acceleration check failed, using software encoding. Error: {e}")

    GPU_ACCELERATION["checked"] = True
    return GPU_ACCELERATION


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
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-select_streams", "v:0",
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


def detect_gaps(segments: List[VideoSegment], min_gap_threshold: float = 0.5, max_gap_threshold: Optional[float] = None) -> List[Tuple[datetime, float]]:
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
                logger.info(f"Gap of {gap_duration:.2f}s detected after {segments[i].file_path.name} (too large, skipping)")
        elif gap_duration > 0.1:  # Log smaller gaps but don't fill them
            logger.debug(f"Small gap of {gap_duration:.2f}s detected after {segments[i].file_path.name} (below threshold)")
    return gaps


def create_black_video(output_path: Path, duration: float, properties: Dict[str, Any]) -> None:
    """
    Creates a black video with properties matching the input video.
    """
    # Use the same codec as the source videos to avoid compatibility issues
    source_codec = properties.get("codec", "h264")
    
    # Convert fps to fraction format for better precision
    fps = properties["fps"]
    if isinstance(fps, float) and fps.is_integer():
        fps_str = f"{int(fps)}/1"
    else:
        fps_str = f"{fps:.6f}"
    
    # Use compatible pixel format for encoding
    pixel_format = properties["pixel_format"]
    if pixel_format == "yuvj420p":
        pixel_format = "yuv420p"  # Convert incompatible format to compatible one
    
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=black:s={properties['width']}x{properties['height']}:rate={fps_str}",
        "-f", "lavfi",
        "-i", "anullsrc=channel_layout=mono:sample_rate=32000",
        "-t", str(duration),
        "-pix_fmt", pixel_format,
    ]
    
    # Match the source codec exactly with compatible settings
    if source_codec == "hevc":
        gpu_settings = check_gpu_acceleration()
        if gpu_settings.get("hevc_nvenc_available"):
            cmd.extend([
                "-c:v", "hevc_nvenc", 
                "-preset", "p1",
                "-bf", "0",  # No B-frames to match source
                "-refs", "1"  # Single reference frame to match source
            ])
        else:
            cmd.extend([
                "-c:v", "libx265", 
                "-preset", "ultrafast",
                "-x265-params", "bframes=0:ref=1"  # Match source settings
            ])
    else:
        gpu_settings = check_gpu_acceleration()
        encoder = gpu_settings.get("encoder", "libx264")
        if "nvenc" in encoder:
            cmd.extend(["-c:v", encoder, "-preset", "p1"])
        else:
            cmd.extend(["-c:v", encoder, "-preset", "ultrafast"])
    
    # Add additional encoding parameters for better compatibility
    cmd.extend(["-movflags", "+faststart"])
    cmd.append(str(output_path))
    
    run_subprocess_with_encoding(cmd, check=True)
    logger.info(f"Created black video at {output_path} with duration {duration:.2f}s using codec {source_codec}")


def merge_videos(input_dir: Path, output_path: Path, min_gap_threshold: float = 0.5, max_gap_threshold: Optional[float] = None, convert_h264: bool = True) -> None:
    """
    Merges all videos in a directory, filling gaps with black frames.
    
    Args:
        input_dir: Directory containing timestamped video files
        output_path: Path for the merged output video
        min_gap_threshold: Minimum gap duration to fill (default 0.5s)
        max_gap_threshold: Maximum gap duration to fill (None = no limit)
        convert_h264: Whether to convert to H264 or keep original codec (default True)
    """
    check_gpu_acceleration()
    segments = analyze_video_directory(input_dir)
    if not segments:
        return

    gaps = detect_gaps(segments, min_gap_threshold, max_gap_threshold)
    ref_props = get_video_properties(segments[0].file_path)

    temp_dir = input_dir / "temp_merge_files"
    temp_dir.mkdir(exist_ok=True)

    try:
        timeline = []
        for segment in segments:
            timeline.append(("video", segment.timestamp, segment.file_path))

        for gap_start, gap_duration in gaps:
            timeline.append(("gap", gap_start, gap_duration))

        timeline.sort(key=lambda x: x[1])

        # Create individual black videos for each gap to avoid HEVC stream corruption
        gap_videos = {}
        if gaps:
            logger.info(f"Creating individual black videos for {len(gaps)} gaps")
            for i, (gap_start, gap_duration) in enumerate(gaps):
                gap_video = temp_dir / f"gap_{i}.mp4"
                logger.info(f"Creating gap video {i+1}/{len(gaps)} with duration {gap_duration:.2f}s")
                create_black_video(gap_video, gap_duration, ref_props)
                gap_videos[i] = gap_video
        
        concat_list_path = temp_dir / "concat_list.txt"
        with open(concat_list_path, "w") as f:
            gap_index = 0
            for item_type, _, item_path in timeline:
                if item_type == "video":
                    f.write(f"file '{item_path.resolve()}'\n")
                elif item_type == "gap":
                    # Use the individual gap video for this gap
                    gap_video = gap_videos[gap_index]
                    f.write(f"file '{gap_video.resolve()}'\n")
                    gap_index += 1

        gpu_settings = check_gpu_acceleration()
        decoder = ["-hwaccel", gpu_settings["decoder"]] if gpu_settings.get("decoder") else []
        
        # Choose encoder based on convert_h264 flag and source codec
        source_codec = ref_props.get("codec", "h264")
        if convert_h264:
            # Force H264 encoding
            encoder = ["-c:v", gpu_settings.get("encoder", "libx264")]
        else:
            # Keep original codec or use HEVC if available
            if source_codec == "hevc" and gpu_settings.get("hevc_nvenc_available"):
                encoder = ["-c:v", "hevc_nvenc"]
                logger.info("Using HEVC NVENC encoder (GPU accelerated)")
            elif source_codec == "hevc":
                encoder = ["-c:v", "libx265"]
                logger.info("Using HEVC software encoder")
            else:
                # For non-HEVC sources, use the best available encoder
                encoder = ["-c:v", gpu_settings.get("encoder", "libx264")]

        # Ensure compatible pixel format for the encoder
        pixel_format = ref_props["pixel_format"]
        if "nvenc" in encoder[1] and pixel_format == "yuvj420p":
            pixel_format = "yuv420p"  # NVENC doesn't support yuvj420p
        
        # Build input files list for filter_complex approach
        input_files = []
        filter_inputs = []
        gap_index = 0
        
        for i, (item_type, _, item_path) in enumerate(timeline):
            if item_type == "video":
                input_files.extend(["-i", str(item_path.resolve())])
                filter_inputs.append(f"[{i}:v][{i}:a]")
            elif item_type == "gap":
                gap_video = gap_videos[gap_index]
                input_files.extend(["-i", str(gap_video.resolve())])
                filter_inputs.append(f"[{i}:v][{i}:a]")
                gap_index += 1
        
        # Use filter_complex for more robust concatenation
        filter_complex = "".join(filter_inputs) + f"concat=n={len(timeline)}:v=1:a=1[outv][outa]"
        
        cmd = [
            "ffmpeg", "-y", *decoder,
            *input_files,
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-map", "[outa]",
            *encoder,
            "-pix_fmt", pixel_format,
            str(output_path),
        ]
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
