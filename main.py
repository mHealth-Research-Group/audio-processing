#!/usr/bin/env python
import sys
from pathlib import Path
import warnings


# Add the src directory to the Python path
sys.path.append(str(Path(__file__).parent / "src"))

from src.main import main

# Silence the deprecation warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

if __name__ == "__main__":
    sys.exit(main())
