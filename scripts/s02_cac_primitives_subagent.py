"""S02 - CAC pilot on sub-agent (sidechain) transcripts: can the delegation boundary be observed?

Output (in --results-dir):
  cac_pilot_subagents.csv     one row per sub-agent transcript (agent_id and parent_session are
                              salted hashes; start/end are coarsened to the UTC date)
  cac_pilot_sub_summary.json  corpus summary (parent ids in top_parents are salted hashes)

Released copies: data/pilot_subagents_20260904.csv + results/pilot_summary_subagent_20260904.json
(scan of 2026-09-04) and data/pilot_subagents_20260905.csv + results/pilot_summary_subagent_20260905.json
(rescan of 2026-09-05).

Usage: python s02_cac_primitives_subagent.py --transcript-root <dir> --results-dir <dir>
Requires env CAC_SALT.
"""
import json, os, csv, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cac_common import setup, hash_id  # noqa: E402

ARGS, CFG = setup(__doc__)
SRC = ARGS.transcript_root
OUT = ARGS.results_dir
READ = {"Read", "NotebookRead", "Grep", "Glob"}
WRITE = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def blocks(m):
    c = (m or {}).get("content")
    if isinstance(c, list):
        for b in c:
            if isinstance(b, dict):
                yield b


files = []
for root, dirs, fns in os.walk(SRC):
    if os.path.basename(root) == "subagents":
        for fn in fns:
            if fn.endswith(".jsonl"):
                files.append(os.path.join(root, fn))

rows, tools = [], Counter()
tot_lines = err = 0
for p in files:
    parent = os.path.basename(os.path.dirname(os.path.dirname(p)))
    turns = reads = writes = 0
    sid_true = 0
    paths, res_chars, out_tok, cr_tok, crd_tok = [], 0, 0, 0, 0
    id2t = {}
    t0 = t1 = None
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            tot_lines += 1
            try:
                o = json.loads(line)
            except Exception:
                err += 1
                continue
            ts = o.get("timestamp")
            if ts:
                t0 = ts if t0 is None or ts < t0 else t0
                t1 = ts if t1 is None or ts > t1 else t1
            if o.get("isSidechain"):
                sid_true += 1
            if o.get("type") == "assistant":
                turns += 1
                u = (o.get("message") or {}).get("usage") or {}
                out_tok += u.get("output_tokens") or 0
                cr_tok += u.get("cache_creation_input_tokens") or 0
                crd_tok += u.get("cache_read_input_tokens") or 0
                for b in blocks(o.get("message")):
                    if b.get("type") == "tool_use":
                        n = b.get("name") or "?"
                        tools[n] += 1
                        id2t[b.get("id")] = n
                        if n in READ:
                            reads += 1
                            i = b.get("input") or {}
                            for k in ("file_path", "path", "pattern"):
                                v = i.get(k)
                                if isinstance(v, str) and len(v) > 2:
                                    paths.append(v)
                                    break
                        elif n in WRITE:
                            writes += 1
            elif o.get("type") == "user":
                for b in blocks(o.get("message")):
                    if b.get("type") == "tool_result":
                        c = b.get("content")
                        if isinstance(c, str):
                            res_chars += len(c)
                        elif isinstance(c, list):
                            for x in c:
                                if isinstance(x, dict) and x.get("type") == "text":
                                    res_chars += len(x.get("text") or "")
    if turns == 0:
        continue
    keys = [os.path.normpath(x).lower() for x in paths]
    rows.append(dict(agent_file=os.path.basename(p), parent_session=parent,
                     turns=turns, reads=reads, writes=writes,
                     unique_paths=len(set(keys)), rereads=len(keys) - len(set(keys)),
                     sidechain_lines=sid_true, result_chars=res_chars,
                     out_tokens=out_tok, cache_create=cr_tok, cache_read=crd_tok,
                     start=t0, end=t1))


def release_row(r):
    """Release form: hashed ids (file stem without '.jsonl'), timestamps coarsened to UTC date."""
    out = {"agent_id": hash_id(r["agent_file"][:-6]), "parent_session": hash_id(r["parent_session"])}
    for k, v in r.items():
        if k in ("agent_file", "parent_session", "start", "end"):
            continue
        out[k] = v
    out["start_date_utc"] = (r["start"] or "")[:10]
    out["end_date_utc"] = (r["end"] or "")[:10]
    return out


rel = [release_row(r) for r in rows]
with open(os.path.join(OUT, "cac_pilot_subagents.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rel[0].keys()))
    w.writeheader()
    w.writerows(rel)

parents = Counter(r["parent_session"] for r in rows)
S = dict(subagent_files=len(files), with_turns=len(rows), lines=tot_lines, parse_errors=err,
         parent_sessions=len(parents), max_subagents_per_session=max(parents.values()),
         total_turns=sum(r["turns"] for r in rows),
         total_reads=sum(r["reads"] for r in rows),
         total_writes=sum(r["writes"] for r in rows),
         total_result_chars=sum(r["result_chars"] for r in rows),
         total_out_tokens=sum(r["out_tokens"] for r in rows),
         total_cache_create=sum(r["cache_create"] for r in rows),
         total_cache_read=sum(r["cache_read"] for r in rows),
         window=[min(r["start"] for r in rows), max(r["end"] for r in rows)],
         top_parents={hash_id(k): v for k, v in parents.most_common(5)},
         tools=dict(tools.most_common(20)))
print(json.dumps(S, ensure_ascii=False, indent=2))
json.dump(S, open(os.path.join(OUT, "cac_pilot_sub_summary.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
