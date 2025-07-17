# Audio Processing Pipeline

This project provides a multi-step pipeline for processing video and audio files. It is designed to merge multiple video files, detect and label gaps, analyze audio for speaker segments, and allow for manual adjustments to the timeline.

## Features

- **Video Merging**: Merges multiple video files in a directory into a single video.
- **Gap Detection**: Detects gaps between video files and fills them with black video.
- **Audio Analysis**: Uses `pyannote.audio` to perform voice activity detection and speaker diarization.
- **Manual Adjustments**: Allows for manual adjustments to the audio and video timeline.
- **Comprehensive Labeling**: Generates a comprehensive label file that can be used with annotation tools like Signaligner.

## Installation

1.  **Clone the repository:**

    ```bash
    git clone git@github.com:mHealth-Research-Group/audio-processing.git
    cd audio-processing
    ```

2.  **Install dependencies:**

    This project uses `uv` for dependency management. To install the required packages, run:

    ```bash
    uv sync
    ```

3.  **Set up environment variables:**

    This project requires a Hugging Face access token to download the `pyannote/segmentation-3.0` model. Create a `.env` file in the root of the project and add your token:

    ```
    HUGGINGFACE_ACCESS_TOKEN="your-hugging-face-token"
    ```

## Usage

The pipeline is designed to minimize user effort. Simply provide a folder of videos, the pipeline processes everything automatically, then you make manual adjustments and apply the final processing.

### Streamlined Workflow: Processing a Directory of Videos

This example shows the recommended workflow for processing a directory of videos with minimal effort.

**Step 1: Process Videos (Automated - Steps 1-3)**

This single command merges all videos, analyzes audio, and prepares the adjustment file:

```bash
uv run python main.py pipeline full test_videos -w pipeline_test
```

This command will:
- Merge all video files in the `test_videos` directory into `merged_video.mp4`
- Detect gaps between videos and create metadata
- Extract and analyze audio for voice activity and speaker detection
- Create a `merged_video_timeline_manual_adjustments.json` file for manual review

**Step 2: Manual Review (Human Input Required)**

Open the generated adjustment file and review/modify the timeline:

```bash
# Edit this file to make your adjustments:
# pipeline_test/merged_video_timeline_manual_adjustments.json
```

- Review the timeline segments and their labels
- Change labels to control effects:
  - `"speaking"` or `"conversation"`: Mute audio only
  - `"black"`: Black out video but preserve audio  
  - `"all"`: Mute audio AND black out video
  - `"silence"`: No effects applied
- Add custom time ranges if needed
- **Important**: Change `"manual_review_completed": false` to `true` when done

**Step 3: Apply Final Adjustments**

Apply your manual adjustments to create the final processed video:

```bash
uv run python main.py pipeline step4 -w pipeline_test
```

This creates:
- `pipeline_test/merged_video_final.mp4`: The final processed video
- `pipeline_test/merged_video_final_comprehensive_labels.csv`: Labels for annotation tools

### Check Pipeline Status

To check the current status of your pipeline at any time:

```bash
uv run python main.py pipeline status -w pipeline_test
```

## Pipeline Steps in Detail

### `full` (Recommended)

Runs the complete automated pipeline through step 3. This is the recommended approach for most users.

- First argument: Path to the directory containing the video files
- `-w, --working-dir`: Working directory for pipeline files (default: `./pipeline_work`)
- `-o, --output`: Output path for merged video (optional)
- `--min-duration-on`: Minimum duration for speech regions (in seconds, default: 0.1)
- `--min-duration-off`: Minimum duration for non-speech regions (in seconds, default: 0.1)

### `step4`

Applies manual adjustments to create the final video. This is the second command you run after manual review.

- `-w, --working-dir`: Working directory for pipeline files
- `--output-suffix`: Suffix for final output files (default: `_final`)

### `status`

Shows the current pipeline status and file locations.

- `-w, --working-dir`: Working directory for pipeline files

### Individual Steps (Advanced)

For advanced users who need granular control:

#### `step1`

Merges video files in a directory with gap detection.

- First argument: Path to the directory containing the video files
- `-w, --working-dir`: Working directory for pipeline files (default: `./pipeline_work`)
- `-o, --output`: Output path for merged video (optional)

#### `step2`

Processes the audio in the merged video file.

- `-w, --working-dir`: Working directory for pipeline files
- `--min-duration-on`: Minimum duration for speech regions (in seconds, default: 0.1)
- `--min-duration-off`: Minimum duration for non-speech regions (in seconds, default: 0.1)

#### `step3`

Prepares a file for manual adjustments.

- `-w, --working-dir`: Working directory for pipeline files

## Label File Format

The pipeline generates a comprehensive label file in CSV format with the following columns:

- `START_TIME`: Start timestamp in "YYYY-MM-DD HH:MM:SS.sss" format
- `STOP_TIME`: Stop timestamp in "YYYY-MM-DD HH:MM:SS.sss" format  
- `PREDICTION`: Label type (e.g., "Speaking", "Conversation", "Silence", "Missing_Video")
- `SOURCE`: Always "Player"
- `LABELSET`: Always "DEFAULT"

Example CSV content:

```csv
START_TIME,STOP_TIME,PREDICTION,SOURCE,LABELSET
2019-06-19 16:10:06.400,2019-06-19 23:49:53.600,Silence,Player,DEFAULT
2019-06-19 23:49:53.600,2019-06-20 06:36:43.200,Speaking,Player,DEFAULT
2019-06-20 06:36:43.200,2019-06-21 00:32:40.000,Conversation,Player,DEFAULT
```

This CSV file can be used with annotation tools like [Signaligner](https://https://signaligner.org/) to visualize and edit the timeline.