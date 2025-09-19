import argparse
import sys
from pathlib import Path
from dotenv import load_dotenv
from .commands import (
    process_directory,
    process_single_file,
    apply_blank_command,
    compress_command,
)
from .utils import ensure_utf8_encoding

load_dotenv()


def add_process_arguments(parser):
    """Add arguments for the 'process' command to the parser."""
    parser.add_argument("input_path", help="Path to input audio/video file or directory")
    parser.add_argument(
        "-o",
        "--output",
        help="Path for output file or directory.",
    )

    # Convenience flag for complete processing
    parser.add_argument(
        "--complete",
        action="store_true",
        help="Enable complete processing (equivalent to --merge-videos --generate-timeline --analyze-speakers)",
    )

    # Batch processing control
    parser.add_argument(
        "--no-batch",
        action="store_true",
        help="Disable automatic batch processing for large datasets (>60 videos)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of videos per batch (default: 50)",
    )
    parser.add_argument(
        "--keep-batches",
        action="store_true",
        help="Keep intermediate batch files for debugging",
    )

    parser.add_argument(
        "--min-duration-on",
        type=float,
        default=0.05,
        help="Minimum duration for speech regions in seconds.",
    )
    parser.add_argument(
        "--min-duration-off",
        type=float,
        default=0.2,
        help="Minimum duration for non-speech regions in seconds.",
    )
    parser.add_argument(
        "--analyze-speakers",
        action="store_true",
        help="Analyze and report if multiple speakers are detected.",
    )
    parser.add_argument(
        "--detailed-analysis",
        action="store_true",
        help="Use advanced speaker analysis (slower but more accurate).",
    )
    parser.add_argument(
        "--speaker-analysis-only",
        action="store_true",
        help="Only perform speaker analysis without generating output files.",
    )
    parser.add_argument(
        "--generate-timeline",
        action="store_true",
        help="Generate a timeline YAML file with detected segments.",
    )
    parser.add_argument(
        "--timeline-output",
        help="Custom path for timeline output file.",
    )

    # Video merging options
    parser.add_argument(
        "--merge-videos",
        action="store_true",
        help="Enable video merging for timestamped videos in directory.",
    )
    parser.add_argument(
        "--merge-only",
        action="store_true",
        help="Only merge videos without additional speech processing.",
    )
    parser.add_argument(
        "--force-overwrite",
        "-f",
        action="store_true",
        help="Overwrite existing merged videos without prompting.",
    )
    parser.add_argument(
        "--min-gap-threshold",
        type=float,
        default=2,
        help="Minimum gap duration (seconds) to fill with black frames when merging videos.",
    )
    parser.add_argument(
        "--max-gap-threshold",
        type=float,
        default=None,
        help="Maximum gap duration (seconds) to fill with black frames when merging videos.",
    )
    parser.add_argument(
        "--blank-video",
        default="blank_muted.MP4",
        help="Path to blank video file to use for gap filling (default: blank_muted.MP4).",
    )


def main():
    """Main function to handle command-line arguments and execute commands."""
    ensure_utf8_encoding()
    try:
        parser = argparse.ArgumentParser(description="Process and edit audio/video files based on speaker analysis.")
        subparsers = parser.add_subparsers(dest="mode", help="Processing mode")

        process_parser = subparsers.add_parser("process", help="Process a file or directory")
        add_process_arguments(process_parser)

        blank_parser = subparsers.add_parser("apply-blank", help="Apply timeline edits: blank video, mute audio, etc.")
        blank_parser.add_argument("input_video", help="Path to input video file")
        blank_parser.add_argument("timeline", help="Path to timeline YAML file")
        blank_parser.add_argument(
            "--blank-video", default="blank_muted.MP4", help="Path to blank video file (default: blank_muted.MP4)"
        )
        blank_parser.add_argument("-o", "--output", help="Path for output file")
        blank_parser.add_argument(
            "--no-trim-first-frame",
            action="store_true",
            help="Disable trimming of first frame for privacy preservation (default: trimming enabled)",
        )

        compress_parser = subparsers.add_parser(
            "compress", help="Compress video files to H.264 with smaller file sizes"
        )
        compress_parser.add_argument("input_path", help="Path to input video file or directory")
        compress_parser.add_argument("-o", "--output", help="Path for output file or directory")
        compress_parser.add_argument(
            "--quality",
            type=int,
            default=23,
            help="Video quality (CRF): lower = better quality, higher file size (default: 23)",
        )
        compress_parser.add_argument(
            "--preset",
            default="fast",
            choices=["ultrafast", "fast", "medium", "slow", "veryslow"],
            help="Encoding preset: ultrafast to veryslow (default: fast)",
        )
        compress_parser.add_argument(
            "--max-width", type=int, default=1280, help="Maximum width for output video, 0 = no scaling (default: 1280)"
        )

        args_list = sys.argv[1:]
        if not args_list or args_list[0] not in [
            "process",
            "apply-blank",
            "compress",
        ]:
            if args_list and (Path(args_list[0]).exists() or Path(args_list[0]).is_dir()):
                args_list.insert(0, "process")
            elif not args_list or "-h" in args_list or "--help" in args_list:
                pass
            else:
                args_list.insert(0, "process")

        args = parser.parse_args(args_list)

        # Handle --complete flag by setting individual flags
        if hasattr(args, "complete") and args.complete:
            args.merge_videos = True
            args.generate_timeline = True
            args.analyze_speakers = True
            print("Complete processing enabled: --merge-videos --generate-timeline --analyze-speakers")

        if args.mode == "process":
            if Path(args.input_path).is_dir():
                return process_directory(args)
            else:
                return process_single_file(args)
        elif args.mode == "apply-blank":
            return apply_blank_command(args)
        elif args.mode == "compress":
            return compress_command(args)
        else:
            parser.print_help()
            return 1

    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
