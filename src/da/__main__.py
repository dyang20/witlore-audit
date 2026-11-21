"""Allow `python -m da ...` alongside the installed `da` console script.

Both routes call the same `cli.main`, so the UI's "equivalent CLI" hint works
whichever one the operator has in their shell.
"""

from .cli import main

raise SystemExit(main())
