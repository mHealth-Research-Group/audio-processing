import subprocess
import sys
import yaml
from pathlib import Path
from datetime import datetime
from typing import List, Optional

# Video and audio file extensions
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"}

# Audio processing constants
# Sample rates for different use cases
SAMPLE_RATE_ML = 16000  # For machine learning models (pyannote, etc.)
SAMPLE_RATE_CAMERA = 32000  # For camera/recording device compatibility
SAMPLE_RATE_HIGH_QUALITY = 48000  # For high-quality audio processing

# Audio codec and encoding settings
AUDIO_CODEC = "aac"  # Standard audio codec for broad compatibility
AUDIO_BITRATE_STANDARD = "128k"  # Standard audio bitrate
AUDIO_BITRATE_HIGH = "192k"  # Higher quality audio bitrate

# Channel layouts
CHANNEL_LAYOUT_MONO = "mono"
CHANNEL_LAYOUT_STEREO = "stereo"

# Silent audio source generators for FFmpeg
SILENT_AUDIO_MONO_CAMERA = f"anullsrc=channel_layout={CHANNEL_LAYOUT_MONO}:sample_rate={SAMPLE_RATE_CAMERA}"
SILENT_AUDIO_STEREO_HIGH = f"anullsrc=channel_layout={CHANNEL_LAYOUT_STEREO}:sample_rate={SAMPLE_RATE_HIGH_QUALITY}"

# Effect configuration for different labels
EFFECT_CONFIGS = {
    "speaking": {"mute_audio": True, "black_video": False},
    "conversation": {"mute_audio": True, "black_video": False},
    "silence": {"mute_audio": False, "black_video": False},
    "black": {"mute_audio": False, "black_video": True},
    "mute": {"mute_audio": True, "black_video": False},
    "all": {"mute_audio": True, "black_video": True},
    "NoVideo": {"mute_audio": False, "black_video": False},  # Missing video segments
    "VideoRemoved": {"mute_audio": False, "black_video": False},  # Blanked video segments
    "removed": {"mute_audio": True, "black_video": True},  # General removed content
    "audio_removed": {"mute_audio": True, "black_video": False},  # Audio-only removal
    "video_only": {"mute_audio": False, "black_video": True},  # Video-only removal
    "speaker_1": {"mute_audio": False, "black_video": False},  # Single speaker segments
    "speaker_2": {"mute_audio": False, "black_video": False},  # Second speaker segments
    "multiple_speakers": {"mute_audio": False, "black_video": False},  # Multiple speaker segments
}


def ensure_utf8_encoding():
    """Ensure that stdout and stderr use UTF-8 encoding, especially on Windows."""
    if sys.platform.startswith("win"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except AttributeError:
            import codecs

            sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer)
            sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer)


def run_subprocess_with_encoding(*args, **kwargs):
    """Helper function to run subprocess with proper encoding on Windows."""
    if sys.platform.startswith("win") and "encoding" not in kwargs and kwargs.get("text") is True:
        kwargs["encoding"] = "utf-8"
    return subprocess.run(*args, **kwargs)


def normalize_path_for_ffmpeg(path):
    """
    Normalize path for FFmpeg usage, handling Windows path issues.

    Args:
        path: Path object or string

    Returns:
        String path safe for FFmpeg usage
    """
    if isinstance(path, str):
        path = Path(path)

    # Get absolute path
    abs_path = path.resolve()

    # On Windows, convert backslashes to forward slashes for FFmpeg
    if sys.platform.startswith("win"):
        # Convert to string and replace backslashes
        path_str = str(abs_path).replace("\\", "/")
        return path_str
    else:
        return str(abs_path)


def load_yaml(file_path):
    """Load YAML from file, trying multiple encodings."""
    encodings_to_try = ["utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp1252", "latin1"]
    for encoding in encodings_to_try:
        try:
            with open(file_path, "r", encoding=encoding) as f:
                return yaml.safe_load(f)
        except (UnicodeDecodeError, UnicodeError, yaml.YAMLError):
            continue
    raise ValueError(f"Could not decode YAML file {file_path}")


def save_yaml(data, file_path):
    """Save data to YAML file with proper formatting."""
    with open(file_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, indent=2, sort_keys=False)


def load_timeline(timeline_path):
    """Load timeline from YAML file, trying multiple encodings."""
    return load_yaml(timeline_path)


