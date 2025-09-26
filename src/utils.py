import subprocess
import sys
import yaml
import time
from pathlib import Path
from datetime import datetime, timedelta
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


def compare_timelines(original_timeline, modified_timeline):
    """
    Compare two timelines and identify segments that have changed.

    Args:
        original_timeline: Timeline data from original processing (all segments marked as 'speech'/'silence')
        modified_timeline: Timeline data with manual edits (some segments changed to 'all')

    Returns:
        dict with changed_segments, unchanged_segments, and processing_plan
    """
    original_segments = original_timeline.get("timeline", [])
    modified_segments = modified_timeline.get("timeline", [])

    changed_segments = []
    unchanged_segments = []

    # Create a mapping for quick lookup
    modified_dict = {}
    for segment in modified_segments:
        key = (segment["start"], segment["end"])
        modified_dict[key] = segment

    # Compare each original segment with modified version
    for orig_segment in original_segments:
        key = (orig_segment["start"], orig_segment["end"])

        if key in modified_dict:
            modified_segment = modified_dict[key]

            # Check if segment type changed to require processing
            orig_type = orig_segment.get("type", "")
            modified_type = modified_segment.get("type", "")

            # If segment was changed to 'all' or has effects, mark as changed
            if (modified_type == "all" or
                modified_type in ["speaking", "conversation"] or
                modified_segment.get("label", "") in EFFECT_CONFIGS):
                changed_segments.append(modified_segment)
            else:
                unchanged_segments.append(modified_segment)
        else:
            # Segment was removed in modified timeline
            unchanged_segments.append(orig_segment)

    # Check for new segments in modified timeline
    original_dict = {(s["start"], s["end"]): s for s in original_segments}
    for modified_segment in modified_segments:
        key = (modified_segment["start"], modified_segment["end"])
        if key not in original_dict:
            changed_segments.append(modified_segment)

    return {
        "changed_segments": changed_segments,
        "unchanged_segments": unchanged_segments,
        "total_changed": len(changed_segments),
        "total_unchanged": len(unchanged_segments),
        "change_percentage": len(changed_segments) / len(modified_segments) * 100 if modified_segments else 0
    }


def split_timeline_into_batches(segments, batch_duration_seconds=600):
    """
    Split timeline segments into temporal batches to prevent FFmpeg filter explosion.

    Args:
        segments: List of timeline segments to process
        batch_duration_seconds: Duration of each batch in seconds (default 10 minutes)

    Returns:
        List of batches, each containing segments within that time range
    """
    if not segments:
        return []

    # Sort segments by start time
    sorted_segments = sorted(segments, key=lambda s: mmss_to_seconds(s["start"]))

    batches = []
    current_batch = []
    current_batch_start = 0

    for segment in sorted_segments:
        segment_start = mmss_to_seconds(segment["start"])

        # If this segment starts beyond current batch window, start new batch
        if segment_start >= current_batch_start + batch_duration_seconds:
            if current_batch:
                batches.append(current_batch)
            current_batch = [segment]
            current_batch_start = segment_start
        else:
            current_batch.append(segment)

    # Add final batch if it has segments
    if current_batch:
        batches.append(current_batch)

    return batches


def get_timeline_cache_path(timeline_path):
    """Get the cache path for storing original timeline for comparison."""
    timeline_path = Path(timeline_path)
    cache_dir = timeline_path.parent / ".timeline_cache"
    cache_dir.mkdir(exist_ok=True)
    return cache_dir / f"{timeline_path.stem}_original.yaml"


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


def hhmmss_to_seconds(time_str):
    """Convert HH:MM:SS format to seconds."""
    parts = time_str.split(":")
    if len(parts) == 3:  # HH:MM:SS
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
        return hours * 3600 + minutes * 60 + seconds
    elif len(parts) == 2:  # MM:SS
        minutes = int(parts[0])
        seconds = float(parts[1])
        return minutes * 60 + seconds
    else:
        return float(time_str)


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


