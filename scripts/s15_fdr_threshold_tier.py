"""S15 - Enumeration of the 28-test family, acquisition-threshold sensitivity, path-confidence tiers.

Input (all in --results-dir unless stated)
  (exact)          final_lineage_cac.csv   - s06 canonical snapshot (2026-09-04T01:19:25Z), TOOL+SHELL regime
  (exact)          final_associations.json - s06 canonical test results
  (reconstructed)  raw transcripts (--transcript-root) - only those of the 339 sessions listed in
                   pilot_sessions.csv that still exist, and only lines with timestamp <= the canonical
                   generation time. The tool-only regime and the path tiers are not in the CSV and are
                   therefore reconstructed.
  pilot_sessions.csv / factor_labels.csv - s01 / s04 session tables (session ids are salted hashes;
                   transcripts are matched by hashing their file names with the same salt)

Path-confidence tiers
  T1 tool-argument path      file_path / path argument of Read/NotebookRead/Grep/Glob (structured field)
  T2 shell path, exists      extracted from PowerShell/Bash commands with the s06 regex, exists on disk now
  T3 shell path, absent      same extraction, absent from disk now
  Existence is judged against the disk at run time; relative paths are resolved against the cwd of
  the transcript line (config default_cwd when the line has none).

Output (in --results-dir): s15_fdr_threshold_tier.json, s15_threshold_grid.csv

Usage: python s15_fdr_threshold_tier.py --transcript-root <dir> --config config.json --results-dir <dir>
Requires env CAC_SALT.
"""
import json, os, re, csv, math, sys
from collections import defaultdict, Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cac_common import setup, hash_id  # noqa: E402

ARGS, CFG = setup(__doc__, need_config=True)
SRC = ARGS.transcript_root
RES = ARGS.results_dir
CUTOFF = datetime.fromisoformat("2026-09-04T01:19:25.071874+00:00")
THRESHOLDS = [1, 2, 3, 5, 10, 20]
ALPHAS = [0.01, 0.05, 0.10]

