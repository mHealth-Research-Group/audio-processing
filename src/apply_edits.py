from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Dict, Tuple

from .utils import compare_timelines, load_timeline


def handle_edit_command(args: Namespace) -> int:
    """Entry point for the `edit` CLI command."""
    try:
        media_path = Path(args.input_path).expanduser().resolve()
        timeline_path = Path(args.timeline).expanduser().resolve()
        edited_timeline_path = Path(args.edited_timeline).expanduser().resolve()
        _validate_required_paths(media_path, timeline_path, edited_timeline_path)
    except (AttributeError, TypeError):
        print("Error: Missing required arguments for edit command.")
        return 1
    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        return 1

    try:
        original_timeline, modified_timeline = _load_timeline_pair(timeline_path, edited_timeline_path)
    except Exception as exc:  # noqa: BLE001
        print(f"Error loading timelines: {exc}")
        return 1

    comparison = compare_timelines(original_timeline, modified_timeline)
    _print_timeline_comparison(media_path, timeline_path, edited_timeline_path, comparison)
    return 0


def _validate_required_paths(media_path: Path, timeline_path: Path, edited_timeline_path: Path) -> None:
    if not media_path.exists():
        raise FileNotFoundError(f"Media path not found: {media_path}")
    if not timeline_path.exists():
        raise FileNotFoundError(f"Timeline file not found: {timeline_path}")
    if not edited_timeline_path.exists():
        raise FileNotFoundError(f"Edited timeline file not found: {edited_timeline_path}")


def _load_timeline_pair(timeline_path: Path, edited_timeline_path: Path) -> Tuple[Dict[str, object], Dict[str, object]]:
    original = load_timeline(timeline_path)
    edited = load_timeline(edited_timeline_path)
    if original is None:
        raise ValueError(f"Timeline file is empty or invalid: {timeline_path}")
    if edited is None:
        raise ValueError(f"Edited timeline file is empty or invalid: {edited_timeline_path}")
    return original, edited


def _print_timeline_comparison(
    media_path: Path,
    original_path: Path,
    edited_path: Path,
    comparison: Dict[str, object],
) -> None:
    changed = comparison.get("total_changed", 0)
    unchanged = comparison.get("total_unchanged", 0)
    percentage = comparison.get("change_percentage", 0.0)

    print("Edit summary")
    print("------------")
    print(f"Media: {media_path}")
    print(f"Original timeline: {original_path}")
    print(f"Edited timeline: {edited_path}")
    print(f"Segments changed: {changed}")
    print(f"Segments unchanged: {unchanged}")
    print(f"Change percentage: {percentage:.1f}%")

    changed_segments = comparison.get("changed_segments", [])
    if not changed_segments:
        print("No differences detected between timelines.")
        return

    preview_count = min(5, len(changed_segments))
    print("")
    print(f"Previewing first {preview_count} changed segment(s):")
    for segment in changed_segments[:preview_count]:
        start = segment.get("start", "?")
        end = segment.get("end", "?")
        label = segment.get("label", segment.get("type", ""))
        print(f"  - {start} -> {end} ({label})")
