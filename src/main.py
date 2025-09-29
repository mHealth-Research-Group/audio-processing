from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Sequence

from dotenv import load_dotenv

from .apply_edits import handle_edit_command
from .commands import compress_command, process_directory, process_single_file
from .utils import ensure_utf8_encoding

load_dotenv()


SUPPORTED_COMMANDS = {"process", "compress", "edit"}
COMPLETE_FLAG_TARGETS = ("merge_videos", "generate_timeline", "analyze_speakers")


def _add_process_arguments(parser: argparse.ArgumentParser) -> None:
    """Attach process-specific CLI arguments to the parser."""
    parser.add_argument(
        "-i", "--input", dest="input_path", required=True, help="Path to input audio/video file or directory"
    )

    general_group = parser.add_argument_group("General")
    general_group.add_argument("-o", "--output", help="Path for output file or directory.")
    general_group.add_argument(
        "--complete",
        action="store_true",
        help="Enable complete processing (merge videos, generate timeline, analyze speakers).",
    )

    batch_group = parser.add_argument_group("Batch Processing")
    batch_group.add_argument(
        "--no-batch",
        action="store_true",
        help="Disable automatic batch processing for large datasets (>60 videos)",
    )
    batch_group.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of videos per batch (default: 50)",
    )
    batch_group.add_argument(
        "--keep-batches",
        action="store_true",
        help="Keep intermediate batch files for debugging",
    )

    analysis_group = parser.add_argument_group("Analysis")
    analysis_group.add_argument(
        "--min-duration-on",
        type=float,
        default=0.05,
        help="Minimum duration for speech regions in seconds.",
    )
    analysis_group.add_argument(
        "--min-duration-off",
        type=float,
        default=0.2,
        help="Minimum duration for non-speech regions in seconds.",
    )
    analysis_group.add_argument(
        "--analyze-speakers",
        action="store_true",
        help="Analyze and report if multiple speakers are detected.",
    )
    analysis_group.add_argument(
        "--detailed-analysis",
        action="store_true",
        help="Use advanced speaker analysis (slower but more accurate).",
    )
    analysis_group.add_argument(
        "--speaker-analysis-only",
        action="store_true",
        help="Only perform speaker analysis without generating output files.",
    )

    timeline_group = parser.add_argument_group("Timeline")
    timeline_group.add_argument(
        "--generate-timeline",
        action="store_true",
        help="Generate a timeline YAML file with detected segments.",
    )
    timeline_group.add_argument(
        "--timeline-output",
        help="Custom path for timeline output file.",
    )

    merge_group = parser.add_argument_group("Video Merging")
    merge_group.add_argument(
        "--merge-videos",
        action="store_true",
        help="Enable video merging for timestamped videos in directory.",
    )
    merge_group.add_argument(
        "--merge-only",
        action="store_true",
        help="Only merge videos without additional speech processing.",
    )
    merge_group.add_argument(
        "--force-overwrite",
        "-f",
        action="store_true",
        help="Overwrite existing merged videos without prompting.",
    )
    merge_group.add_argument(
        "--min-gap-threshold",
        type=float,
        default=2,
        help="Minimum gap duration (seconds) to fill with black frames when merging videos.",
    )
    merge_group.add_argument(
        "--max-gap-threshold",
        type=float,
        default=None,
        help="Maximum gap duration (seconds) to fill with black frames when merging videos.",
    )
    merge_group.add_argument(
        "--blank-video",
        default="blank_muted.MP4",
        help="Path to blank video file to use for gap filling (default: blank_muted.MP4).",
    )


def _add_compress_arguments(parser: argparse.ArgumentParser) -> None:
    """Attach arguments for the compress command."""
    parser.add_argument("-i", "--input", dest="input_path", required=True, help="Path to input video file or directory")
    parser.add_argument("-o", "--output", help="Path for output file or directory")
    parser.add_argument(
        "--quality",
        type=int,
        default=23,
        help="Video quality (CRF): lower = better quality, higher file size (default: 23)",
    )
    parser.add_argument(
        "--preset",
        default="fast",
        choices=["ultrafast", "fast", "medium", "slow", "veryslow"],
        help="Encoding preset: ultrafast to veryslow (default: fast)",
    )
    parser.add_argument(
        "--max-width",
        type=int,
        default=1280,
        help="Maximum width for output video, 0 = no scaling (default: 1280)",
    )


def _add_edit_arguments(parser: argparse.ArgumentParser) -> None:
    """Attach edit-specific CLI arguments to the parser."""
    parser.add_argument(
        "-i", "--input", dest="input_path", required=True, help="Path to input audio/video file or directory"
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Optional path for edited output; defaults alongside input when unspecified.",
    )
    parser.add_argument(
        "-t",
        "--timeline",
        required=True,
        help=(
            "Path to the edited timeline YAML file. The file should include your manual edits "
            "(type: 'all' segments) which take precedence over underlying segments."
        ),
    )


def _handle_process_command(args: argparse.Namespace) -> int:
    """Dispatch processing based on whether the input path is a file or directory."""
    input_path = Path(args.input_path)
    if input_path.is_dir():
        return process_directory(args)
    return process_single_file(args)


def _apply_complete_shortcut(args: argparse.Namespace) -> None:
    if not getattr(args, "complete", False):
        return

    for flag in COMPLETE_FLAG_TARGETS:
        setattr(args, flag, True)

    print("Complete processing enabled: --merge-videos --generate-timeline --analyze-speakers")


def _normalize_argv(argv: Sequence[str] | None) -> list[str]:
    if argv is None:
        argv = sys.argv[1:]

    args_list = list(argv)
    if not args_list:
        return args_list

    first_token = args_list[0]
    if first_token in SUPPORTED_COMMANDS or "-h" in args_list or "--help" in args_list:
        return args_list

    return ["process", *args_list]


def _dispatch(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    handler: Callable[[argparse.Namespace], int] | None = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args)


def build_parser() -> argparse.ArgumentParser:
    """Create the top-level CLI parser with subcommands."""
    parser = argparse.ArgumentParser(
        description="Process and edit audio/video files based on speaker analysis.",
    )
    subparsers = parser.add_subparsers(dest="mode", help="Processing mode")

    process_parser = subparsers.add_parser("process", help="Process a file or directory")
    _add_process_arguments(process_parser)
    process_parser.set_defaults(handler=_handle_process_command)

    compress_parser = subparsers.add_parser("compress", help="Compress video files to H.264 with smaller file sizes")
    _add_compress_arguments(compress_parser)
    compress_parser.set_defaults(handler=compress_command)

    edit_parser = subparsers.add_parser("edit", help="Apply manual timeline edits to processed media")
    _add_edit_arguments(edit_parser)
    edit_parser.set_defaults(handler=handle_edit_command)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point used by CLI and tests."""
    ensure_utf8_encoding()
    parser = build_parser()
    normalized_argv = _normalize_argv(argv)

    args = parser.parse_args(normalized_argv)

    _apply_complete_shortcut(args)

    try:
        return _dispatch(parser, args)
    except Exception as exc:  # noqa: BLE001
        print(f"An unexpected error occurred: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
