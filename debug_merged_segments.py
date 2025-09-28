#!/usr/bin/env python3
"""Debug script to examine the merged segments that are causing the issue"""

import yaml
from pathlib import Path

# Load the timeline to see what segments were identified as 'all'
timeline_path = Path("data/VIDEO-7-7-25/20250926_merged_processed_timeline_modified.yaml")

with open(timeline_path, "r") as f:
    data = yaml.safe_load(f)

timeline = data["timeline"]

# Find all segments marked as 'all'
all_segments = []
for segment in timeline:
    if segment.get("type") == "all":
        start = segment["start"]
        end = segment["end"]

        # Convert time to seconds for easier analysis
        def time_to_seconds(time_str):
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

        start_sec = time_to_seconds(start)
        end_sec = time_to_seconds(end)
        all_segments.append((start_sec, end_sec, start, end))

# Sort by start time
all_segments.sort()

print(f"Found {len(all_segments)} segments marked as 'all':")
print("Start(sec)\tEnd(sec)\tDuration\tGap to next")

for i, (start_sec, end_sec, start_str, end_str) in enumerate(all_segments):
    duration = end_sec - start_sec

    # Calculate gap to next segment
    gap_to_next = ""
    if i < len(all_segments) - 1:
        next_start = all_segments[i + 1][0]
        gap = next_start - end_sec
        gap_to_next = f"{gap:.1f}s"
        if gap > 3600:  # More than 1 hour
            gap_to_next += f" ({gap / 3600:.1f}h)"

    print(f"{start_sec:.1f}\t\t{end_sec:.1f}\t\t{duration:.1f}s\t\t{gap_to_next}")

print("\nLargest gaps between 'all' segments:")
gaps = []
for i in range(len(all_segments) - 1):
    end_current = all_segments[i][1]
    start_next = all_segments[i + 1][0]
    gap = start_next - end_current
    gaps.append((gap, end_current, start_next))

gaps.sort(reverse=True)
for gap, end_current, start_next in gaps[:5]:
    print(f"Gap: {gap:.1f}s ({gap / 3600:.1f}h) from {end_current:.1f} to {start_next:.1f}")