# ---- same definitions as s06 ----
READ_TOOLS = {"Read", "NotebookRead"}
SEARCH_TOOLS = {"Grep", "Glob"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
DELEG_TOOLS = {"Agent", "Task", "Skill", "Workflow"}
PATH_KEYS = ("file_path", "notebook_path", "path", "filePath")
MUT_VERBS = re.compile(
    r"(?<![\w-])("
    r"Set-Content|Add-Content|Out-File|New-Item|Remove-Item|Move-Item|Copy-Item|"
    r"Rename-Item|Clear-Content|robocopy|xcopy|"
    r"rm|mv|cp|touch|mkdir|rmdir|dd|truncate|install|"
    r"tee|sed\s+-i|perl\s+-i"
    r")(?![\w-])", re.IGNORECASE)
REDIRECT = re.compile(r"(?<![0-9>])>>?(?!\s*&)\s*([\"']?)([A-Za-z]:\\[^\"'\s|;)]+|/[^\"'\s|;)]+|[\w.\\/-]+\.\w+)\1")
ABS_PATH = re.compile(r"([A-Za-z]:\\[^\"'\s|;,)]+)")
REL_FILE = re.compile(r"(?<![\w\\/:.])([\w.\-]+[\\/][\w.\\/-]*\.\w{1,6}|[\w.\-]+\.(?:py|md|json|jsonl|csv|ps1|bat|txt|ya?ml|log))\b")
VERIFY_RE = re.compile(CFG["verify_regex"], re.I)
DRIVE_RE = re.compile(r"^([A-Za-z]):")
PROJECT_DRIVE = CFG["project_drive"].upper()
REPO = CFG["project_repo_dirname"].lower()
TWO_LEVEL = tuple(x.lower() for x in CFG["two_level_dirs"])
DEFAULT_CWD = CFG["default_cwd"]


def norm(p):
    if not p:
        return None
    p = p.strip().strip('"').strip("'").replace("/", "\\")
    return p if len(p) > 3 else None


def key(p):
    return os.path.normpath(p).lower().rstrip("\\")


def module_of(p):
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


def is_codified(p):
    q = p.lower()
    return ("claude.md" in q or "\\_manifests\\" in q or "\\.claude\\" in q
            or "\\memory\\" in q or q.endswith("skill.md") or "agents.md" in q)


def shell_paths(cmd):
    if not cmd:
        return False, []
    mut = bool(MUT_VERBS.search(cmd) or REDIRECT.search(cmd))
    ps = [m.group(1) for m in ABS_PATH.finditer(cmd)]
    ps += [m.group(1) for m in REL_FILE.finditer(cmd)]
    if mut:
        ps += [m.group(2) for m in REDIRECT.finditer(cmd)]
    out, seen = [], set()
    for p in ps:
        p = norm(p)
        if not p:
            continue
        k = key(p)
        if k not in seen:
            seen.add(k)
            out.append(p)
    return mut, out


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


def ranks(xs):
    idx = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and xs[idx[j + 1]] == xs[idx[i]]:
            j += 1
        for k2 in range(i, j + 1):
            r[idx[k2]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return r


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _med(v):
    s = sorted(v); n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def spearman(xs, ys):
    n = len(xs)
    if n < 8:
        return None
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if not dx or not dy:
        return None
    rho = num / (dx * dy)
    z = rho * math.sqrt(n - 1)
    return {"rho": round(rho, 4), "n": n, "p_raw": 2 * (1 - _phi(abs(z)))}


def mannwhitney(a, b):
    if len(a) < 5 or len(b) < 5:
        return None
    r = ranks(a + b)
    na, nb = len(a), len(b)
    U = sum(r[:na]) - na * (na + 1) / 2.0
    sd = math.sqrt(na * nb * (na + nb + 1) / 12.0)
    z = (U - na * nb / 2.0) / sd if sd else 0.0
    return {"n_a": na, "n_b": nb, "median_a": round(_med(a), 1), "median_b": round(_med(b), 1),
            "z": round(z, 3), "p_raw": 2 * (1 - _phi(abs(z))),
            "rank_biserial": round(2 * U / (na * nb) - 1, 4)}


def bh(ps):
    """ps: list of p. returns q list (same order)."""
    m = len(ps)
    order = sorted(range(m), key=lambda i: ps[i])
    q = [1.0] * m
    prev = 1.0
    for rank in range(m - 1, -1, -1):
        i = order[rank]
        prev = min(prev, ps[i] * m / (rank + 1))
        q[i] = prev
    return q


# ---- family definition (same order as s06) ----
X_DEF = [("unique_files", "unique_files"), ("modules_touched", "modules"),
         ("boundary_crossings", "crossings"), ("crossing_rate", "crossing_rate"),
         ("reread_rate", "reread_rate")]
Y_DEF = [("explore_chars_per_edit", "explore_per_edit"), ("turns", "turns"),
         ("cache_read_tokens", "cr_tok"), ("verify_runs", "verify")]


def run_family(rows, thr):
    rows = [r for r in rows if r["acq_events"] >= thr]
    fam = []
    for xn, xc in X_DEF:
        for yn, yc in Y_DEF:
            d = spearman([float(r[xc]) for r in rows], [float(r[yc]) for r in rows])
            fam.append((f"{xn} ~ {yn}", "spearman", d))
        sub = [r for r in rows if r["ttfe"] != ""]
        d = spearman([float(r[xc]) for r in sub], [float(r["ttfe"]) for r in sub])
        fam.append((f"{xn} ~ time_to_first_edit", "spearman", d))
    dy = [r for r in rows if r["delegations"] > 0]
    dn = [r for r in rows if r["delegations"] == 0]
    fam.append(("delegation_explore_per_edit", "mann_whitney",
                mannwhitney([r["explore_per_edit"] for r in dn], [r["explore_per_edit"] for r in dy])))
    fam.append(("delegation_crossing_rate", "mann_whitney",
                mannwhitney([r["crossing_rate"] for r in dn], [r["crossing_rate"] for r in dy])))
    cy = [r for r in rows if r["codified_reads"] > 0]
    cn = [r for r in rows if r["codified_reads"] == 0]
    fam.append(("codified_explore_per_edit", "mann_whitney",
                mannwhitney([r["explore_per_edit"] for r in cn], [r["explore_per_edit"] for r in cy])))
    live = [(lb, d) for lb, _, d in fam if d]
    qs = bh([d["p_raw"] for _, d in live])
    out = {"threshold": thr, "n_subset": len(rows), "family_size": len(live),
           "dropped_tests": [lb for lb, _, d in fam if not d], "tests": {}}
    for (lb, d), q in zip(live, qs):
        e = dict(d); e["p_raw"] = round(d["p_raw"], 6); e["q_bh"] = round(q, 6)
        out["tests"][lb] = e
    for a in ALPHAS:
        out[f"survivors_q<{a}"] = sorted(lb for lb, e in out["tests"].items() if e["q_bh"] < a)
    return out


def canon_label(lb):
    return (lb.replace("delegation: explore_per_edit (no vs yes)", "delegation_explore_per_edit")
              .replace("delegation: crossing_rate (no vs yes)", "delegation_crossing_rate")
              .replace("codified: explore_per_edit (no vs yes)", "codified_explore_per_edit"))


def load_csv_rows():
    rows = []
    with open(os.path.join(RES, "final_lineage_cac.csv"), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            for c in ("turns", "acq_events", "unique_files", "modules", "crossings", "verify",
                      "delegations", "codified_reads", "cr_tok"):
                r[c] = int(r[c])
            for c in ("reread_rate", "crossing_rate", "explore_per_edit"):
                r[c] = float(r[c])
            r["ttfe"] = r["ttfe"] if r["ttfe"] == "" else int(r["ttfe"])
            rows.append(r)
    return rows


# ================= 1) canonical family enumeration =================
CANON = json.load(open(os.path.join(RES, "final_associations.json"), encoding="utf-8"))
ENUM = []
i = 0
for xn, _ in X_DEF:
    for yn in [y for y, _ in Y_DEF] + ["time_to_first_edit"]:
        i += 1
        lb = f"{xn} ~ {yn}"
        ENUM.append({"id": f"F{i:02d}", "label": lb, "test": "spearman", "x": xn, "y": yn})
for lb, x, y in [("delegation: explore_per_edit (no vs yes)", "delegations>0", "explore_chars_per_edit"),
                 ("delegation: crossing_rate (no vs yes)", "delegations>0", "crossing_rate"),
                 ("codified: explore_per_edit (no vs yes)", "codified_reads>0", "explore_chars_per_edit")]:
    i += 1
    ENUM.append({"id": f"F{i:02d}", "label": lb, "test": "mann_whitney", "x": x, "y": y})
for e in ENUM:
    for reg in ("tool", "both"):
        d = CANON[reg].get(e["label"], {})
        e[reg] = {k: d.get(k) for k in ("rho", "z", "rank_biserial", "n", "n_a", "n_b",
                                          "p_raw", "q_bh", "sig_fdr05") if k in d}

# ================= 2) TOOL+SHELL threshold grid from the canonical CSV (exact) =================
csv_rows = load_csv_rows()
GRID_EXACT = {thr: run_family(csv_rows, thr) for thr in THRESHOLDS}
chk = GRID_EXACT[5]["tests"]
mism = []
for lb, d in CANON["both"].items():
    if lb.startswith("_") or not isinstance(d, dict) or "p_raw" not in d:
        continue
    c = chk.get(canon_label(lb))
    if not c or abs(c["q_bh"] - d["q_bh"]) > 1e-5 or (c.get("rho") != d.get("rho") and "rho" in d):
        mism.append(lb)
REPRO_EXACT = {"threshold5_matches_canonical_both": not mism, "mismatched_labels": mism}

# ================= 3) transcript reconstruction (tool-only and tiers) =================
sess_ids = []
with open(os.path.join(RES, "pilot_sessions.csv"), encoding="utf-8-sig") as f:
    sess_ids = [r["session"] for r in csv.DictReader(f)]
# session ids in pilot_sessions.csv are salted hashes; map them back to transcript files present now
_raw_of = {hash_id(fn[:-6]): fn[:-6] for fn in os.listdir(SRC) if fn.endswith(".jsonl")}
present = [_raw_of[s] for s in sess_ids if s in _raw_of]
missing = [s for s in sess_ids if s not in _raw_of]

events, msgs = [], []
tc_sessions = defaultdict(set)
res_chars = {}
lines_used = lines_after_cutoff = 0
for sid in present:
    with open(os.path.join(SRC, sid + ".jsonl"), encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = parse_ts(o.get("timestamp"))
            if t and t > CUTOFF:
                lines_after_cutoff += 1
                continue
            lines_used += 1
            cwd = o.get("cwd") or ""
            if o.get("type") == "assistant":
                u = (o.get("message") or {}).get("usage") or {}
                msgs.append({"sid": sid, "uuid": o.get("uuid"), "t": t,
                             "cr": u.get("cache_read_input_tokens") or 0})
                for b in blocks(o.get("message")):
                    if b.get("type") != "tool_use":
                        continue
                    tid = b.get("id"); name = b.get("name") or "?"
                    if tid:
                        tc_sessions[tid].add(sid)
                    inp = b.get("input") or {}
                    if name in READ_TOOLS or name in SEARCH_TOOLS:
                        p = None
                        for k2 in PATH_KEYS:
                            v = inp.get(k2)
                            if isinstance(v, str) and len(v) > 2:
                                p = norm(v); break
                        if p:
                            events.append({"sid": sid, "tid": tid, "t": t, "kind": "ACQ",
                                           "src": "tool", "path": p, "cwd": cwd})
                    elif name in WRITE_TOOLS:
                        events.append({"sid": sid, "tid": tid, "t": t, "kind": "EDIT", "src": "tool", "path": None})
                    elif name in SHELL_TOOLS:
                        cmd = inp.get("command") or ""
                        mut, ps = shell_paths(cmd)
                        if VERIFY_RE.search(cmd):
                            events.append({"sid": sid, "tid": tid, "t": t, "kind": "VERIFY", "src": "shell", "path": None})
                        if mut:
                            events.append({"sid": sid, "tid": tid, "t": t, "kind": "EDIT", "src": "shell", "path": None})
                        for i2, p in enumerate(ps):
                            events.append({"sid": sid, "tid": f"{tid}#{i2}", "t": t,
                                           "kind": "EDIT_P" if mut else "ACQ",
                                           "src": "shell", "path": p, "cwd": cwd})
                    elif name in DELEG_TOOLS:
                        events.append({"sid": sid, "tid": tid, "t": t, "kind": "DELEG", "src": "tool", "path": None})
            elif o.get("type") == "user":
                for b in blocks(o.get("message")):
                    if b.get("type") != "tool_result":
                        continue
                    c = b.get("content"); L = 0
                    if isinstance(c, str):
                        L = len(c)
                    elif isinstance(c, list):
                        for x in c:
                            if isinstance(x, dict) and x.get("type") == "text":
                                L += len(x.get("text") or "")
                    tid = b.get("tool_use_id")
                    if tid:
                        res_chars[tid] = max(res_chars.get(tid, 0), L)

parent = {s: s for s in present}


def find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


for tid, sids in tc_sessions.items():
    s = sorted(sids)
    for x in s[1:]:
        ra, rb = find(s[0]), find(x)
        if ra != rb:
            parent[rb] = ra
lin_of = {s: find(s) for s in present}

seen, ev = set(), []
for e in events:
    k = (lin_of[e["sid"]], e["tid"], e["kind"])
    if k in seen:
        continue
    seen.add(k); e["lin"] = lin_of[e["sid"]]; ev.append(e)
seen, mm = set(), []
for m in msgs:
    k = (lin_of[m["sid"]], m["uuid"])
    if k in seen:
        continue
    seen.add(k); m["lin"] = lin_of[m["sid"]]; mm.append(m)

# existence check (disk at run time)
exist_cache = {}


def exists(p, cwd):
    q = p
    if not DRIVE_RE.match(q) and not q.startswith("\\"):
        q = os.path.join(cwd or DEFAULT_CWD, q)
    k = key(q)
    if k not in exist_cache:
        try:
            exist_cache[k] = os.path.exists(q)
        except Exception:
            exist_cache[k] = False
    return exist_cache[k]


for e in ev:
    if e["kind"] == "ACQ":
        if e["src"] == "tool":
            e["tier"] = "T1"
        else:
            e["tier"] = "T2" if exists(e["path"], e.get("cwd")) else "T3"

by_ev = defaultdict(list)
for e in ev:
    by_ev[e["lin"]].append(e)
by_msg = defaultdict(list)
for m in mm:
    by_msg[m["lin"]].append(m)
MIN_T = datetime.min.replace(tzinfo=timezone.utc)


def build(tiers):
    rows = []
    for lin, evs in by_ev.items():
        evs = sorted(evs, key=lambda x: x["t"] or MIN_T)
        ms = sorted(by_msg.get(lin, []), key=lambda x: x["t"] or MIN_T)
        if not ms:
            continue
        acq = [e for e in evs if e["kind"] == "ACQ" and e["tier"] in tiers]
        edits = [e for e in evs if e["kind"] in ("EDIT", "EDIT_P")]
        paths = [e["path"] for e in acq]
        keys = [key(p) for p in paths]
        mods = [module_of(p) for p in paths]
        uniq = len(set(keys)); cross = sum(1 for a, b in zip(mods, mods[1:]) if a != b)
        chars = sum(res_chars.get(e["tid"], 0) for e in acq)
        ttfe = ""
        if edits:
            t0 = min((e["t"] for e in edits if e["t"]), default=None)
            if t0:
                ttfe = sum(1 for m in ms if m["t"] and m["t"] <= t0)
        rows.append(dict(
            lineage=lin, turns=len(ms), acq_events=len(acq), unique_files=uniq,
            reread_rate=round((len(keys) - uniq) / len(keys), 4) if keys else 0.0,
            modules=len(set(mods)), crossings=cross,
            crossing_rate=round(cross / (len(mods) - 1), 4) if len(mods) > 1 else 0.0,
            verify=sum(1 for e in evs if e["kind"] == "VERIFY"),
            delegations=sum(1 for e in evs if e["kind"] == "DELEG"),
            codified_reads=sum(1 for p in paths if is_codified(p)),
            explore_per_edit=round(chars / (len(edits) + 1), 1), ttfe=ttfe,
            cr_tok=sum(m["cr"] for m in ms)))
    return rows


REGIMES = {"T1_tool": {"T1"}, "T1+T2_tool+shell_exist": {"T1", "T2"}, "T1+T2+T3_tool+shell_all": {"T1", "T2", "T3"}}
ROWS = {k: build(v) for k, v in REGIMES.items()}
GRID_RECON = {k: {thr: run_family(rows, thr) for thr in THRESHOLDS} for k, rows in ROWS.items()}


def recon_vs_canon(reg_recon, reg_canon):
    g = GRID_RECON[reg_recon][5]
    c = CANON[reg_canon]
    diffs = []
    for lb, d in c.items():
        if lb.startswith("_") or not isinstance(d, dict) or "p_raw" not in d:
            continue
        r = g["tests"].get(canon_label(lb))
        diffs.append({"label": canon_label(lb),
                      "canon_stat": d.get("rho", d.get("z")), "recon_stat": (r or {}).get("rho", (r or {}).get("z")),
                      "canon_q": d["q_bh"], "recon_q": (r or {}).get("q_bh"),
                      "canon_sig": d["sig_fdr05"], "recon_sig": (r["q_bh"] < 0.05) if r else None})
    return {"canon_n_subset": c["n_subset"], "recon_n_subset": g["n_subset"],
            "canon_survivors": len(c["_fdr_survivors"]), "recon_survivors": len(g["survivors_q<0.05"]),
            "sig_agreement": sum(1 for d in diffs if d["canon_sig"] == d["recon_sig"]),
            "tests_compared": len(diffs), "rows": diffs}


# tier sizes
tier_ev = Counter(e["tier"] for e in ev if e["kind"] == "ACQ")
tier_uniq = defaultdict(set)
for e in ev:
    if e["kind"] == "ACQ":
        tier_uniq[e["tier"]].add(key(e["path"]))
t1_exist = defaultdict(set)
for e in ev:
    if e["kind"] == "ACQ" and e["src"] == "tool":
        t1_exist[exists(e["path"], e.get("cwd"))].add(key(e["path"]))

TIERS = {
    "definition": {"T1": "tool-argument path (Read/NotebookRead/Grep/Glob)",
                   "T2": "extracted from shell command by regex; exists on disk at run time",
                   "T3": "extracted from shell command by regex; absent from disk at run time"},
    "acq_events_after_dedup": dict(tier_ev),
    "acq_event_share_pct": {k: round(100.0 * v / sum(tier_ev.values()), 2) for k, v in tier_ev.items()},
    "unique_paths": {k: len(v) for k, v in tier_uniq.items()},
    "T1_unique_paths_exist_now": len(t1_exist[True]),
    "T1_unique_paths_absent_now": len(t1_exist[False]),
    "T1_exist_rate_pct": round(100.0 * len(t1_exist[True]) / (len(t1_exist[True]) + len(t1_exist[False])), 2),
    "shell_unique_paths_exist_rate_pct": round(100.0 * len(tier_uniq["T2"]) / (len(tier_uniq["T2"]) + len(tier_uniq["T3"])), 2),
    "summary_by_regime": {k: {"lineages": len(r), "acq_events": sum(x["acq_events"] for x in r),
                              "unique_files_sum": sum(x["unique_files"] for x in r)} for k, r in ROWS.items()},
}

# ================= 4) s01 session-level tool-only grid (all 339 sessions kept; different definition) =================
fl = {}
with open(os.path.join(RES, "factor_labels.csv"), encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        fl[r["session"]] = int(r["codified_reads"])
s01_rows = []
with open(os.path.join(RES, "pilot_sessions.csv"), encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        tt = r["time_to_first_edit_turns"]
        s01_rows.append(dict(
            acq_events=int(r["read_events"]), unique_files=int(r["unique_files"]),
            modules=int(r["modules_touched"]), crossings=int(r["boundary_crossings"]),
            crossing_rate=float(r["crossing_rate"]), reread_rate=float(r["reread_rate"]),
            turns=int(r["turns"]), cr_tok=int(r["cache_read_tokens"]), verify=int(r["verify_runs"]),
            delegations=int(r["delegations"]), codified_reads=fl.get(r["session"], 0),
            explore_per_edit=int(r["explore_chars"]) / (int(r["writes"]) + 1),
            ttfe="" if tt == "" else int(tt)))
GRID_S01 = {thr: run_family(s01_rows, thr) for thr in THRESHOLDS}

OUT = {
    "generated_utc": datetime.now(timezone.utc).isoformat(),
    "threshold_grid_s01_session_tool_only": GRID_S01,
    "s01_note": ("Session unit (before lineage dedup); tool-argument paths only; writes exclude shell "
                 "edits; all 339 sessions. Definitions differ from the s06 canonical computation."),
    "canonical_snapshot_utc": CUTOFF.isoformat(),
    "family_enumeration": ENUM,
    "family_size_canonical": {"tool": CANON["tool"]["_fdr_family_size"], "both": CANON["both"]["_fdr_family_size"]},
    "threshold_grid_exact_both_from_csv": GRID_EXACT,
    "exact_reproduction_check": REPRO_EXACT,
    "reconstruction": {
        "sessions_in_canonical": len(sess_ids), "sessions_present_now": len(present),
        "sessions_missing_now": len(missing), "lines_used": lines_used,
        "lines_dropped_after_cutoff": lines_after_cutoff,
        "lineages": len(set(lin_of.values())),
        "tool_vs_canonical_tool_at_thr5": recon_vs_canon("T1_tool", "tool"),
        "all_vs_canonical_both_at_thr5": recon_vs_canon("T1+T2+T3_tool+shell_all", "both"),
    },
    "path_tiers": TIERS,
    "threshold_grid_reconstructed_by_tier": GRID_RECON,
}
json.dump(OUT, open(os.path.join(RES, "s15_fdr_threshold_tier.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

with open(os.path.join(RES, "s15_threshold_grid.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["source", "regime", "threshold", "n_subset", "family_size", "test", "stat", "n",
                "p_raw", "q_bh", "sig_q01", "sig_q05", "sig_q10"])
    def dump(source, regime, grid):
        for thr, g in grid.items():
            for lb, e in g["tests"].items():
                st = e.get("rho", e.get("rank_biserial"))
                w.writerow([source, regime, thr, g["n_subset"], g["family_size"], lb, st,
                            e.get("n", (e.get("n_a", 0) + e.get("n_b", 0))), e["p_raw"], e["q_bh"],
                            e["q_bh"] < 0.01, e["q_bh"] < 0.05, e["q_bh"] < 0.10])
    dump("exact_csv", "tool+shell", GRID_EXACT)
    dump("s01_session_csv", "tool_only_session_unit", GRID_S01)
    for k, grid in GRID_RECON.items():
        dump("reconstructed", k, grid)

print(json.dumps({"exact_repro": REPRO_EXACT,
                  "recon": {k: v for k, v in OUT["reconstruction"].items() if not isinstance(v, dict)},
                  "tool_recon": {k: v for k, v in OUT["reconstruction"]["tool_vs_canonical_tool_at_thr5"].items() if k != "rows"},
                  "both_recon": {k: v for k, v in OUT["reconstruction"]["all_vs_canonical_both_at_thr5"].items() if k != "rows"},
                  "tiers": TIERS,
                  "grid_exact": {t: (g["n_subset"], g["family_size"], len(g["survivors_q<0.01"]), len(g["survivors_q<0.05"]), len(g["survivors_q<0.1"])) for t, g in GRID_EXACT.items()},
                  "grid_s01": {t: (g["n_subset"], g["family_size"], len(g["survivors_q<0.01"]), len(g["survivors_q<0.05"]), len(g["survivors_q<0.1"])) for t, g in GRID_S01.items()},
                  "grid_recon": {k: {t: (g["n_subset"], g["family_size"], len(g["survivors_q<0.01"]), len(g["survivors_q<0.05"]), len(g["survivors_q<0.1"])) for t, g in grid.items()} for k, grid in GRID_RECON.items()},
                  }, ensure_ascii=False, indent=1))
