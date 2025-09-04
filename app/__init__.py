__version__ = "0.0.1"

import sys
from pathlib import Path

AGENT_NAME = "eon"

# Add the src directory to Python path so we can import modules directly
src_dir = Path(__file__).parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))