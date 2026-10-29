"""`python3 -m macmote`: the same entry point as the `macmote` command."""
import sys

from .cli import main

sys.exit(main())
