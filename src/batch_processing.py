"""
Batch Processing Module for Large Video Datasets

This module implements automatic batching to solve the FFmpeg filter explosion issue
that causes exponential slowdown with large video counts (>60 videos).

Performance Issue:
- Small datasets (< 60 videos): ~90 filters → 1-3s per video (fast)
- Large datasets (> 100 videos): 1000+ filters → 30-100s per video (slow)

Solution:
- Split large datasets into batches of ~50 videos each
- Process each batch independently (fast)
- Merge processed results (also fast, just concatenation)
"""

import argparse
import logging
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from .utils import (
    is_video_file,
    extract_timestamp_from_filename,
    save_yaml,
    run_subprocess_with_encoding,
)

logger = logging.getLogger(__name__)

# Default batch settings (can be overridden by CLI args)
DEFAULT_BATCH_SIZE = 50
DEFAULT_BATCH_THRESHOLD = 60  # Start batching when > 60 videos


class BatchProcessor:
    """Handles automatic batching for large video datasets."""

    def __init__(self, input_dir: Path, output_path: Path, args):
        self.input_dir = input_dir
        self.output_path = output_path
        self.args = args
        self.batch_dir = input_dir / "batches"
        self.processed_batches = []

        # Use CLI arguments or defaults
        self.batch_size = getattr(args, "batch_size", DEFAULT_BATCH_SIZE)
        self.batch_threshold = DEFAULT_BATCH_THRESHOLD

    def should_use_batching(self, video_files: List[Path]) -> bool:
        """Determine if batching should be used based on video count."""
        # Respect --no-batch flag
        if getattr(self.args, "no_batch", False):
            return False
        return len(video_files) > self.batch_threshold

    def create_batches(self, video_files: List[Path]) -> List[List[Path]]:
        """Split video files into optimal-sized batches."""
        # Sort by timestamp to maintain chronological order
        sorted_videos = sorted(video_files, key=lambda f: extract_timestamp_from_filename(f.name) or f.name)

        batches = []
        for i in range(0, len(sorted_videos), self.batch_size):
            batch = sorted_videos[i : i + self.batch_size]
            batches.append(batch)

        logger.info(f"Split {len(video_files)} videos into {len(batches)} batches of ~{self.batch_size} videos each")
        return batches

    def create_batch_directory(self, batch_videos: List[Path], batch_num: int) -> Path:
        """Create a temporary directory with symlinks for a batch."""
        batch_dir = self.batch_dir / f"batch_{batch_num:03d}"
        batch_dir.mkdir(parents=True, exist_ok=True)

        # Clean any existing files
        for file in batch_dir.iterdir():
            if file.is_symlink() or file.is_file():
                file.unlink()
            elif file.is_dir():
                shutil.rmtree(file)

        # Windows compatibility: Always use copying on Windows for reliability
        import platform

        use_copy_fallback = platform.system() == "Windows"

        if use_copy_fallback:
            logger.info(f"Windows detected - using file copying for batch {batch_num}")

        # Create symlinks or copy files to batch directory
        for i, video_file in enumerate(batch_videos):
            target_path = batch_dir / video_file.name

            if use_copy_fallback:
                try:
                    # Use copy2 to preserve metadata including timestamps
                    shutil.copy2(video_file, target_path)
                    if i == 0:  # Log only for first file to avoid spam
                        logger.debug(f"Copying {video_file.name} to batch directory")
                except Exception as e:
                    logger.error(f"Failed to copy {video_file.name}: {e}")
                    raise
            else:
                try:
                    target_path.symlink_to(video_file.absolute())
                    if i == 0:  # Log only for first file
                        logger.debug(f"Creating symlink for {video_file.name}")
                except (OSError, NotImplementedError, PermissionError) as exc:
                    # Fallback to copying for all remaining files
                    use_copy_fallback = True
                    logger.warning(f"Symlink creation failed ({exc}). Falling back to copying files.")

                    # Copy this file and continue with copying for the rest
                    try:
                        shutil.copy2(video_file, target_path)
                    except Exception as e:
                        logger.error(f"Failed to copy {video_file.name} after symlink failure: {e}")
                        raise

        # Verify batch directory has the expected files
        created_files = list(batch_dir.glob("*"))
        if len(created_files) != len(batch_videos):
            logger.error(f"Batch creation failed: expected {len(batch_videos)} files, got {len(created_files)}")
            logger.error(f"Expected: {[v.name for v in batch_videos]}")
            logger.error(f"Created: {[f.name for f in created_files]}")
            raise RuntimeError(f"Batch directory creation failed for batch {batch_num}")

        logger.info(f"Created batch {batch_num} with {len(created_files)} videos")
        return batch_dir

    def process_single_batch(self, batch_dir: Path, batch_num: int) -> Optional[Path]:
        """Process a single batch and return the output file path."""
        logger.info(f"\n{'=' * 60}")
        logger.info(f"PROCESSING BATCH {batch_num}")
        logger.info(f"{'=' * 60}")

        # Create batch-specific args
        batch_args = argparse.Namespace(**vars(self.args))
        batch_args.input_path = str(batch_dir)

        # Set batch output path
        batch_output = self.batch_dir / f"batch_{batch_num:03d}_processed.mp4"
        batch_args.output = str(batch_output)

        # Set batch timeline output
        if hasattr(self.args, "generate_timeline") and self.args.generate_timeline:
            batch_timeline = self.batch_dir / f"batch_{batch_num:03d}_timeline.yaml"
            batch_args.timeline_output = str(batch_timeline)

        try:
            # Process the batch using direct video processing logic
            # This avoids circular imports by calling the core logic directly
            result = self._process_batch_videos(batch_dir, batch_output, batch_args)

            if result == 0 and batch_output.exists():
                self.processed_batches.append(batch_output)
                logger.info(f"Batch {batch_num} completed: {batch_output}")
                return batch_output
            else:
                logger.error(f"Batch {batch_num} failed")
                return None

        except Exception as e:
            logger.error(f"Batch {batch_num} failed with error: {e}")
            return None

    def _process_batch_videos(self, batch_dir: Path, batch_output: Path, batch_args) -> int:
        """Process videos in a batch directory using the core logic."""
        try:
            # Get video files in batch with detailed logging
            video_files = []
            all_files = list(batch_dir.iterdir())

            logger.debug(f"Scanning batch directory {batch_dir}")
            logger.debug(f"Found {len(all_files)} total files: {[f.name for f in all_files]}")

            for file in all_files:
                if file.is_file():
                    logger.debug(f"Checking file: {file.name}")
                    if is_video_file(file):
                        video_files.append(file)
                        logger.debug(f"Video file: {file.name}")
                    else:
                        logger.debug(f"Not a video file: {file.name}")
                else:
                    logger.debug(f"Not a regular file: {file.name}")

            if not video_files:
                logger.error(f"No video files found in batch directory: {batch_dir}")
                logger.error(f"Total files in directory: {len(all_files)}")
                logger.error(f"Files found: {[f.name for f in all_files]}")
                return 1

            logger.info(f"Found {len(video_files)} video files in batch: {[f.name for f in video_files]}")

            # Import merge functions
            from .merge_operations import (
                detect_timestamped_videos,
                setup_merge_operation,
                perform_video_merge,
                should_process_after_merge,
                create_processed_video_args,
            )

            # Fast-path: single video batches do not require merging
            if len(video_files) == 1:
                single_file = video_files[0]
                timestamp = extract_timestamp_from_filename(single_file.name)

                if not timestamp:
                    logger.error("Single-video batch missing timestamped filename")
                    return 1

                logger.info(
                    "Single video batch detected (%s); skipping merge and processing directly",
                    single_file.name,
                )

                if should_process_after_merge(batch_args):
                    from .commands import process_single_file

                    file_args = argparse.Namespace(**vars(batch_args))
                    file_args.input_path = str(single_file)
                    file_args.output = str(batch_output)

                    if hasattr(batch_args, "generate_timeline") and batch_args.generate_timeline:
                        timeline_path = batch_output.parent / f"{batch_output.stem}_timeline.yaml"
                        file_args.timeline_output = str(timeline_path)

                    result = process_single_file(file_args, gap_info=None)
                    return result

                try:
                    shutil.copy2(single_file, batch_output)
                    logger.info("Copied single batch video to %s", batch_output)
                    return 0
                except Exception as exc:
                    logger.error(f"Failed to copy single batch video: {exc}")
                    return 1

            # Check if we have timestamped videos with detailed logging
            has_timestamped_videos, timestamped_count = detect_timestamped_videos(video_files)

            logger.info(f"Timestamp detection: {timestamped_count}/{len(video_files)} files have timestamps")

            if not has_timestamped_videos:
                logger.error("Batch does not contain timestamped videos")
                logger.error("Files in batch:")
                for vf in video_files:
                    timestamp = extract_timestamp_from_filename(vf.name)
                    logger.error(f"  - {vf.name} → timestamp: {timestamp}")
                return 1

            logger.info(f"Batch contains {timestamped_count} timestamped videos")

            # Set up merge operation
            output_dir = batch_output.parent
            merged_output, should_skip = setup_merge_operation(batch_args, video_files, output_dir)
            if should_skip:
                return 0

            # Override merged output to use our batch output path
            merged_output = batch_output.parent / f"{batch_output.stem}_merged.mp4"

            # Perform video merge
            gap_info = perform_video_merge(batch_dir, merged_output, batch_args)

            # Process merged video if needed
            if should_process_after_merge(batch_args):
                from .commands import process_single_file

                # Create processed video args
                file_args = create_processed_video_args(batch_args, merged_output)
                file_args.output = str(batch_output)  # Use our batch output path

                # Set timeline output
                if hasattr(batch_args, "generate_timeline") and batch_args.generate_timeline:
                    timeline_path = batch_output.parent / f"{batch_output.stem}_timeline.yaml"
                    file_args.timeline_output = str(timeline_path)

                # Process the merged video
                result = process_single_file(file_args, gap_info=gap_info)
                return result

            # If no further processing needed, just rename merged to final output
            if merged_output.exists():
                shutil.move(merged_output, batch_output)
                return 0

            return 1

        except Exception as e:
            logger.error(f"Error processing batch videos: {e}")
            return 1

    def merge_processed_batches(self) -> bool:
        """Merge all processed batches into final output."""
        if not self.processed_batches:
            logger.error("No processed batches to merge")
            return False

        logger.info(f"\n{'=' * 60}")
        logger.info(f"MERGING {len(self.processed_batches)} PROCESSED BATCHES")
        logger.info(f"{'=' * 60}")

        # Create concat list for final merge
        concat_list = self.batch_dir / "final_merge_list.txt"

        with open(concat_list, "w") as f:
            for batch_output in sorted(self.processed_batches):
                f.write(f"file '{batch_output.absolute()}'\n")

        # Merge using FFmpeg concat (fast - just stream copy)
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",  # Stream copy - no re-encoding!
            str(self.output_path),
        ]

        try:
            logger.info("Starting final merge (stream copy, no re-encoding)...")
            logger.info(f"Output: {self.output_path}")

            run_subprocess_with_encoding(cmd, check=True)

            if self.output_path.exists():
                logger.info(f"Final merge completed: {self.output_path}")

                # Create final timeline if requested
                if hasattr(self.args, "generate_timeline") and self.args.generate_timeline:
                    self._merge_timelines()

                return True
            else:
                logger.error("Final merge failed - output file not created")
                return False

        except subprocess.CalledProcessError as e:
            logger.error(f"Final merge failed: {e}")
            return False

    def _merge_timelines(self):
        """Merge timeline files from all batches."""
        try:
            timeline_files = list(self.batch_dir.glob("batch_*_timeline.yaml"))
            if not timeline_files:
                return

            # Load first timeline as base
            from .utils import load_timeline

            merged_timeline = {"timeline": [], "summary": {}}

            for timeline_file in sorted(timeline_files):
                batch_timeline = load_timeline(timeline_file)

                if "timeline" in batch_timeline:
                    # Adjust timestamps for this batch
                    for segment in batch_timeline["timeline"]:
                        # Add current offset to start/end times
                        # This would need more sophisticated time handling
                        merged_timeline["timeline"].append(segment)

            # Save merged timeline
            final_timeline_path = self.output_path.parent / f"{self.output_path.stem}_timeline.yaml"
            save_yaml(merged_timeline, final_timeline_path)
            logger.info(f"📄 Merged timeline saved: {final_timeline_path}")

        except Exception as e:
            logger.warning(f"Failed to merge timelines: {e}")

    def cleanup_batches(self):
        """Clean up batch directories and intermediate files."""
        try:
            if self.batch_dir.exists():
                shutil.rmtree(self.batch_dir)
                logger.info("Cleaned up batch directories")
        except Exception as e:
            logger.warning(f"Failed to clean up batches: {e}")

    def process_with_batching(self, video_files: List[Path]) -> int:
        """Main entry point for batch processing."""
        try:
            logger.info("STARTING BATCH PROCESSING")
            logger.info(f"Input: {len(video_files)} videos")
            logger.info(f"Batch size: {self.batch_size} videos")
            logger.info(f"Output: {self.output_path}")

            # Create batches
            batches = self.create_batches(video_files)

            # Process each batch
            for batch_num, batch_videos in enumerate(batches, 1):
                # Create batch directory with symlinks
                batch_dir = self.create_batch_directory(batch_videos, batch_num)

                try:
                    # Process this batch
                    result = self.process_single_batch(batch_dir, batch_num)

                    if result is None:
                        logger.error(f"Batch {batch_num} failed - aborting")
                        return 1

                except Exception as e:
                    logger.error(f"Batch {batch_num} failed with error: {e}")
                    return 1

            # Merge all processed batches
            if self.merge_processed_batches():
                logger.info("BATCH PROCESSING COMPLETED SUCCESSFULLY!")
                logger.info(f"Final output: {self.output_path}")

                # Clean up intermediate files
                if not getattr(self.args, "keep_batches", False):
                    self.cleanup_batches()
                else:
                    logger.info(f"Keeping batch files in {self.batch_dir} (--keep-batches)")

                return 0
            else:
                logger.error("Final merge failed")
                return 1

        except Exception as e:
            logger.error(f"Batch processing failed: {e}")
            return 1


def should_use_batch_processing(video_files: List[Path]) -> bool:
    """Global function to check if batch processing should be used."""
    return len(video_files) > DEFAULT_BATCH_THRESHOLD


def process_with_automatic_batching(input_dir: Path, output_path: Path, video_files: List[Path], args) -> int:
    """
    Automatically use batch processing for large datasets.

    This is the main entry point that should be called from commands.py
    when a large video dataset is detected.
    """
    processor = BatchProcessor(input_dir, output_path, args)
    return processor.process_with_batching(video_files)
