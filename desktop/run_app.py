"""PyInstaller's entry script (it wants a file, not `-m`). Same as `python -m caption_translator`."""
import sys

from caption_translator.main import main

if __name__ == "__main__":
    sys.exit(main())
