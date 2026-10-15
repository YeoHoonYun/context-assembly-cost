"""S12 (D1) - Where does the duplication in an earlier extraction of an overlapping window come from?

Background
  In the main corpus (339 files), B5 showed that all 2,315 cross-session duplicated tool_use_ids
  came from a **single** session pair (re-emission gap 0.00 s), i.e. a resumed session that
  copied the earlier history.

D1 question
  Does an earlier extraction of an overlapping window (mutation records, snapshot 2026-08-27)
  show the same structure?
    (a) are duplicates across sessions or within one session?
    (b) how many session pairs - is it concentrated in one pair?
    (c) is that pair the resumed pair found in the main corpus?

Input : --mutations-file  JSONL of mutation records from the earlier extraction (not released)
Config: resumed_pair_session_ids  the two raw session ids of the main-corpus resumed pair
Output: <results-dir>/prior_extraction_duplication.json  (session ids salted-hashed)

Usage: python s12_prior_extraction_duplication.py --mutations-file <file> --config config.json --results-dir <dir>
Requires env CAC_SALT.
"""
import json, os, sys
from collections import defaultdict, Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cac_common import setup, hash_id  # noqa: E402

ARGS, CFG = setup(__doc__, transcript_root=False, mutations_file=True, need_config=True)
SRC = ARGS.mutations_file
RES = ARGS.results_dir

rows = []
keys = Counter()
with open(SRC, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        rows.append(e)
        for k in e:
            keys[k] += 1

# tool_call_id -> occurrences (session, timestamp)
occ = defaultdict(list)
for e in rows:
    tid = e.get("tool_call_id") or e.get("tool_use_id") or e.get("tid")
    sid = e.get("session_id") or e.get("sid")
    ts = e.get("ts") or e.get("timestamp")
    if tid and sid:
        occ[tid].append((sid, ts, e.get("operation"), e.get("resource_key") or e.get("path")))

total = sum(len(v) for v in occ.values())
uniq = len(occ)
dup = {t: v for t, v in occ.items() if len(v) > 1}
cross = {t: v for t, v in dup.items() if len({x[0] for x in v}) > 1}
within = {t: v for t, v in dup.items() if len({x[0] for x in v}) == 1}

pairs = Counter()
for t, v in cross.items():
    s = sorted({x[0] for x in v})
    for i in range(len(s)):
        for j in range(i + 1, len(s)):
            pairs[(s[i], s[j])] += 1

# re-emission gaps
gaps = []
for t, v in dup.items():
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
    if not v:
        return None
    return round(v[int((len(v) - 1) * q)], 3)


MAIN_CORPUS_PAIR = set(CFG["resumed_pair_session_ids"])

OUT = {
    "generated_utc": datetime.now(timezone.utc).isoformat(),
    "source": "earlier extraction of an overlapping window (mutation records; not released)",
    "source_snapshot": "2026-08-27",
    "record_fields": dict(keys.most_common(20)),
    "records": len(rows),
    "tool_call_occurrences": total,
    "distinct_tool_call_ids": uniq,
    "duplication_rate_pct": round(100.0 * (total - uniq) / total, 2) if total else 0,
    "ids_appearing_more_than_once": len(dup),
    "multiplicity_distribution": dict(sorted(Counter(len(v) for v in occ.values()).items())),
    "duplicated_ACROSS_sessions": len(cross),
    "duplicated_WITHIN_single_session": len(within),
    "cross_session_share_of_duplicates_pct":
        round(100.0 * len(cross) / len(dup), 2) if dup else None,
    "distinct_session_pairs": len(pairs),
    "top_session_pairs": [
        {"pair": [hash_id(p[0]), hash_id(p[1])], "shared_ids": c,
         "matches_main_corpus_resumed_pair": set(p) == MAIN_CORPUS_PAIR}
        for p, c in pairs.most_common(5)],
    "reemission_gap_seconds": {
        "n": len(gaps), "p05": pctl(gaps, .05), "median": pctl(gaps, .5),
        "p95": pctl(gaps, .95), "max": pctl(gaps, 1.0),
        "pct_zero": round(100.0 * sum(1 for g in gaps if g == 0) / len(gaps), 2) if gaps else None},
}
json.dump(OUT, open(os.path.join(RES, "prior_extraction_duplication.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print(json.dumps(OUT, ensure_ascii=False, indent=2))