class ProgressTracker:
    """Track progress for operations with ETA estimation."""

    def __init__(self, total_duration, operation_name="Processing"):
        self.start_time = time.time()
        self.last_update = self.start_time
        self.total_duration = total_duration
        self.operation_name = operation_name

    def update(self, current_progress):
        """Update progress and print ETA if enough time has passed."""
        now = time.time()

        # Only print updates every 3 seconds to avoid spam
        if now - self.last_update >= 3:
            elapsed = now - self.start_time
            elapsed_str = str(timedelta(seconds=int(elapsed)))

            progress_pct = min(100, (current_progress / self.total_duration) * 100)

            if current_progress > 0:
                # ETA calculation
                estimated_total = elapsed * (self.total_duration / current_progress)
                eta_seconds = estimated_total - elapsed
                eta_str = str(timedelta(seconds=int(max(0, eta_seconds))))

                print(f"Progress {self.operation_name}: {progress_pct:.1f}% | Elapsed: {elapsed_str} | ETA: {eta_str}")
            else:
                print(f"Progress {self.operation_name}: Starting... | Elapsed: {elapsed_str}")

            self.last_update = now

    def complete(self, success=True):
        """Mark operation as complete and print final stats."""
        elapsed = time.time() - self.start_time
        elapsed_str = str(timedelta(seconds=int(elapsed)))
        status = "Completed" if success else "Failed"
        print(f"{status} {self.operation_name} in {elapsed_str}")


class MultiStepProgressTracker:
    """Track progress for multi-step operations with correct ETA estimation."""

    def __init__(self, total_steps, operation_name="Multi-step Processing"):
        self.start_time = time.time()
        self.last_update = self.start_time
        self.total_steps = total_steps
        self.operation_name = operation_name
        self.current_step = 0
        self.current_step_progress = 0.0  # 0.0 to 1.0 within current step
        self.step_names = []

    def set_step_names(self, step_names):
        """Set descriptive names for each step."""
        self.step_names = step_names

    def update_step(self, step_number, step_progress=0.0, step_name=None):
        """Update progress within a specific step.

        Args:
            step_number: Current step (0-based index)
            step_progress: Progress within current step (0.0 to 1.0)
            step_name: Optional name for the current step
        """
        now = time.time()

        self.current_step = step_number
        self.current_step_progress = max(0.0, min(1.0, step_progress))

        # Update step name if provided
        if step_name and len(self.step_names) > step_number:
            self.step_names[step_number] = step_name

        # Only print updates every 3 seconds to avoid spam
        if now - self.last_update >= 3:
            self._print_progress()
            self.last_update = now

    def _print_progress(self):
        """Print current progress with ETA using correct multi-step math."""
        elapsed = time.time() - self.start_time
        elapsed_str = str(timedelta(seconds=int(elapsed)))

        # Calculate overall progress: completed steps + current step progress
        overall_progress = (self.current_step + self.current_step_progress) / self.total_steps
        overall_progress_pct = overall_progress * 100

        if overall_progress > 0:
            # ETA calculation: elapsed_time / progress_fraction - elapsed_time
            estimated_total = elapsed / overall_progress
            eta_seconds = estimated_total - elapsed
            eta_str = str(timedelta(seconds=int(max(0, eta_seconds))))

            # Get current step name
            step_name = ""
            if self.step_names and self.current_step < len(self.step_names):
                step_name = f" ({self.step_names[self.current_step]})"

            print(
                f"Progress {self.operation_name}: {overall_progress_pct:.1f}% "
                f"[Step {self.current_step + 1}/{self.total_steps}{step_name}] | "
                f"Elapsed: {elapsed_str} | ETA: {eta_str}"
            )
        else:
            print(f"Progress {self.operation_name}: Starting... | Elapsed: {elapsed_str}")

    def complete_step(self, step_number):
        """Mark a step as complete (convenience method)."""
        self.update_step(step_number, 1.0)

    def complete(self, success=True):
        """Mark operation as complete and print final stats."""
        elapsed = time.time() - self.start_time
        elapsed_str = str(timedelta(seconds=int(elapsed)))
        status = "Completed" if success else "Failed"
        print(f"{status} {self.operation_name} in {elapsed_str}")


