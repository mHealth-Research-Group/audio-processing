# Audio/Video Processing Tool

This tool uses AI-powered speaker detection to apply effects to media files. It's designed for a two-pass workflow:

1.  **First Pass:** Automatically detect speech and generate an edited media file with muted conversations, along with a JSON timeline file.
2.  **Second Pass:** Manually edit the JSON timeline to customize effects (e.g., black out video segments), then apply these changes to create a final version.

## Installation

### Prerequisites

- Python 3.11 or higher
- FFmpeg installed and available in your system's PATH
- A Hugging Face account and an access token

### Setup

1.  **Clone the repository:**
    ```bash
    git clone <repository-url>
    cd audio-processing
    ```

2.  **Install dependencies:**
    ```bash
    uv sync
    ```

3.  **Configure your Hugging Face access token:**
    Create a `.env` file in the root of the project and add your token:
    ```
    HUGGINGFACE_ACCESS_TOKEN=your_token_here
    ```

## Usage

### Pass 1: Generate Timeline and Initial Muted File

This command processes your media file, mutes all detected speech, and creates a `_timeline.json` file.

```bash
# Process a single file
uv run main.py process your_video.mp4 --generate-timeline

# Process all files in a directory
uv run main.py process /path/to/your/media/ --generate-timeline
```

### Pass 2: Apply Manual Edits from JSON

After the first pass, you can manually edit the `_timeline.json` file. Change the `label` of any segment to `black` or `all` to apply different effects.

-   `"label": "speaking"` or `"label": "conversation"` (default): Mutes audio.
-   `"label": "black"`: Blacks out the video but preserves audio.
-   `"label": "all"`: Mutes audio and blacks out the video.

Once you've saved your changes to the JSON file, run the `apply-edits` command:

```bash
# Apply the edits from the timeline files in the current directory
uv run main.py apply-edits .
```

This will create a new, final edited file with the `_edited` suffix.

### Applying Effects to Specific Time Ranges

If you already know the exact time ranges you want to modify, you can use the `apply-effects` command for a faster, single-pass workflow:

```bash
# Black out video from 1:30 to 2:45
uv run main.py apply-effects your_video.mp4 "1:30-2:45" --effect black

# Mute audio in multiple segments
uv run main.py apply-effects your_video.mp4 "0:30-1:00" "3:15-4:30" --effect mute
```