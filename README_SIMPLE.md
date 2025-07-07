# Simple Audio Processing Workflow

## What You Need
- Python with the required packages (pyannote.audio, torch, etc.)
- FFmpeg installed
- Your `HUGGINGFACE_ACCESS_TOKEN` in a `.env` file

## Step 1: Generate Timeline Files

Run this to analyze all audio/video files in a directory and create editable timeline files:

```bash
python batch_process.py /path/to/your/media/directory
```

This creates `*_timeline.json` files for each media file.

## Step 2: Edit Timeline Files (Human Step)

Open each `*_timeline.json` file and look at the `timeline` section. You'll see segments like:

```json
{
  "start": "2:15.500",
  "end": "2:45.200", 
  "type": "speech",
  "label": "conversation",
  "speakers": 2
}
```

**To remove a conversation segment**: Delete the entire segment object from the timeline array.

**To keep a conversation segment**: Change `"label": "conversation"` to `"label": "speaking"`.

## Step 3: Apply Your Edits

Run this to process all the video files based on your edited timeline files:

```bash
python apply_edits.py /path/to/your/media/directory
```

This creates new video files with `_edited` suffix where conversation segments are zeroed out.

## That's It!

- `batch_process.py` - Analyzes all files, creates timeline JSONs
- Edit the JSON files by hand to mark what to remove
- `apply_edits.py` - Creates new videos with conversations zeroed out

## Example Timeline Structure

```json
{
  "timeline": [
    {
      "start": "0:00.000",
      "end": "0:15.500",
      "type": "silence",
      "speakers": 0,
      "label": "silence"
    },
    {
      "start": "0:15.500", 
      "end": "1:30.200",
      "type": "speech",
      "speakers": 1,
      "label": "speaking"
    },
    {
      "start": "1:30.200",
      "end": "2:45.800", 
      "type": "speech",
      "speakers": 2,
      "label": "conversation"  // ← This will be zeroed out
    }
  ]
}
```

## Notes

- Only segments with `"label": "conversation"` get zeroed out
- Single speaker segments (`"label": "speaking"`) are preserved
- Silence segments are always preserved
- The script automatically detects video vs audio files and processes accordingly 