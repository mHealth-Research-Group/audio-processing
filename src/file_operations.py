"""
File operation utilities for audio processing.
Handles input validation, output path generation, and file management.
"""

from pathlib import Path
from typing import Optional
from .utils import is_video_file, is_audio_file, generate_processed_filename, generate_compressed_filename


def validate_input_file(input_path: Path) -> tuple[bool, bool]:
    """
    Validate input file and return file type information.

    Args:
        input_path: Path to input file

    Returns:
        Tuple of (is_video, is_audio)

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If file format is unsupported
    """
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    is_video = is_video_file(input_path)
    is_audio = is_audio_file(input_path)

    if not (is_video or is_audio):
        raise ValueError(f"Unsupported file format for {input_path}")

    return is_video, is_audio


def generate_output_path(input_path: Path, output_arg: Optional[str], operation: str) -> Path:
    """
    Generate output path based on input and operation type.

    Args:
        input_path: Input file path
        output_arg: User-specified output path (optional)
        operation: Type of operation ('process', 'compress', etc.)

    Returns:
        Generated output path
    """
    if output_arg:
        return Path(output_arg)

    if operation == "process":
        filename = generate_processed_filename(input_path)
        return input_path.parent / filename
    elif operation == "compress":
        filename = generate_compressed_filename(input_path)
        return input_path.parent / filename
    else:
        # Default fallback
        is_video = is_video_file(input_path)
        suffix = input_path.suffix if is_video else ".mp3"
        return input_path.parent / f"{input_path.stem}_{operation}{suffix}"


def setup_temp_directory(base_path: Path) -> Path:
    """
    Create and return a temporary directory for processing.

    Args:
        base_path: Base path for creating temp directory

    Returns:
        Path to created temp directory
    """
    temp_dir = base_path / "tmp"
    temp_dir.mkdir(exist_ok=True)
    return temp_dir


def cleanup_temp_files(temp_paths: list):
    """
    Clean up temporary files and directories.

    Args:
        temp_paths: List of paths to clean up
    """
    for path in temp_paths:
        if isinstance(path, str):
            path = Path(path)

        try:
            if path.exists():
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    import shutil

                    shutil.rmtree(path)
        except Exception as e:
            print(f"Warning: Could not clean up {path}: {e}")
