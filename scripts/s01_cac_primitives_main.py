"""S01 - CAC (Context Assembly Cost) pilot extraction, session unit, main-agent transcripts.

Purpose: decide whether first-order CAC indicators can be computed from the session
         transcripts at all (feasibility numbers). No file content is exported - only
         paths, tool names and lengths are read, and only counts are written.

Output (in --results-dir):
      cac_pilot_sessions.csv  (session-level CAC primitives; released as data/pilot_sessions.csv
                               without the cwds/branches columns)
      cac_pilot_summary.json  (summary + observability of factors A/B/C;
                               released as results/pilot_summary_main.json)

Usage: python s01_cac_primitives_main.py --transcript-root <dir> --config config.json --results-dir <dir>
Requires env CAC_SALT (session ids are written as salted hashes).
"""
import json, os, re, csv, sys
from collections import defaultdict, Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cac_common import setup, hash_id  # noqa: E402

ARGS, CFG = setup(__doc__, need_config=True)
SRC = ARGS.transcript_root
OUT = ARGS.results_dir

READ_TOOLS = {"Read", "NotebookRead"}
SEARCH_TOOLS = {"Grep", "Glob", "Explore", "WebFetch", "WebSearch"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
DELEG_TOOLS = {"Agent", "Task", "Skill", "Workflow"}
PATH_KEYS = ("file_path", "notebook_path", "path", "filePath")

# Verification-repetition signal (test / run retries). The pattern contains
# project-specific script and scheduler names, so it is read from the config.
VERIFY_RE = re.compile(CFG["verify_regex"], re.I)

DRIVE_RE = re.compile(r"^([A-Za-z]):")
PROJECT_DRIVE = CFG["project_drive"].upper()
REPO = CFG["project_repo_dirname"].lower()
TWO_LEVEL = tuple(x.lower() for x in CFG["two_level_dirs"])


def module_of(p):
    """Boundary unit: drive + 1-2 module levels. Defines a CAC 'boundary crossing'."""
    if not p:
        return None
    p = p.replace("/", "\\")
    m = DRIVE_RE.match(p)
    drive = m.group(1).upper() if m else "?"
    parts = [x for x in p.split("\\") if x]
    if drive == PROJECT_DRIVE and len(parts) >= 2 and parts[1].lower() == REPO:
        seg = parts[2] if len(parts) > 2 else "_root"
        if seg.lower() in TWO_LEVEL and len(parts) > 3:
            return f"{drive}:/{seg}/{parts[3]}"
        return f"{drive}:/{seg}"
    if len(parts) >= 2:
        return f"{drive}:/{parts[1]}"
    return f"{drive}:/"


def iter_blocks(msg):
    if not isinstance(msg, dict):
        return
    c = msg.get("content")
    if isinstance(c, list):
        for b in c:
            if isinstance(b, dict):
                yield b


def first_path(inp):
    if not isinstance(inp, dict):
        return None
    for k in PATH_KEYS:
        v = inp.get(k)
        if isinstance(v, str) and len(v) > 2:
            return v
    return None


def result_len(block):
    c = block.get("content")
    if isinstance(c, str):
        return len(c)
    if isinstance(c, list):
        n = 0
        for b in c:
            if isinstance(b, dict) and b.get("type") == "text":
                n += len(b.get("text") or "")
        return n
    return 0


def main():
    files = [f for f in os.listdir(SRC) if f.endswith(".jsonl")]
    rows = []
    tool_counter = Counter()
    parse_err = 0
    total_lines = 0
    mod_counter = Counter()
    ver_seen = Counter()
    ts_min, ts_max = None, None

    for fn in files:
        path = os.path.join(SRC, fn)
        sid = fn[:-6]
        n_read = n_search = n_write = n_shell = n_deleg = 0
        n_verify = 0
        read_paths = []
        turns = 0
        first_edit_turn = None
        tok_out = tok_cache_create = tok_cache_read = tok_in = 0
        res_chars_read = 0
        res_chars_all = 0
        id2tool = {}
        cwds = set()
        branches = set()
        vers = set()
        sidechain = 0
        try:
            fh = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                total_lines += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    parse_err += 1
                    continue
                ts = o.get("timestamp")
                if ts:
                    if ts_min is None or ts < ts_min:
                        ts_min = ts
                    if ts_max is None or ts > ts_max:
                        ts_max = ts
                if o.get("cwd"):
                    cwds.add(o["cwd"])
                if o.get("gitBranch"):
                    branches.add(o["gitBranch"])
                if o.get("version"):
                    vers.add(o["version"])
                    ver_seen[o["version"]] += 1
                if o.get("isSidechain"):
                    sidechain += 1
                t = o.get("type")
                msg = o.get("message") or {}
                if t == "assistant":
                    turns += 1
                    u = msg.get("usage") or {}
                    tok_in += u.get("input_tokens") or 0
                    tok_out += u.get("output_tokens") or 0
                    tok_cache_create += u.get("cache_creation_input_tokens") or 0
                    tok_cache_read += u.get("cache_read_input_tokens") or 0
                    for b in iter_blocks(msg):
                        if b.get("type") != "tool_use":
                            continue
                        name = b.get("name") or "?"
                        tool_counter[name] += 1
                        id2tool[b.get("id")] = name
                        inp = b.get("input") or {}
                        if name in READ_TOOLS:
                            n_read += 1
                            p = first_path(inp)
                            if p:
                                read_paths.append(p)
                        elif name in SEARCH_TOOLS:
                            n_search += 1
                            p = first_path(inp)
                            if p:
                                read_paths.append(p)
                        elif name in WRITE_TOOLS:
                            n_write += 1
                            if first_edit_turn is None:
                                first_edit_turn = turns
                        elif name in SHELL_TOOLS:
                            n_shell += 1
                            cmd = inp.get("command") or ""
                            if VERIFY_RE.search(cmd):
                                n_verify += 1
                        elif name in DELEG_TOOLS:
                            n_deleg += 1
                elif t == "user":
                    for b in iter_blocks(msg):
                        if b.get("type") != "tool_result":
                            continue
                        L = result_len(b)
                        res_chars_all += L
                        tn = id2tool.get(b.get("tool_use_id"))
                        if tn in READ_TOOLS or tn in SEARCH_TOOLS:
                            res_chars_read += L

        if turns == 0:
            continue
        keys = [os.path.normpath(p).lower() for p in read_paths]
        uniq = len(set(keys))
        rereads = len(keys) - uniq
        mods = [module_of(p) for p in read_paths]
        mods = [m for m in mods if m]
        for m in mods:
            mod_counter[m] += 1
        crossings = sum(1 for a, b in zip(mods, mods[1:]) if a != b)
        drives = {m.split(":")[0] for m in mods}
        rows.append(dict(
            session=hash_id(sid), turns=turns, reads=n_read, searches=n_search,
            writes=n_write, shells=n_shell, delegations=n_deleg,
            verify_runs=n_verify,
            read_events=len(keys), unique_files=uniq, rereads=rereads,
            reread_rate=round(rereads / len(keys), 4) if keys else 0.0,
            modules_touched=len(set(mods)), boundary_crossings=crossings,
            crossing_rate=round(crossings / (len(mods) - 1), 4) if len(mods) > 1 else 0.0,
            drives_touched=len(drives),
            time_to_first_edit_turns=first_edit_turn if first_edit_turn else "",
            explore_chars=res_chars_read, all_result_chars=res_chars_all,
            explore_char_share=round(res_chars_read / res_chars_all, 4) if res_chars_all else 0.0,
            out_tokens=tok_out, cache_create_tokens=tok_cache_create,
            cache_read_tokens=tok_cache_read,
            # Release form: the cwds count and git branch names are not emitted.
            versions=len(vers), sidechain_events=sidechain,
        ))

    rows.sort(key=lambda r: -r["turns"])
    cols = list(rows[0].keys())
    with open(os.path.join(OUT, "cac_pilot_sessions.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    def agg(k):
        v = [r[k] for r in rows if isinstance(r[k], (int, float))]
        return sum(v)

    tot_read_ev = agg("read_events")
    tot_rr = agg("rereads")
    tot_mods = agg("boundary_crossings")
    n_edit = [r for r in rows if r["time_to_first_edit_turns"] != ""]
    ttfe = sorted(r["time_to_first_edit_turns"] for r in n_edit)
    summary = dict(
        source="<transcript_root>",
        transcript_files=len(files), sessions_with_turns=len(rows),
        total_lines=total_lines, json_parse_errors=parse_err,
        window_utc=[ts_min, ts_max],
        total_turns=agg("turns"),
        total_read_events=tot_read_ev,
        total_unique_files=agg("unique_files"),
        global_reread_rate=round(tot_rr / tot_read_ev, 4) if tot_read_ev else 0,
        total_boundary_crossings=tot_mods,
        global_crossing_rate=round(tot_mods / tot_read_ev, 4) if tot_read_ev else 0,
        explore_char_share_global=round(agg("explore_chars") / agg("all_result_chars"), 4) if agg("all_result_chars") else 0,
        total_output_tokens=agg("out_tokens"),
        total_cache_creation_tokens=agg("cache_create_tokens"),
        total_cache_read_tokens=agg("cache_read_tokens"),
        sessions_with_any_edit=len(n_edit),
        ttfe_median_turns=ttfe[len(ttfe) // 2] if ttfe else None,
        total_verify_runs=agg("verify_runs"),
        sidechain_events_total=agg("sidechain_events"),
        multi_drive_sessions=sum(1 for r in rows if r["drives_touched"] > 1),
        tool_counts=dict(tool_counter.most_common(30)),
        # Release form: module names are replaced by rank labels (module_01 = most frequent).
        top_modules={f"module_{i:02d}": c for i, (_, c) in enumerate(mod_counter.most_common(30), 1)},
        cli_versions=len(ver_seen),
    )
    with open(os.path.join(OUT, "cac_pilot_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
