"""S13 (C2 / C3) - Accuracy of the shell path regex + decomposition by delegation depth.

C2  Accuracy of the shell path extraction regex.
    Precision proxy: does an extracted path exist on the file system / is it an obvious non-path?
    Recall proxy   : candidates caught by a wider net that the production regex missed.
    NOTE: this is not a human-coded comparison. It is an automated approximation and cannot be
    called 'accuracy' as such.

C3  Delegation depth. Do sub-agents call Agent again (nested delegation), and does depth add cost?

Output (in --results-dir): c2c3_regex_depth.json
  Release form: only counts are written; no example path strings are emitted.

Usage: python s13_c2c3_regex_depth.py --transcript-root <dir> --results-dir <dir>
Note: the existence check (os.path.exists) is evaluated against the disk of the machine running
the script; relative paths are resolved against the current working directory.
"""
import json, os, re, random, sys
from collections import defaultdict, Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cac_common import setup  # noqa: E402

ARGS, CFG = setup(__doc__)
SRC = ARGS.transcript_root
RES = ARGS.results_dir
SHELL_TOOLS = {"Bash", "PowerShell"}

# ---- production regex (same as s06/s08) ----
MUT_VERBS = re.compile(
    r"(?<![\w-])(Set-Content|Add-Content|Out-File|New-Item|Remove-Item|Move-Item|Copy-Item|"
    r"Rename-Item|Clear-Content|robocopy|xcopy|rm|mv|cp|touch|mkdir|rmdir|dd|truncate|install|"
    r"tee|sed\s+-i|perl\s+-i)(?![\w-])", re.IGNORECASE)
REDIRECT = re.compile(r"(?<![0-9>])>>?(?!\s*&)\s*([\"']?)([A-Za-z]:\\[^\"'\s|;)]+|/[^\"'\s|;)]+|[\w.\\/-]+\.\w+)\1")
ABS_PATH = re.compile(r"([A-Za-z]:\\[^\"'\s|;,)]+)")
REL_FILE = re.compile(r"(?<![\w\\/:.])([\w.\-]+[\\/][\w.\\/-]*\.\w{1,6}|[\w.\-]+\.(?:py|md|json|jsonl|csv|ps1|bat|txt|ya?ml|log))\b")

# ---- wider net (recall proxy) ----
WIDE = re.compile(
    r"([A-Za-z]:[\\/][^\s\"'|;,)]+"          # absolute path
    r"|\$env:[A-Za-z_]+[\\/][^\s\"'|;,)]+"    # environment-variable based
    r"|\.{1,2}[\\/][^\s\"'|;,)]+"             # ./ ../
    r"|[\w.\-]+\.(?:py|md|json|jsonl|csv|ps1|bat|txt|ya?ml|log|R|sql|html|xml|toml|ini|cfg))")

NON_PATH = re.compile(r"^(https?:|--|-[A-Za-z]$|\d+\.\d+$)")


def norm(p):
    if not p: return None
    p = p.strip().strip('"').strip("'").replace("/", "\\")
    return p if len(p) > 3 else None


def prod_extract(cmd):
    mut = bool(MUT_VERBS.search(cmd) or REDIRECT.search(cmd))
    ps = [m.group(1) for m in ABS_PATH.finditer(cmd)] + [m.group(1) for m in REL_FILE.finditer(cmd)]
    if mut:
        ps += [m.group(2) for m in REDIRECT.finditer(cmd)]
    out, seen = [], set()
    for p in ps:
        p = norm(p)
        if p and p.lower() not in seen:
            seen.add(p.lower()); out.append(p)
    return out


def wide_extract(cmd):
    out, seen = [], set()
    for m in WIDE.finditer(cmd):
        p = norm(m.group(1))
        if p and not NON_PATH.match(p) and p.lower() not in seen:
            seen.add(p.lower()); out.append(p)
    return out


def blocks(m):
    c = (m or {}).get("content")
    if isinstance(c, list):
        for b in c:
            if isinstance(b, dict): yield b


# ================= scan =================
cmds = []
deleg_parent = Counter()      # Agent calls in parent sessions
deleg_in_sub = Counter()      # Agent calls inside sub-agents
sub_stats = {}                # sub-agent file -> (parent, turns, reads, writes, agent_calls, chars)
res_chars = {}

def scan(path, sid, origin):
    turns = reads = writes = agent = 0
    chars = 0
    ids = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try: o = json.loads(line)
            except Exception: continue
            if o.get("type") == "assistant":
                turns += 1
                for b in blocks(o.get("message")):
                    if b.get("type") != "tool_use": continue
                    nm = b.get("name") or ""
                    ids[b.get("id")] = nm
                    if nm in SHELL_TOOLS:
                        c = (b.get("input") or {}).get("command") or ""
                        if c: cmds.append((origin, c))
                    elif nm in ("Read", "NotebookRead", "Grep", "Glob"):
                        reads += 1
                    elif nm in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                        writes += 1
                    elif nm == "Agent":
                        agent += 1
                        (deleg_in_sub if origin == "sub" else deleg_parent)[sid] += 1
            elif o.get("type") == "user":
                for b in blocks(o.get("message")):
                    if b.get("type") == "tool_result" and b.get("tool_use_id") in ids:
                        c = b.get("content"); L = 0
                        if isinstance(c, str): L = len(c)
                        elif isinstance(c, list):
                            L = sum(len(x.get("text") or "") for x in c
                                    if isinstance(x, dict) and x.get("type") == "text")
                        if ids[b["tool_use_id"]] in ("Read", "NotebookRead", "Grep", "Glob"):
                            chars += L
    return turns, reads, writes, agent, chars


