#!/usr/bin/env python3
"""
Rename files based on CSV data and set modified timestamp metadata.

CSV format: timestamp,filepath
Example:
20240811143022,data/video1.mp4
20240811144530,data/video2.mp4

This script will:
1. Rename files to YYYYMMDD_compressed format
2. Set the file's modified timestamp to match the original timestamp
"""

import argparse
import csv
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List


def is_valid_timestamp_format(timestamp_str: str) -> bool:
    """
    Check if a string matches the expected timestamp format YYYYMMDDHHMMSS.

    Args:
        timestamp_str: String to validate

    Returns:
        True if the string is a valid timestamp format, False otherwise
    """
    if not timestamp_str or len(timestamp_str) != 14:
        return False

    if not timestamp_str.isdigit():
        return False

    try:
        # Try to parse it as a valid datetime
        datetime.strptime(timestamp_str, "%Y%m%d%H%M%S")
        return True
    except ValueError:
        return False


def is_likely_header_row(row: list[str]) -> bool:
    """
    Determine if a CSV row is likely a header row.

    Args:
        row: List of strings representing a CSV row

    Returns:
        True if the row appears to be a header, False otherwise
    """
    if len(row) < 2:
        return False

    first_col = row[0].strip().lower()
    second_col = row[1].strip().lower()

    # If first column matches timestamp-like headers and second matches file-like headers
    if first_col in {"timestamp", "time", "datetime", "date"} and second_col in {
        "filepath",
        "filename",
        "file",
        "path",
    }:
        return True

    # If first column is not a valid timestamp format, likely a header
    if not is_valid_timestamp_format(row[0].strip()):
        return True

    return False


def parse_timestamp(timestamp_str: str) -> datetime:
    """
    Parse timestamp from YYYYMMDDHHMMSS format.

    Args:
        timestamp_str: Timestamp string in YYYYMMDDHHMMSS format

    Returns:
        datetime object

    Raises:
        ValueError: If timestamp format is invalid
    """
    try:
        return datetime.strptime(timestamp_str, "%Y%m%d%H%M%S")
    except ValueError:
        raise ValueError(f"Invalid timestamp format '{timestamp_str}'. Expected format: YYYYMMDDHHMMSS")


def generate_new_filename(timestamp_str: str, original_path: Path) -> str:
    """
    Generate new filename in YYYYMMDD_compressed format.

    Args:
        timestamp_str: Original timestamp string
        original_path: Path to original file

    Returns:
        New filename string
    """
    dt = parse_timestamp(timestamp_str)
    day_str = dt.strftime("%Y%m%d")
    return f"{day_str}_compressed{original_path.suffix}"


def read_csv_data(csv_path: Path) -> List[tuple[str, str]]:
    """
    Read CSV file and return list of (timestamp, filename) tuples.

    Args:
        csv_path: Path to CSV file

    Returns:
        List of (timestamp, filename) tuples

    Raises:
        FileNotFoundError: If CSV file doesn't exist
        ValueError: If CSV format is invalid
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    data = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)

        # Skip header if present
        first_row = next(reader, None)
        if first_row is None:
            raise ValueError("CSV file is empty")

        # Check if first row is header using robust detection
        if is_likely_header_row(first_row):
            pass  # Skip header row
        else:
            # First row is data, process it
            if len(first_row) != 2:
                raise ValueError(f"CSV must have exactly 2 columns, got {len(first_row)}")
            data.append((first_row[0].strip(), first_row[1].strip()))

        # Process remaining rows
        for row_num, row in enumerate(reader, start=2):
            if len(row) != 2:
                raise ValueError(f"Row {row_num}: CSV must have exactly 2 columns, got {len(row)}")

            timestamp, filename = row[0].strip(), row[1].strip()
            if not timestamp or not filename:
                raise ValueError(f"Row {row_num}: Empty timestamp or filename")

            data.append((timestamp, filename))

    if not data:
        raise ValueError("No data found in CSV file")

    return data


def set_file_timestamp(file_path: Path, timestamp: datetime) -> None:
    """
    Set file's modified and access timestamp.

    Args:
        file_path: Path to file
        timestamp: Timestamp to set
    """
    # Convert datetime to Unix timestamp
    unix_timestamp = timestamp.timestamp()

    # Set both access and modified time
    os.utime(file_path, (unix_timestamp, unix_timestamp))


def rename_and_set_timestamp(file_path_str: str, timestamp_str: str, dry_run: bool = False) -> bool:
    """
    Rename file and set its timestamp metadata.

    Args:
        file_path_str: Full path to the file (from CSV)
        timestamp_str: Timestamp string
        dry_run: If True, only print what would be done

    Returns:
        True if successful, False otherwise
    """
    original_path = Path(file_path_str)

    if not original_path.exists():
        print(f"ERROR: File not found: {original_path}")
        return False

    try:
        # Parse timestamp
        dt = parse_timestamp(timestamp_str)

        # Generate new filename
        new_filename = generate_new_filename(timestamp_str, original_path)
        new_path = original_path.parent / new_filename

        if new_path.exists() and new_path != original_path:
            print(f"ERROR: Target file already exists: {new_path}")
            return False

        if dry_run:
            print(f"DRY RUN: Would rename: {original_path} -> {new_path}")
            print(f"DRY RUN: Would set timestamp: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            # Rename file
            if original_path != new_path:
                original_path.rename(new_path)
                print(f"SUCCESS: Renamed: {original_path} -> {new_path}")

            # Set timestamp metadata
            set_file_timestamp(new_path, dt)
            print(f"SUCCESS: Set timestamp: {dt.strftime('%Y-%m-%d %H:%M:%S')}")

        return True

    except Exception as e:
        print(f"ERROR: Error processing {file_path_str}: {e}")
        return False


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Rename files based on CSV data and set modified timestamp metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
CSV Format:
  The CSV file should contain two columns: timestamp,filepath
  Example:
    timestamp,filepath
    20240811143022,data/video1.mp4
    20240811144530,data/video2.mp4

Operation:
  - Renames files to YYYYMMDD_compressed format (e.g., 20240811_compressed.mp4)
  - Sets file modified timestamp to match the original timestamp
        """,
    )

    parser.add_argument("csv_file", type=Path, help="Path to CSV file with timestamp,filepath data")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # Validate arguments
    if not args.csv_file.exists():
        print(f"ERROR: CSV file not found: {args.csv_file}", file=sys.stderr)
        return 1

    try:
        # Read CSV data
        print(f"Reading CSV: {args.csv_file}")
        csv_data = read_csv_data(args.csv_file)
        print(f"Found {len(csv_data)} entries")

        if args.dry_run:
            print("\nDRY RUN MODE - No changes will be made\n")

        # Process each file
        success_count = 0
        for timestamp_str, filepath in csv_data:
            if args.verbose:
                print(f"\nProcessing: {filepath}")

            success = rename_and_set_timestamp(filepath, timestamp_str, args.dry_run)

            if success:
                success_count += 1

        # Summary
        print(f"\nSummary: {success_count}/{len(csv_data)} files processed successfully")

        if args.dry_run:
            print("Run without --dry-run to apply changes")

        return 0 if success_count == len(csv_data) else 1

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
