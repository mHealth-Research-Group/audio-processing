#!/usr/bin/env python3
"""
Apply timeline edits to video files to zero out selected conversation segments.
"""

import json
import sys
from pathlib import Path
import argparse
from main import VIDEO_EXTENSIONS, AUDIO_EXTENSIONS


def load_timeline(timeline_path):
    """Load timeline from JSON file."""
    with open(timeline_path, "r") as f:
        return json.load(f)


def mmss_to_seconds(mmss_str):
    """Convert MM:SS.sss format to seconds."""
    parts = mmss_str.split(":")
    minutes = int(parts[0])
    seconds = float(parts[1])
    return minutes * 60 + seconds


def extract_conversation_segments(timeline_data):
    """Extract conversation segments that should be zeroed out."""
    segments = []

    for segment in timeline_data["timeline"]:
        # Zero out segments labeled as 'conversation' (multiple speakers)
        if segment.get("label") == "conversation":
            start_seconds = mmss_to_seconds(segment["start"])
            end_seconds = mmss_to_seconds(segment["end"])
            segments.append((start_seconds, end_seconds))

    return segments


def find_timeline_files(directory):
    """Find all timeline JSON files in a directory."""
    timeline_files = []
    directory = Path(directory)

    for file_path in directory.iterdir():
        if file_path.is_file() and file_path.name.endswith("_timeline.json"):
            timeline_files.append(file_path)

    return sorted(timeline_files)


def find_media_file_for_timeline(timeline_path):
    """Find the corresponding media file for a timeline file."""
    # Remove '_timeline.json' suffix to get base name
    base_name = timeline_path.stem.replace("_timeline", "")
    directory = timeline_path.parent

    # Look for video files first, then audio files
    for ext in VIDEO_EXTENSIONS:
        media_path = directory / f"{base_name}{ext}"
        if media_path.exists():
            return media_path

    for ext in AUDIO_EXTENSIONS:
        media_path = directory / f"{base_name}{ext}"
        if media_path.exists():
            return media_path

    return None


def main():
    parser = argparse.ArgumentParser(description="Apply timeline edits to media files")
    parser.add_argument("directory", help="Directory containing timeline JSON files and media files")
    parser.add_argument(
        "--output-suffix",
        default="_edited",
        help="Suffix for output files (default: _edited)",
    )

    args = parser.parse_args()

    # Find all timeline files
    timeline_files = find_timeline_files(args.directory)

    if not timeline_files:
        print(f"No timeline files (*_timeline.json) found in {args.directory}")
        return 1

    print(f"Found {len(timeline_files)} timeline files:")
    for file_path in timeline_files:
        print(f"  - {file_path.name}")
    print()

    # Process each timeline file
    for timeline_path in timeline_files:
        print(f"Processing {timeline_path.name}...")

        # Find corresponding media file
        media_path = find_media_file_for_timeline(timeline_path)
        if not media_path:
            print(f"✗ No media file found for {timeline_path.name}")
            continue

        print(f"  Found media file: {media_path.name}")

        # Load timeline data
        try:
            timeline_data = load_timeline(timeline_path)
        except Exception as e:
            print(f"✗ Error loading timeline {timeline_path.name}: {e}")
            continue

        # Extract conversation segments to zero out
        conversation_segments = extract_conversation_segments(timeline_data)
        print(f"  Found {len(conversation_segments)} conversation segments to zero out")

        if not conversation_segments:
            print("  No conversation segments found, skipping...")
            continue

        # Create output path
        output_path = media_path.parent / f"{media_path.stem}{args.output_suffix}{media_path.suffix}"

        # Create a temporary file with the segments to process
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as temp_file:
            for start, end in conversation_segments:
                temp_file.write(f"{start} {end}\n")
            temp_segments_file = temp_file.name

        # Build command line arguments for main.py
        sys.argv = ["main.py", str(media_path), "-o", str(output_path)]

        # We need to modify main.py to accept pre-defined segments
        # For now, let's create a simpler approach using the existing functionality

        try:
            # Import the necessary functions directly
            from main import (
                load_model,
                process_video_with_ffmpeg,
                process_audio_with_ffmpeg,
                is_video_file,
            )

            print("  Loading model...")
            _ = load_model()

            print("  Processing media file...")
            if is_video_file(media_path):
                process_video_with_ffmpeg(media_path, output_path, conversation_segments)
            else:
                process_audio_with_ffmpeg(media_path, output_path, conversation_segments)

            print(f"✓ Created edited file: {output_path.name}")

        except Exception as e:
            print(f"✗ Error processing {media_path.name}: {e}")

        # Clean up temp file
        import os

        if os.path.exists(temp_segments_file):
            os.unlink(temp_segments_file)

        print()

    print("✓ All files processed!")

    return 0


if __name__ == "__main__":
    exit(main())