for fn in os.listdir(SRC):
    if fn.endswith(".jsonl"):
        scan(os.path.join(SRC, fn), fn[:-6], "parent")
for root, dirs, fns in os.walk(SRC):
    if os.path.basename(root) != "subagents": continue
    parent = os.path.basename(os.path.dirname(root))
    for fn in fns:
        if not fn.endswith(".jsonl"): continue
        t, r, w, a, ch = scan(os.path.join(root, fn), parent, "sub")
        sub_stats[fn] = dict(parent=parent, turns=t, reads=r, writes=w,
                             agent_calls=a, explore_chars=ch)

# ================= C2 =================
random.seed(20260906)
file_cmds = [c for o, c in cmds if WIDE.search(c)]
sample = random.sample(file_cmds, min(300, len(file_cmds)))

n_extracted = n_exist = n_nonpath = 0
missed_total = 0
missed_examples, false_examples = [], []
for c in sample:
    prod = prod_extract(c)
    wide = wide_extract(c)
    pl = {p.lower() for p in prod}
    for p in prod:
        n_extracted += 1
        if NON_PATH.match(p):
            n_nonpath += 1
            if len(false_examples) < 8: false_examples.append(p)
        elif os.path.exists(p):
            n_exist += 1
    for p in wide:
        if p.lower() not in pl:
            missed_total += 1
            if len(missed_examples) < 12: missed_examples.append(p)

C2 = {
    "note": "Not a human-coded comparison. Automated audit approximated by on-disk existence and non-path patterns.",
    "shell_commands_total": len(cmds),
    "commands_with_file_like_token": len(file_cmds),
    "sampled": len(sample),
    "extracted_paths": n_extracted,
    "extracted_that_exist_on_disk": n_exist,
    "extracted_exist_rate_pct": round(100.0*n_exist/n_extracted, 2) if n_extracted else None,
    "extracted_matching_nonpath_pattern": n_nonpath,
    "wide_net_candidates_missed_by_production_regex": missed_total,
    "missed_per_sampled_command": round(missed_total/len(sample), 3) if sample else None,
    # Release form: the example lists (raw command fragments) are not emitted; counts only.
    "missed_examples_count": len(missed_examples),
    "nonpath_examples_count": len(false_examples),
}

# ================= C3 =================
by_parent = defaultdict(list)
for fn, s in sub_stats.items(): by_parent[s["parent"]].append(s)

nested = {fn: s for fn, s in sub_stats.items() if s["agent_calls"] > 0}
flat = {fn: s for fn, s in sub_stats.items() if s["agent_calls"] == 0}

def med(v):
    if not v: return None
    s = sorted(v); n = len(s)
    return s[n//2] if n % 2 else (s[n//2-1]+s[n//2])/2.0

C3 = {
    "subagent_files": len(sub_stats),
    "parent_sessions_with_subagents": len(by_parent),
    "parent_Agent_calls_total": sum(deleg_parent.values()),
    "parent_sessions_that_delegated": len(deleg_parent),
    "subagents_that_delegate_further": len(nested),
    "nested_Agent_calls_total": sum(deleg_in_sub.values()),
    "max_subagents_per_parent": max(len(v) for v in by_parent.values()) if by_parent else 0,
    "subagents_per_parent_median": med([float(len(v)) for v in by_parent.values()]),
    "depth1_flat": {
        "n": len(flat),
        "median_turns": med([float(s["turns"]) for s in flat.values()]),
        "median_reads": med([float(s["reads"]) for s in flat.values()]),
        "median_writes": med([float(s["writes"]) for s in flat.values()]),
        "median_explore_chars": med([float(s["explore_chars"]) for s in flat.values()]),
        "pct_zero_write": round(100.0*sum(1 for s in flat.values() if s["writes"] == 0)/len(flat), 2) if flat else None},
    "depth2_nested": {
        "n": len(nested),
        "median_turns": med([float(s["turns"]) for s in nested.values()]),
        "median_reads": med([float(s["reads"]) for s in nested.values()]),
        "median_writes": med([float(s["writes"]) for s in nested.values()]),
        "median_explore_chars": med([float(s["explore_chars"]) for s in nested.values()]),
        "pct_zero_write": round(100.0*sum(1 for s in nested.values() if s["writes"] == 0)/len(nested), 2) if nested else None},
    "caveat": "Grandchild trajectories spawned by nested delegation are not stored as separate files and are not observed.",
}

OUT = {"generated_utc": datetime.now(timezone.utc).isoformat(), "C2": C2, "C3": C3}
json.dump(OUT, open(os.path.join(RES, "c2c3_regex_depth.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print(json.dumps(OUT, ensure_ascii=False, indent=2))
