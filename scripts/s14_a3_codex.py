"""S14 (A3) - Do the three measurement risks reproduce in a second agent product (Codex CLI)?

Background
  The three measurement risks were observed in Claude Code logs only.
    R1 resume duplication - resuming a session copies earlier history and inflates events
    R2 shell paths        - much file access happens inside shell commands and is invisible
                            to tool-argument logs
    R3 delegation accounting - savings are overstated if sub-trajectories are not counted
  Telling product-specific from general risks requires logs from another product.

Input: --codex-root  (directory searched recursively for rollout-*.jsonl session logs)
  schema: {timestamp, type, payload}
    type=session_meta   payload.session_id / cwd / cli_version / originator
    type=response_item  payload.type in {message, reasoning, function_call,
                        function_call_output, custom_tool_call, custom_tool_call_output}
                        tool-call identifier = payload.call_id

Output: <results-dir>/a3_codex.json  (released as second_product/a3_codex.json;
        file names in top_file_pairs are salted-hashed)

Usage: python s14_a3_codex.py --codex-root <dir> --results-dir <dir>
Requires env CAC_SALT.
"""
import json, os, re, sys
from collections import defaultdict, Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cac_common import setup, hash_id  # noqa: E402

ARGS, CFG = setup(__doc__, transcript_root=False, codex_root=True)
ROOT = ARGS.codex_root
RES = ARGS.results_dir

# for R2 - same family as the main-corpus production regex
ABS_PATH = re.compile(r"([A-Za-z]:\\[^\"'\s|;,)]+)")
REL_FILE = re.compile(r"(?<![\w\\/:.])([\w.\-]+[\\/][\w.\\/-]*\.\w{1,6}"
                      r"|[\w.\-]+\.(?:py|md|json|jsonl|csv|ps1|bat|txt|ya?ml|log))\b")
POSIX_PATH = re.compile(r"(?<![\w])(/(?:home|mnt|tmp|usr|var|etc|opt)/[^\"'\s|;,)]+)")

files = []
for r, d, fns in os.walk(ROOT):
    for fn in fns:
        if fn.endswith(".jsonl"):
            files.append(os.path.join(r, fn))
files.sort()

call_occ = defaultdict(list)     # call_id -> [(file, ts, name)]
tool_names = Counter()
payload_types = Counter()
top_types = Counter()
sess_meta = {}
lines_total = parse_err = 0
shell_cmds = 0
shell_with_path = 0
path_tokens = 0
exec_inputs = 0
compacted = 0

for path in files:
    fkey = os.path.basename(path)
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            lines_total += 1
            try:
                o = json.loads(line)
            except Exception:
                parse_err += 1
                continue
            t = o.get("type")
            top_types[t] += 1
            p = o.get("payload") or {}
            ts = o.get("timestamp")
            if t == "session_meta":
                sess_meta[fkey] = {
                    "session_id": p.get("session_id"),
                    "cwd": p.get("cwd"),
                    "cli_version": p.get("cli_version"),
                    "originator": p.get("originator"),
                    "started": p.get("timestamp")}
            elif t == "compacted":
                compacted += 1
            elif t == "response_item":
                pt = p.get("type")
                payload_types[pt] += 1
                cid = p.get("call_id")
                if cid and pt in ("function_call", "custom_tool_call"):
                    nm = p.get("name") or "?"
                    tool_names[nm] += 1
                    call_occ[cid].append((fkey, ts, nm))
                    # R2 - paths inside the tool input
                    raw = p.get("input") or p.get("arguments") or ""
                    if isinstance(raw, str) and raw:
                        if "shell_command" in raw or nm in ("shell", "exec", "bash"):
                            shell_cmds += 1
                            n = (len(ABS_PATH.findall(raw)) + len(REL_FILE.findall(raw))
                                 + len(POSIX_PATH.findall(raw)))
                            if n:
                                shell_with_path += 1
                                path_tokens += n
                        if nm == "exec":
                            exec_inputs += 1

