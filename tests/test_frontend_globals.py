"""All web/js scripts share one global scope: a name declared twice silently replaces the earlier one
(fa.js's faFmt replaced des.js's and blanked the FA panel). Fail on any duplicate top-level name."""
import re
from collections import defaultdict
from pathlib import Path

DECL = re.compile(r"^(?:async\s+)?(?:function\s*\*?\s*([A-Za-z_$][\w$]*)|(?:const|let|var|class)\s+([A-Za-z_$][\w$]*))", re.M)
# Intentional: each page module registers its own DRILL handlers, never top-level names.


def test_no_duplicate_top_level_names_across_scripts():
    seen = defaultdict(list)
    for f in sorted((Path(__file__).parent.parent / "web" / "js").glob("*.js")):
        for m in DECL.finditer(f.read_text(encoding="utf-8")):
            seen[m.group(1) or m.group(2)].append(f.name)
    dups = {k: v for k, v in seen.items() if len(v) > 1}
    assert not dups, f"declared in more than one place: {dups}"
