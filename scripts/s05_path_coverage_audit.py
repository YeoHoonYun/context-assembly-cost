"""S05 - Path observation audit: share of file access that goes through shell commands
(PowerShell/Bash) and is therefore missing from the Read/Grep/Glob tool-argument paths.

Output: JSON on stdout; also written to <results-dir>/path_coverage.json.

Usage: python s05_path_coverage_audit.py --transcript-root <dir> --results-dir <dir>
"""
import json, os, re, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cac_common import setup  # noqa: E402

ARGS, CFG = setup(__doc__)
SRC = ARGS.transcript_root
ABS = re.compile(r"([A-Za-z]:\\[^\"'\s|;,)]+)")
RELPY = re.compile(r"[\w./\\-]+\.(py|md|json|jsonl|csv|ps1|bat|txt|yaml|yml)\b", re.I)
READ = {"Read", "NotebookRead", "Grep", "Glob"}
PK = ("file_path", "notebook_path", "path", "filePath")


def blocks(m):
    c = (m or {}).get("content")
    if isinstance(c, list):
        for b in c:
            if isinstance(b, dict):
                yield b


tool_paths = 0
ps_calls = 0
ps_with_abs = 0
ps_with_any_file = 0
ps_extra_paths = set()
tool_path_set = set()

for root, dirs, fns in os.walk(SRC):
    for fn in fns:
        if not fn.endswith(".jsonl"):
            continue
        with open(os.path.join(root, fn), encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") != "assistant":
                    continue
                for b in blocks(o.get("message")):
                    if b.get("type") != "tool_use":
                        continue
                    n = b.get("name") or ""
                    inp = b.get("input") or {}
                    if n in READ:
                        for k in PK:
                            v = inp.get(k)
                            if isinstance(v, str) and len(v) > 2:
                                tool_paths += 1
                                tool_path_set.add(os.path.normpath(v).lower())
                                break
                    elif n in ("PowerShell", "Bash"):
                        ps_calls += 1
                        cmd = inp.get("command") or ""
                        ab = ABS.findall(cmd)
                        if ab:
                            ps_with_abs += 1
                            for p in ab:
                                ps_extra_paths.add(os.path.normpath(p).lower())
                        if ab or RELPY.search(cmd):
                            ps_with_any_file += 1

only_shell = ps_extra_paths - tool_path_set
RESULT = dict(
    tool_path_events=tool_paths,
    unique_tool_paths=len(tool_path_set),
    shell_calls=ps_calls,
    shell_calls_with_abs_path=ps_with_abs,
    shell_calls_referencing_any_file=ps_with_any_file,
    shell_file_reference_rate=round(ps_with_any_file / ps_calls, 4) if ps_calls else 0,
    unique_paths_seen_only_in_shell=len(only_shell),
    path_observation_coverage=round(tool_paths / (tool_paths + ps_with_any_file), 4)
    if (tool_paths + ps_with_any_file) else 0,
)
print(json.dumps(RESULT, ensure_ascii=False, indent=2))
json.dump(RESULT, open(os.path.join(ARGS.results_dir, "path_coverage.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