total_calls = sum(len(v) for v in call_occ.values())
uniq_calls = len(call_occ)
dup = {c: v for c, v in call_occ.items() if len(v) > 1}
cross = {c: v for c, v in dup.items() if len({x[0] for x in v}) > 1}
within = {c: v for c, v in dup.items() if len({x[0] for x in v}) == 1}

pairs = Counter()
for c, v in cross.items():
    s = sorted({x[0] for x in v})
    for i in range(len(s)):
        for j in range(i + 1, len(s)):
            pairs[(s[i], s[j])] += 1

gaps = []
for c, v in dup.items():
    ts = sorted([x[1] for x in v if x[1]])
    for a, b in zip(ts, ts[1:]):
        try:
            ta = datetime.fromisoformat(a.replace("Z", "+00:00"))
            tb = datetime.fromisoformat(b.replace("Z", "+00:00"))
            gaps.append((tb - ta).total_seconds())
        except Exception:
            pass
gaps.sort()


def pctl(v, q):
    return round(v[int((len(v) - 1) * q)], 3) if v else None


# R3 - look for delegation tools
DELEG_HINT = re.compile(r"(agent|subagent|delegate|spawn|task)", re.I)
deleg_tools = {n: c for n, c in tool_names.items() if DELEG_HINT.search(n)}

OUT = {
    "generated_utc": datetime.now(timezone.utc).isoformat(),
    "product": "Codex CLI",
    "root": "<codex_sessions_root>",
    "session_files": len(files),
    "lines": lines_total,
    "json_parse_errors": parse_err,
    "top_level_types": dict(top_types.most_common()),
    "response_item_types": dict(payload_types.most_common()),
    "compacted_records": compacted,
    "cli_versions": dict(Counter(m.get("cli_version") for m in sess_meta.values()).most_common()),
    "originators": dict(Counter(m.get("originator") for m in sess_meta.values()).most_common()),

    "R1_resume_duplication": {
        "tool_call_occurrences": total_calls,
        "distinct_call_ids": uniq_calls,
        "duplication_rate_pct": round(100.0 * (total_calls - uniq_calls) / total_calls, 2)
        if total_calls else 0,
        "ids_appearing_more_than_once": len(dup),
        "duplicated_ACROSS_files": len(cross),
        "duplicated_WITHIN_single_file": len(within),
        "cross_file_share_of_duplicates_pct":
            round(100.0 * len(cross) / len(dup), 2) if dup else None,
        "distinct_file_pairs": len(pairs),
        "top_file_pairs": [{"pair": [hash_id(p[0]), hash_id(p[1])], "shared_call_ids": c}
                           for p, c in pairs.most_common(5)],
        "multiplicity_distribution": dict(sorted(Counter(len(v) for v in call_occ.values()).items())),
        "gap_seconds": {"n": len(gaps), "p05": pctl(gaps, .05), "median": pctl(gaps, .5),
                        "p95": pctl(gaps, .95), "max": pctl(gaps, 1.0),
                        "pct_zero": round(100.0 * sum(1 for g in gaps if g == 0) / len(gaps), 2)
                        if gaps else None},
    },
    "R2_shell_paths": {
        "shell_bearing_tool_calls": shell_cmds,
        "of_those_containing_path_tokens": shell_with_path,
        "share_pct": round(100.0 * shell_with_path / shell_cmds, 2) if shell_cmds else None,
        "path_tokens_extracted": path_tokens,
        "exec_tool_calls": exec_inputs,
        "note": "Codex performs most file access inside exec/shell_command. Without a dedicated read "
                "tool log, paths do not appear in the tool log.",
    },
    "R3_delegation": {
        "tool_name_distribution": dict(tool_names.most_common(20)),
        "delegation_like_tools": deleg_tools,
        "separate_sub_trajectory_files": 0,
        "note": "The Codex sessions tree has no files dedicated to sub-trajectories. Delegation, "
                "when it occurs, is not recorded as a separate file.",
    },
}
json.dump(OUT, open(os.path.join(RES, "a3_codex.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print(json.dumps(OUT, ensure_ascii=False, indent=2))
