#!/usr/bin/env python3
"""
Script to duplicate videos in test_stephen directory with incremental filenames.
Creates videos to reach approximately 16 hours total duration.
"""

import shutil
import subprocess
import json
from pathlib import Path


def get_video_duration(video_path):
    """Get duration of video in seconds using ffprobe"""
    try:
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(video_path)]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except Exception as e:
        print(f"Error getting duration for {video_path}: {e}")
        return 0.0


def duplicate_videos():
    source_dir = Path("data/test_stephen")

    # Get list of original MP4 files with timestamp format (HHMMSS pattern)
    original_files = [
        f
        for f in source_dir.glob("*.MP4")
        if f.name.startswith("202507070") and "_000" in f.name and not f.name.endswith("_merged.mp4")
    ]
    original_files.sort()

    print(f"Found {len(original_files)} original video files")

    # Calculate total duration of original videos
    total_original_duration = 0
    file_durations = {}

    for file_path in original_files:
        duration = get_video_duration(file_path)
        file_durations[file_path] = duration
        total_original_duration += duration
        print(f"{file_path.name}: {duration:.1f} seconds")

    print(
        f"Total original duration: {total_original_duration:.1f} seconds ({total_original_duration / 3600:.2f} hours)"
    )

    # Target is 16 hours = 57600 seconds
    target_duration = 16 * 3600  # 16 hours in seconds
    duplication_factor = target_duration / total_original_duration

    print(f"Need to duplicate by factor of {duplication_factor:.2f} to reach 16 hours")
    print(f"Will create approximately {len(original_files) * int(duplication_factor)} videos")

    video_counter = 1
    total_copied_duration = 0

    # Check existing duplicates first
    existing_duplicates = list(source_dir.glob("20250707_*.MP4"))
    existing_count = len(existing_duplicates)
    existing_duration = existing_count * 180.0  # Each file is 180s
    total_copied_duration = existing_duration
    video_counter = existing_count + 1

    print(f"Found {existing_count} existing duplicates ({existing_duration / 3600:.2f} hours)")

    # Keep duplicating until we reach target duration
    while total_copied_duration < target_duration:
        for original_file in original_files:
            if total_copied_duration >= target_duration:
                break

            # Generate new incremental filename
            new_filename = f"20250707_{video_counter:06d}.MP4"
            new_path = source_dir / new_filename

            duration = file_durations[original_file]
            print(f"Copying {original_file.name} -> {new_filename} ({duration:.1f}s)")
            shutil.copy2(original_file, new_path)

            total_copied_duration += duration
            video_counter += 1

            if video_counter % 50 == 0:
                print(f"Progress: {video_counter - 1} videos, {total_copied_duration / 3600:.2f} hours")

    print("Duplication complete!")
    print(f"Created {video_counter - 1} total videos")
    print(f"Total duration: {total_copied_duration:.1f} seconds ({total_copied_duration / 3600:.2f} hours)")


if __name__ == "__main__":
    duplicate_videos()
