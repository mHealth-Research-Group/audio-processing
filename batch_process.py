#!/usr/bin/env python3
"""
Batch process audio/video files to generate editable timeline files.
"""

import sys
from pathlib import Path
import argparse
from main import main as process_file, VIDEO_EXTENSIONS, AUDIO_EXTENSIONS


def find_media_files(directory):
    """Find all audio and video files in a directory."""
    media_files = []
    directory = Path(directory)

    for file_path in directory.iterdir():
        if file_path.is_file():
            if (
                file_path.suffix.lower() in VIDEO_EXTENSIONS
                or file_path.suffix.lower() in AUDIO_EXTENSIONS
            ):
                media_files.append(file_path)

    return sorted(media_files)


def main():
    parser = argparse.ArgumentParser(
        description="Batch process media files to generate timeline files"
    )
    parser.add_argument("directory", help="Directory containing audio/video files")
    parser.add_argument(
        "--min-duration-on",
        type=float,
        default=0.1,
        help="Minimum speech duration (default: 0.1)",
    )
    parser.add_argument(
        "--min-duration-off",
        type=float,
        default=0.1,
        help="Minimum silence duration (default: 0.1)",
    )

    args = parser.parse_args()

    # Find all media files
    media_files = find_media_files(args.directory)

    if not media_files:
        print(f"No media files found in {args.directory}")
        return 1

    print(f"Found {len(media_files)} media files:")
    for file_path in media_files:
        print(f"  - {file_path.name}")
    print()

    # Process each file to generate timeline
    for file_path in media_files:
        print(f"Processing {file_path.name}...")

        # Build command line arguments for main.py
        sys.argv = [
            "main.py",
            str(file_path),
            "--generate-timeline",
            "--speaker-analysis-only",
            f"--min-duration-on={args.min_duration_on}",
            f"--min-duration-off={args.min_duration_off}",
        ]

        try:
            process_file()
            print(f"✓ Timeline generated for {file_path.name}")
        except Exception as e:
            print(f"✗ Error processing {file_path.name}: {e}")

        print()

    print("✓ All files processed!")
    print("\nNext steps:")
    print("1. Edit the *_timeline.json files to remove unwanted conversation segments")
    print("2. Run apply_edits.py to process the video files with your edits")

    return 0


if __name__ == "__main__":
    exit(main())
