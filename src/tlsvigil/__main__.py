"""Allow ``python -m tlsvigil``, the same command as the ``tlsvigil`` script."""

import sys

from tlsvigil.cli import main

sys.exit(main())
