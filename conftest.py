"""Make the repository root importable so `import docdiff` works under pytest
regardless of the import mode."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
