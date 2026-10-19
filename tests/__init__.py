import os
os.environ.pop("ACE_PLAYBOOK_KEYED", None)   # the operator shell's keyed-install flag must not leak into the tests
