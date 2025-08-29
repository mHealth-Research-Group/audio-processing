#!/usr/bin/env python
import sys
from pathlib import Path
import warnings


# Add the src directory to the Python path
sys.path.append(str(Path(__file__).parent / "src"))

from src.main import main

# Silence the deprecation warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Silence torchaudio deprecation warnings
warnings.filterwarnings(
    "ignore", category=UserWarning, message=".*torchaudio._backend.list_audio_backends has been deprecated.*"
)
warnings.filterwarnings("ignore", category=UserWarning, module="pyannote.audio.core.io")
warnings.filterwarnings("ignore", category=UserWarning, module="speechbrain.utils.torch_audio_backend")

if __name__ == "__main__":
    sys.exit(main())
