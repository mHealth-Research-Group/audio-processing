#!/usr/bin/env python3
"""
Script to duplicate videos with proper consecutive timestamps.
Creates approximately 400 videos totaling ~16 hours with proper naming.
"""

import shutil
from pathlib import Path
from datetime import datetime, timedelta


def parse_timestamp(filename):
    """Parse timestamp from filename like 20250707062146_000001.MP4"""
    parts = filename.split("_")
    if len(parts) >= 2 and len(parts[0]) == 14:  # YYYYMMDDHHMMSS
        timestamp_str = parts[0]
        number_str = parts[1].split(".")[0]  # Remove .MP4

        year = int(timestamp_str[0:4])
        month = int(timestamp_str[4:6])
        day = int(timestamp_str[6:8])
        hour = int(timestamp_str[8:10])
        minute = int(timestamp_str[10:12])
        second = int(timestamp_str[12:14])

        dt = datetime(year, month, day, hour, minute, second)
        number = int(number_str)

        return dt, number
    return None, None


def generate_timestamp_filename(dt, number):
    """Generate filename from datetime and number"""
    timestamp_str = dt.strftime("%Y%m%d%H%M%S")
    return f"{timestamp_str}_{number:06d}.MP4"


def duplicate_videos():
    source_dir = Path("data/test_stephen")

    # Get original files with timestamps
    original_files = []
    for f in source_dir.glob("*.MP4"):
        if f.name.startswith("202507070") and "_000" in f.name:
            dt, num = parse_timestamp(f.name)
            if dt is not None:
                original_files.append((f, dt, num))

    original_files.sort(key=lambda x: x[1])  # Sort by datetime
    print(f"Found {len(original_files)} original video files")

    if not original_files:
        print("No valid timestamped files found!")
        return

    # Calculate how many copies we need for ~16 hours
    target_duration = 16 * 3600  # 16 hours in seconds
    file_duration = 180  # 3 minutes per file
    target_files = target_duration // file_duration  # 320 files needed

    copies_needed = target_files // len(original_files)  # ~13 copies of each
    print(f"Need {copies_needed} copies of each file to reach ~{target_files} total files")

    # Get the last timestamp to continue from
    last_dt = original_files[-1][1]
    last_number = original_files[-1][2]

    # Start duplicating with consecutive timestamps
    current_dt = last_dt + timedelta(minutes=3)  # Next 3-minute slot
    current_number = last_number + 1

    total_created = 0

    for copy_round in range(copies_needed):
        for original_file, orig_dt, orig_num in original_files:
            if total_created >= target_files - len(original_files):
                break

            # Generate new filename with consecutive timestamp
            new_filename = generate_timestamp_filename(current_dt, current_number)
            new_path = source_dir / new_filename

            print(f"Copying {original_file.name} -> {new_filename}")
            shutil.copy2(original_file, new_path)

            # Increment for next file
            current_dt += timedelta(minutes=3)
            current_number += 1
            total_created += 1

            if total_created % 50 == 0:
                print(f"Progress: {total_created} files created")

        if total_created >= target_files - len(original_files):
            break

    total_files = len(original_files) + total_created
    total_hours = total_files * file_duration / 3600

    print("Duplication complete!")
    print(f"Created {total_created} duplicate files")
    print(f"Total files: {total_files}")
    print(f"Total duration: {total_hours:.2f} hours")


if __name__ == "__main__":
    duplicate_videos()
