#!/usr/bin/env python3
"""A FAKE `claude -p` that never answers and leaves a grandchild behind (the Reader's timeout test; stdlib only, synthetic).

It starts a `sleep`-like grandchild, writes the grandchild's pid to $ACE_WATCH_FAKE_DIR/grandchild.pid and hangs. The Reader's timeout must
end the whole process group, not only this process.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
Path(os.environ["ACE_WATCH_FAKE_DIR"], "grandchild.pid").write_text(str(grandchild.pid))
time.sleep(60)
