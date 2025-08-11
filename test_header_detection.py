#!/usr/bin/env python3
"""
Quick test script to verify the improved header detection logic.
"""

import sys
from pathlib import Path

# Add the current directory to sys.path to import our functions
sys.path.insert(0, str(Path(__file__).parent))

from rename_from_csv import is_valid_timestamp_format, is_likely_header_row


def test_timestamp_validation():
    """Test the timestamp validation function."""
    print("Testing timestamp validation...")

    # Valid timestamps
    valid_timestamps = [
        "20240811143022",  # Normal timestamp
        "20251231235959",  # End of year
        "20240101000000",  # Start of year
    ]

    # Invalid timestamps
    invalid_timestamps = [
        "timestamp",  # Header text
        "20240811",  # Too short
        "2024081114302A",  # Contains letter
        "20240811143099",  # Invalid seconds
        "20241301143022",  # Invalid month
        "20240832143022",  # Invalid day
        "",  # Empty string
        "abcdefghijklmn",  # All letters, correct length
    ]

    for ts in valid_timestamps:
        result = is_valid_timestamp_format(ts)
        print(f"  '{ts}' -> {result} (expected True)")
        assert result, f"Expected True for {ts}"

    for ts in invalid_timestamps:
        result = is_valid_timestamp_format(ts)
        print(f"  '{ts}' -> {result} (expected False)")
        assert not result, f"Expected False for {ts}"

    print("✅ Timestamp validation tests passed!")


def test_header_detection():
    """Test the header detection function."""
    print("\nTesting header detection...")

    # Header rows
    header_rows = [
        ["timestamp", "filepath"],
        ["time", "filename"],
        ["datetime", "file"],
        ["date", "path"],
        ["Timestamp", "FilePath"],  # Case insensitive
        ["some_header", "another_header"],  # Invalid timestamp format
    ]

    # Data rows
    data_rows = [
        ["20240811143022", "data/video1.mp4"],
        ["20251231235959", "/path/to/file.mp4"],
        ["20240101000000", "file.avi"],
    ]

    for row in header_rows:
        result = is_likely_header_row(row)
        print(f"  {row} -> {result} (expected True)")
        assert result, f"Expected True for {row}"

    for row in data_rows:
        result = is_likely_header_row(row)
        print(f"  {row} -> {result} (expected False)")
        assert not result, f"Expected False for {row}"

    print("✅ Header detection tests passed!")


if __name__ == "__main__":
    test_timestamp_validation()
    test_header_detection()
    print("\n🎉 All tests passed! The improved header detection is working correctly.")
