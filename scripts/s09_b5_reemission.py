"""S09 (B5) - Characterising the 17.3% event re-emission: noise ceiling of the measurement pipeline.

Background
  In S06, dedup on (lineage, tool_use_id, kind) removed 17.3% of the raw events.
  Only one lineage merge occurred (339 -> 338 lineages). The question is whether the duplicates
  are copies created by session resume/fork or re-emission of assistant messages within the
  same session.

Question (B5)
  Which events are re-emitted? If re-emission concentrates on particular tools or kinds,
  the corresponding indicators are systematically inflated without dedup.

Output (in --results-dir): b5_reemission.json  (no identifiers are written)

Usage: python s09_b5_reemission.py --transcript-root <dir> --results-dir <dir>
"""
import json, os, re, sys
from collections import defaultdict, Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cac_common import setup  # noqa: E402

ARGS, CFG = setup(__doc__)
SRC = ARGS.transcript_root
RES = ARGS.results_dir

READ_TOOLS = {"Read", "NotebookRead"}
SEARCH_TOOLS = {"Grep", "Glob"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
DELEG_TOOLS = {"Agent", "Task", "Skill", "Workflow"}


def blocks(m):
    c = (m or {}).get("content")
    if isinstance(c, list):
        for b in c:
            if isinstance(b, dict):
                yield b


def parse_ts(s):
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


# tool_use_id -> occurrence records
occ = defaultdict(list)      # tid -> [(sid, uuid, ts, tool)]
uuid_occ = defaultdict(list)  # (sid, uuid) -> count
msg_tools = {}               # (sid, uuid) -> set(tool)
n_msgs = 0
files = [f for f in os.listdir(SRC) if f.endswith(".jsonl")]
for fn in files:
    sid = fn[:-6]
    with open(os.path.join(SRC, fn), encoding="utf-8", errors="replace") as f:
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
            n_msgs += 1
            uu = o.get("uuid")
            ts = parse_ts(o.get("timestamp"))
            uuid_occ[(sid, uu)].append(ts)
            tools = set()
            for b in blocks(o.get("message")):
                if b.get("type") != "tool_use":
                    continue
                tid = b.get("id"); name = b.get("name") or "?"
                tools.add(name)
                if tid:
                    occ[tid].append((sid, uu, ts, name))
            if tools:
                msg_tools[(sid, uu)] = tools


def kind_of(name):
    if name in READ_TOOLS: return "READ"
    if name in SEARCH_TOOLS: return "SEARCH"
    if name in WRITE_TOOLS: return "EDIT"
    if name in SHELL_TOOLS: return "SHELL"
    if name in DELEG_TOOLS: return "DELEGATE"
    return "OTHER"


total = sum(len(v) for v in occ.values())
uniq = len(occ)
dup_tids = {t: v for t, v in occ.items() if len(v) > 1}
cross_session = {t: v for t, v in dup_tids.items() if len({x[0] for x in v}) > 1}
cross_uuid = {t: v for t, v in dup_tids.items() if len({x[1] for x in v}) > 1}

# re-emission rate per tool
by_tool_total, by_tool_dup = Counter(), Counter()
for t, v in occ.items():
    nm = v[0][3]
    by_tool_total[nm] += len(v)
    if len(v) > 1:
        by_tool_dup[nm] += len(v) - 1
by_kind_total, by_kind_dup = Counter(), Counter()
for nm, c in by_tool_total.items(): by_kind_total[kind_of(nm)] += c
for nm, c in by_tool_dup.items(): by_kind_dup[kind_of(nm)] += c

# re-emission gaps
gaps = []
for t, v in dup_tids.items():
    ts = sorted([x[2] for x in v if x[2]])
    for a, b in zip(ts, ts[1:]):
        gaps.append((b - a).total_seconds())
gaps.sort()


def pct(v, q):
    if not v: return None
    i = int((len(v) - 1) * q)
    return round(v[i], 2)


# multiplicity distribution
mult = Counter(len(v) for v in occ.values())

OUT = {
    "generated_utc": datetime.now(timezone.utc).isoformat(),
    "question": "Where does the 17.3% event duplication come from - session forks or re-emission within the same session?",
    "assistant_messages": n_msgs,
    "tool_use_occurrences": total,
    "distinct_tool_use_ids": uniq,
    "reemission_rate_pct": round(100.0 * (total - uniq) / total, 2) if total else 0,
    "tool_use_ids_appearing_more_than_once": len(dup_tids),
    "share_of_ids_duplicated_pct": round(100.0 * len(dup_tids) / uniq, 2) if uniq else 0,
    "duplicated_ACROSS_sessions": len(cross_session),
    "duplicated_across_DIFFERENT_uuids_same_or_other_session": len(cross_uuid),
    "multiplicity_distribution": dict(sorted(mult.items())),
    "reemission_gap_seconds": {
        "n": len(gaps), "p05": pct(gaps, .05), "median": pct(gaps, .5),
        "p95": pct(gaps, .95), "max": pct(gaps, 1.0),
        "pct_under_1s": round(100.0 * sum(1 for g in gaps if g < 1) / len(gaps), 2) if gaps else None,
        "pct_under_60s": round(100.0 * sum(1 for g in gaps if g < 60) / len(gaps), 2) if gaps else None,
    },
    "by_kind": {k: {"occurrences": by_kind_total[k], "duplicated": by_kind_dup[k],
                    "reemission_rate_pct": round(100.0 * by_kind_dup[k] / by_kind_total[k], 2)
                    if by_kind_total[k] else 0}
                for k in sorted(by_kind_total, key=lambda x: -by_kind_total[x])},
    "by_tool": {nm: {"occurrences": by_tool_total[nm], "duplicated": by_tool_dup[nm],
                     "reemission_rate_pct": round(100.0 * by_tool_dup[nm] / by_tool_total[nm], 2)
                     if by_tool_total[nm] else 0}
                for nm in sorted(by_tool_total, key=lambda x: -by_tool_total[x])[:15]},
}

# verdict
cs = OUT["duplicated_ACROSS_sessions"]
OUT["verdict"] = {
    "cross_session_share_of_duplicates_pct":
        round(100.0 * cs / len(dup_tids), 2) if dup_tids else 0,
    "conclusion": ("If re-emission within the same session dominated, a 'fork replay' attribution "
                   "would not hold. See the cross_session share."),
}
json.dump(OUT, open(os.path.join(RES, "b5_reemission.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print(json.dumps(OUT, ensure_ascii=False, indent=2))