class FFmpegProgressTracker:
    """Track FFmpeg progress by parsing stderr output with improved performance."""

    def __init__(self, operation_name="FFmpeg Processing"):
        self.start_time = time.time()
        self.last_update = self.start_time
        self.operation_name = operation_name
        self.total_duration = None

    def parse_duration(self, line):
        """Parse total duration from FFmpeg output."""
        if "Duration:" in line and self.total_duration is None:
            try:
                # Extract duration in format HH:MM:SS.ss
                duration_str = line.split("Duration:")[1].split(",")[0].strip()
                time_parts = duration_str.split(":")
                hours = int(time_parts[0])
                minutes = int(time_parts[1])
                seconds = float(time_parts[2])
                self.total_duration = hours * 3600 + minutes * 60 + seconds
            except (IndexError, ValueError):
                pass

    def parse_progress(self, line):
        """Parse current progress from FFmpeg output."""
        if "time=" in line and self.total_duration:
            try:
                # Extract current time in format HH:MM:SS.ss
                time_str = line.split("time=")[1].split(" ")[0]
                time_parts = time_str.split(":")
                hours = int(time_parts[0])
                minutes = int(time_parts[1])
                seconds = float(time_parts[2])
                current_time = hours * 3600 + minutes * 60 + seconds

                now = time.time()
                if now - self.last_update >= 3:  # Update every 3 seconds
                    elapsed = now - self.start_time
                    elapsed_str = str(timedelta(seconds=int(elapsed)))

                    progress_pct = min(100, (current_time / self.total_duration) * 100)

                    if current_time > 0:
                        estimated_total = elapsed * (self.total_duration / current_time)
                        eta_seconds = estimated_total - elapsed
                        eta_str = str(timedelta(seconds=int(max(0, eta_seconds))))

                        print(
                            f"Progress {self.operation_name}: {progress_pct:.1f}% | "
                            f"Elapsed: {elapsed_str} | ETA: {eta_str}"
                        )

                    self.last_update = now

            except (IndexError, ValueError):
                pass

    def complete(self, success=True):
        """Mark operation as complete."""
        elapsed = time.time() - self.start_time
        elapsed_str = str(timedelta(seconds=int(elapsed)))
        status = "Completed" if success else "Failed"
        print(f"{status} {self.operation_name} in {elapsed_str}")


def optimize_ffmpeg_copy_performance():
    """
    Get optimized FFmpeg parameters for stream copy operations.

    Returns:
        dict: Optimized parameters for different scenarios
    """
    import psutil
    import os

    # Detect system capabilities
    cpu_count = os.cpu_count() or 4
    memory_gb = psutil.virtual_memory().total // (1024**3)

    # Base optimizations for stream copy
    base_params = [
        "-avoid_negative_ts",
        "make_zero",  # Handle timestamp issues
        "-fflags",
        "+genpts",  # Generate presentation timestamps
        "-max_muxing_queue_size",
        "9999",  # Prevent queue overflow
    ]

    # Memory and buffer optimizations
    if memory_gb >= 8:
        # High memory system - use larger buffers
        buffer_size = "32M"
        probesize = "100M"
        analyzeduration = "200M"
    elif memory_gb >= 4:
        # Medium memory system
        buffer_size = "16M"
        probesize = "50M"
        analyzeduration = "100M"
    else:
        # Low memory system
        buffer_size = "8M"
        probesize = "25M"
        analyzeduration = "50M"

    buffer_params = [
        "-probesize",
        probesize,
        "-analyzeduration",
        analyzeduration,
        "-bufsize",
        buffer_size,
    ]

    # Threading optimizations for copy operations
    # Use fewer threads for copy operations to avoid overhead
    thread_params = [
        "-threads",
        str(min(4, cpu_count)),  # Limit threads for copy operations
    ]

    return {
        "base": base_params,
        "buffer": buffer_params,
        "threading": thread_params,
        "all": base_params + buffer_params + thread_params,
        "concat_specific": [
            "-f",
            "concat",
            "-safe",
            "0",
            "-protocol_whitelist",
            "file,pipe",
        ]
        + base_params
        + buffer_params,
    }
