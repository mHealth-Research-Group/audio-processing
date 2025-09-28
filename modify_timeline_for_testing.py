#!/usr/bin/env python3
"""
Script to create realistic edits to a timeline for testing incremental processing.
This simulates what a user might do when editing for privacy - blanking out certain segments.
"""

import yaml
import random


def modify_timeline_for_testing(input_file, output_file):
    """
    Make realistic edits to a timeline:
    1. Blank out some speech segments (privacy edits)
    2. Blank out some silence segments that might contain sensitive audio
    3. Leave most segments unchanged to test incremental processing
    """

    # Load the timeline
    with open(input_file, "r") as f:
        data = yaml.safe_load(f)

    timeline = data["timeline"]
    total_segments = len(timeline)

    print(f"Total segments: {total_segments}")

    # Set random seed for reproducible results
    random.seed(42)

    modifications_made = 0

    # Strategy: Make realistic privacy edits
    # 1. Blank out some speech segments (maybe they contain sensitive information)
    # 2. Blank out some silence segments that might have background conversations
    # 3. Focus edits in certain time ranges (simulate editing specific sections)

    # Define time ranges where we'll make more edits (simulate focused editing)
    focus_ranges = [
        (300, 600),  # 5-10 minutes
        (1800, 2100),  # 30-35 minutes
        (3600, 3900),  # 1 hour - 1h 5min
    ]

    for i, segment in enumerate(timeline):
        start_seconds = time_to_seconds(segment["start"])
        should_modify = False

        # Higher chance to modify segments in focus ranges
        in_focus_range = any(start <= start_seconds <= end for start, end in focus_ranges)

        if segment["type"] == "speech":
            # 0.5% chance to blank speech segments normally, 2% in focus ranges
            chance = 0.02 if in_focus_range else 0.005
            if random.random() < chance:
                should_modify = True
        elif segment["type"] == "silence":
            # 0.1% chance to blank silence segments normally, 0.5% in focus ranges
            chance = 0.005 if in_focus_range else 0.001
            if random.random() < chance:
                should_modify = True

        if should_modify:
            segment["type"] = "all"  # This will cause the segment to be blanked
            modifications_made += 1

        # Print progress every 1000 segments
        if (i + 1) % 1000 == 0:
            print(f"Processed {i + 1}/{total_segments} segments...")

    print(
        f"Made {modifications_made} modifications out of {total_segments} segments ({modifications_made / total_segments * 100:.2f}%)"
    )

    # Save the modified timeline
    with open(output_file, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    print(f"Modified timeline saved to: {output_file}")
    return modifications_made


def time_to_seconds(time_str):
    """Convert time string like '1:23.456' to seconds"""
    try:
        if ":" in time_str:
            parts = time_str.split(":")
            if len(parts) == 2:  # M:SS.mmm
                minutes = int(parts[0])
                seconds = float(parts[1])
                return minutes * 60 + seconds
            elif len(parts) == 3:  # H:MM:SS.mmm
                hours = int(parts[0])
                minutes = int(parts[1])
                seconds = float(parts[2])
                return hours * 3600 + minutes * 60 + seconds
        else:
            return float(time_str)
    except (ValueError, IndexError):
        return 0


if __name__ == "__main__":
    input_file = "data/VIDEO-7-7-25/20250926_merged_processed_timeline.yaml"
    output_file = "data/VIDEO-7-7-25/20250926_merged_processed_timeline_modified.yaml"

    modifications = modify_timeline_for_testing(input_file, output_file)
    print("\nTimeline modification complete!")
    print(f"Original: {input_file}")
    print(f"Modified: {output_file}")
    print(f"Total modifications: {modifications}")
