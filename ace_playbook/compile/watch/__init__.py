"""The watcher (docs/design/compiler.md 8.x "Watch (as built)"): a model-free configuration watcher that PROPOSES and never applies.

Deliberately empty of imports, like `ace_playbook.compile`: `status.py` imports `watch.summary` only, and `__main__` imports `watch.cli`,
which loads the network-using modules (`fetch`, `inventory`, `scan`) on demand. Nothing here is on the hook path; the conformance test
(tests/test_hook_conformance.py, ABSENT) asserts that the shadow hook never loads any of it.
"""