def mmss_to_seconds(mmss_str):
    """Convert MM:SS.sss format to seconds."""
    parts = mmss_str.split(":")
    minutes = int(parts[0])
    seconds = float(parts[1])
    return minutes * 60 + seconds


def seconds_to_mmss(seconds):
    """Convert seconds to MM:SS.mmm format."""
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes}:{secs:06.3f}"


def parse_time_range(time_str):
    """Parse time range string into start and end seconds."""
    if "-" in time_str:
        start_str, end_str = time_str.split("-", 1)
        start_time = parse_single_time(start_str.strip())
        end_time = parse_single_time(end_str.strip())
        return start_time, end_time
    else:
        time_seconds = parse_single_time(time_str.strip())
        return time_seconds, None


def parse_single_time(time_str):
    """Parse a single time string into seconds."""
    if ":" in time_str:
        parts = time_str.split(":")
        minutes = int(parts[0])
        seconds = float(parts[1])
        return minutes * 60 + seconds
    else:
        return float(time_str)


def is_video_file(file_path):
    """Check if the file is a video file based on its extension."""
    return Path(file_path).suffix.lower() in VIDEO_EXTENSIONS


def is_audio_file(file_path):
    """Check if the file is an audio file based on its extension."""
    return Path(file_path).suffix.lower() in AUDIO_EXTENSIONS


def find_timeline_files(directory):
    """Find all timeline YAML files in a directory."""
    return sorted(Path(directory).glob("*_timeline.yaml"))


def find_media_file_for_timeline(timeline_path):
    """Find the corresponding media file for a timeline file."""
    base_name = timeline_path.stem.replace("_timeline", "")
    directory = timeline_path.parent
    for ext in list(VIDEO_EXTENSIONS) + list(AUDIO_EXTENSIONS):
        media_path = directory / f"{base_name}{ext}"
        if media_path.exists():
            return media_path
    return None


def find_media_files(directory: Path):
    """Find all audio and video files in a directory."""
    media_files = []
    for file_path in directory.iterdir():
        if file_path.is_file() and (is_video_file(file_path) or is_audio_file(file_path)):
            media_files.append(file_path)
    return sorted(media_files)


def extract_timestamp_from_filename(filename: str) -> Optional[datetime]:
    """Extract timestamp from filename with format YYYYMMDDHHMMSS_XXXXXX.ext"""
    import re

    match = re.match(r"(\d{14})_", filename)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y%m%d%H%M%S")
        except ValueError:
            return None
    return None


def generate_merged_filename(video_files: List[Path]) -> str:
    """Generate filename for merged video: YYYYMMDD-from-first-clip_merged.mp4"""
    if not video_files:
        return "merged.mp4"

    # Find first timestamped video
    first_timestamp = None
    for video_file in sorted(video_files):
        timestamp = extract_timestamp_from_filename(video_file.name)
        if timestamp:
            first_timestamp = timestamp
            break

    if first_timestamp:
        # Format: YYYYMMDD_merged.mp4 (e.g., 20240811_merged.mp4)
        day_str = first_timestamp.strftime("%Y%m%d")
        return f"{day_str}_merged.mp4"
    else:
        return "merged.mp4"


def generate_processed_filename(input_path: Path) -> str:
    """Generate filename for processed video: YYYYMMDD_processed.mp4"""
    timestamp = extract_timestamp_from_filename(input_path.name)
    if timestamp:
        # Format: YYYYMMDD_processed.mp4 (e.g., 20240811_processed.mp4)
        day_str = timestamp.strftime("%Y%m%d")
        return f"{day_str}_processed.mp4"
    else:
        # Fallback to original name with _processed suffix
        return f"{input_path.stem}_processed{input_path.suffix}"


def generate_compressed_filename(input_path: Path) -> str:
    """Generate filename for compressed video: YYYYMMDD_compressed.mp4"""
    timestamp = extract_timestamp_from_filename(input_path.name)
    if timestamp:
        # Format: YYYYMMDD_compressed.mp4 (e.g., 20240811_compressed.mp4)
        day_str = timestamp.strftime("%Y%m%d")
        return f"{day_str}_compressed.mp4"
    else:
        # Fallback to original name with _compressed suffix
        return f"{input_path.stem}_compressed{input_path.suffix}"
