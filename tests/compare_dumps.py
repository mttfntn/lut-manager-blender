"""Compare two result dumps from tests/test_addon.py --dump (for example the last
release and your working copy) and list every difference.

    python tests/compare_dumps.py old.json new.json

Exits with status 1 if parsed LUTs, rendered pixels or check outcomes differ.
"""

import json
import sys

old, new = (json.load(open(p)) for p in sys.argv[1:3])
diffs = []
for key in sorted(set(old["dump"]) | set(new["dump"])):
    if old["dump"].get(key) != new["dump"].get(key):
        diffs.append(f"{key}: {old['dump'].get(key)} -> {new['dump'].get(key)}")
old_checks = {c[0]: c[1] for c in old["checks"]}
for name, ok, _detail in new["checks"]:
    if name in old_checks and old_checks[name] != ok:
        diffs.append(f"check '{name}': {old_checks[name]} -> {ok}")
only_new = [c[0] for c in new["checks"] if c[0] not in old_checks]

print(f"{len(old['dump'])} vs {len(new['dump'])} recorded results, {len(diffs)} differences")
for d in diffs:
    print("  DIFF", d)
if only_new:
    print("  checks only in the new run:", only_new)
sys.exit(1 if diffs else 0)
