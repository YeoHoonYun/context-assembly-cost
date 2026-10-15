"""S06 - Canonical CAC computation (lineage merge + shell path resolution + FDR).

T2-1 session-fork dedup : union-find over shared tool_use_id (method reused from an earlier
                          extraction of an overlapping window). When a session is resumed or
                          forked, the earlier history is copied into the new sessionId, so the
                          lineage is the correct unit of analysis. Tokens and turns are also
                          deduplicated on (lineage, uuid).
T2-2 shell path extraction: paths are taken not only from Read/Grep/Glob arguments but also from
                          PowerShell/Bash commands (mutation verbs / redirects / absolute paths).
                          Two regimes are computed for sensitivity: TOOL-only and TOOL+SHELL.
T2-3 FDR correction     : Benjamini-Hochberg. All correlations and group comparisons of one regime
                          form one family.

Output (in --results-dir):
      final_lineage_cac.csv     lineage-level primitives, TOOL+SHELL ('both') regime only
                                (lineage = salted hash of the lineage's root session id)
      final_measurements.json   canonical numbers cited in the paper
      final_associations.json   test results after FDR correction (both regimes)

Principle: zero file content exported (paths / tool names / character counts / token counts only).

Usage: python s06_lineage_cac_final.py --transcript-root <dir> --config config.json --results-dir <dir>
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

READ_TOOLS = {"Read", "NotebookRead"}
SEARCH_TOOLS = {"Grep", "Glob"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
DELEG_TOOLS = {"Agent", "Task", "Skill", "Workflow"}
PATH_KEYS = ("file_path", "notebook_path", "path", "filePath")

# shell-classification regexes (same family as the earlier extraction)
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
    """returns (is_mutation, [paths]). A mutation counts as an edit, otherwise as an acquisition."""
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


# ---------------- statistics ----------------
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
        return {"skipped": f"n_a={len(a)}, n_b={len(b)}"}
    r = ranks(a + b)
    na, nb = len(a), len(b)
    U = sum(r[:na]) - na * (na + 1) / 2.0
    sd = math.sqrt(na * nb * (na + nb + 1) / 12.0)
    z = (U - na * nb / 2.0) / sd if sd else 0.0
    return {"n_a": na, "n_b": nb, "median_a": round(_med(a), 1), "median_b": round(_med(b), 1),
            "z": round(z, 3), "p_raw": 2 * (1 - _phi(abs(z))),
            "rank_biserial": round(2 * U / (na * nb) - 1, 4)}


def bh_fdr(items, alpha=0.05):
    """items: list of (label, dict_with_p_raw). Fills in the BH q value and the significance flag."""
    live = [(lb, d) for lb, d in items if isinstance(d, dict) and "p_raw" in d]
    m = len(live)
    live.sort(key=lambda x: x[1]["p_raw"])
    prev = 1.0
    for i in range(m - 1, -1, -1):
        lb, d = live[i]
        q = min(prev, d["p_raw"] * m / (i + 1))
        d["q_bh"] = round(q, 6)
        d["sig_fdr05"] = q < alpha
        d["p_raw"] = round(d["p_raw"], 6)
        prev = q
    return m


# ---------------- 1) extraction ----------------
events = []          # file-access events
msgs = []            # assistant messages (tokens / turns)
tc_sessions = defaultdict(set)
res_chars = {}       # tool_use_id -> result character count
tool_of = {}         # tool_use_id -> tool name
parse_err = 0
lines_total = 0

files = [f for f in os.listdir(SRC) if f.endswith(".jsonl")]
for fn in files:
    sid = fn[:-6]
    with open(os.path.join(SRC, fn), encoding="utf-8", errors="replace") as f:
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
            t = parse_ts(o.get("timestamp"))
            if o.get("type") == "assistant":
                u = (o.get("message") or {}).get("usage") or {}
                msgs.append({"sid": sid, "uuid": o.get("uuid"), "t": t,
                             "out": u.get("output_tokens") or 0,
                             "cc": u.get("cache_creation_input_tokens") or 0,
                             "cr": u.get("cache_read_input_tokens") or 0})
                for b in blocks(o.get("message")):
                    if b.get("type") != "tool_use":
                        continue
                    tid = b.get("id")
                    name = b.get("name") or "?"
                    tool_of[tid] = name
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
                                           "src": "tool", "path": p})
                    elif name in WRITE_TOOLS:
                        events.append({"sid": sid, "tid": tid, "t": t, "kind": "EDIT",
                                       "src": "tool", "path": None})
                    elif name in SHELL_TOOLS:
                        cmd = inp.get("command") or ""
                        mut, ps = shell_paths(cmd)
                        if VERIFY_RE.search(cmd):
                            events.append({"sid": sid, "tid": tid, "t": t, "kind": "VERIFY",
                                           "src": "shell", "path": None})
                        if mut:
                            events.append({"sid": sid, "tid": tid, "t": t, "kind": "EDIT",
                                           "src": "shell", "path": None})
                        for i2, p in enumerate(ps):
                            events.append({"sid": sid, "tid": f"{tid}#{i2}", "t": t,
                                           "kind": "EDIT_P" if mut else "ACQ",
                                           "src": "shell", "path": p})
                    elif name in DELEG_TOOLS:
                        events.append({"sid": sid, "tid": tid, "t": t, "kind": "DELEG",
                                       "src": "tool", "path": None})
            elif o.get("type") == "user":
                for b in blocks(o.get("message")):
                    if b.get("type") != "tool_result":
                        continue
                    c = b.get("content")
                    L = 0
                    if isinstance(c, str):
                        L = len(c)
                    elif isinstance(c, list):
                        for x in c:
                            if isinstance(x, dict) and x.get("type") == "text":
                                L += len(x.get("text") or "")
                    tid = b.get("tool_use_id")
                    if tid:
                        res_chars[tid] = max(res_chars.get(tid, 0), L)


# ---------------- 2) union-find ----------------
class UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


uf = UF()
for fn in files:
    uf.find(fn[:-6])
for tid, sids in tc_sessions.items():
    s = sorted(sids)
    for x in s[1:]:
        uf.union(s[0], x)
lin_of = {s: uf.find(s) for s in uf.p}
lineages = defaultdict(set)
for s, l in lin_of.items():
    lineages[l].add(s)

# ---------------- 3) dedup ----------------
seen_ev, ev = set(), []
for e in events:
    l = lin_of[e["sid"]]
    k = (l, e["tid"], e["kind"])
    if k in seen_ev:
        continue
    seen_ev.add(k)
    e["lin"] = l
    ev.append(e)

seen_m, mm = set(), []
for m in msgs:
    l = lin_of[m["sid"]]
    k = (l, m["uuid"])
    if k in seen_m:
        continue
    seen_m.add(k)
    m["lin"] = l
    mm.append(m)

DEDUP = {
    "raw_path_and_action_events": len(events),
    "events_after_lineage_dedup": len(ev),
    "event_fork_replay_rate_pct": round(100.0 * (len(events) - len(ev)) / len(events), 2) if events else 0,
    "raw_assistant_messages": len(msgs),
    "messages_after_lineage_dedup": len(mm),
    "message_fork_replay_rate_pct": round(100.0 * (len(msgs) - len(mm)) / len(msgs), 2) if msgs else 0,
    "distinct_session_ids": len(lin_of),
    "distinct_lineages": len(lineages),
    "largest_lineage_sessions": max(len(v) for v in lineages.values()),
    "lineage_size_distribution": dict(sorted(Counter(len(v) for v in lineages.values()).items())),
}

# ---------------- 4) lineage-level CAC ----------------
by_lin_ev = defaultdict(list)
for e in ev:
    by_lin_ev[e["lin"]].append(e)
by_lin_msg = defaultdict(list)
for m in mm:
    by_lin_msg[m["lin"]].append(m)


def build(regime):
    """regime: 'tool' | 'both'"""
    rows = []
    for lin, evs in by_lin_ev.items():
        evs = sorted(evs, key=lambda x: (x["t"] or datetime.min.replace(tzinfo=timezone.utc)))
        ms = sorted(by_lin_msg.get(lin, []),
                    key=lambda x: (x["t"] or datetime.min.replace(tzinfo=timezone.utc)))
        turns = len(ms)
        if turns == 0:
            continue
        acq = [e for e in evs if e["kind"] == "ACQ" and (regime == "both" or e["src"] == "tool")]
        edits = [e for e in evs if e["kind"] in ("EDIT", "EDIT_P")]
        verify = sum(1 for e in evs if e["kind"] == "VERIFY")
        deleg = sum(1 for e in evs if e["kind"] == "DELEG")
        paths = [e["path"] for e in acq if e["path"]]
        keys = [key(p) for p in paths]
        mods = [module_of(p) for p in paths]
        uniq = len(set(keys))
        cross = sum(1 for a, b in zip(mods, mods[1:]) if a != b)
        codi = sum(1 for p in paths if is_codified(p))
        chars = sum(res_chars.get(e["tid"], 0) for e in acq)
        # turns until the first edit
        ttfe = ""
        if edits and ms:
            t0 = min(e["t"] for e in edits if e["t"])
            ttfe = sum(1 for m in ms if m["t"] and m["t"] <= t0)
        rows.append(dict(
            lineage=lin, sessions=len(lineages[lin]), turns=turns,
            acq_events=len(acq), unique_files=uniq, rereads=len(keys) - uniq,
            reread_rate=round((len(keys) - uniq) / len(keys), 4) if keys else 0.0,
            modules=len(set(mods)), crossings=cross,
            crossing_rate=round(cross / (len(mods) - 1), 4) if len(mods) > 1 else 0.0,
            edits=len(edits), verify=verify, delegations=deleg,
            codified_reads=codi,
            explore_chars=chars,
            explore_per_edit=round(chars / (len(edits) + 1), 1),
            ttfe=ttfe,
            out_tok=sum(m["out"] for m in ms), cc_tok=sum(m["cc"] for m in ms),
            cr_tok=sum(m["cr"] for m in ms)))
    return rows


R = {"tool": build("tool"), "both": build("both")}

# Only the 'both' (TOOL+SHELL) regime is written per lineage; the 'tool' regime is kept in
# memory and reported as aggregates and test statistics only.
with open(os.path.join(RES, "final_lineage_cac.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(R["both"][0].keys()))
    w.writeheader(); w.writerows([{**r, "lineage": hash_id(r["lineage"])} for r in R["both"]])


# ---------------- 5) canonical numbers + tests ----------------
def summarize(rows):
    tk = sum(r["acq_events"] for r in rows)
    uq = sum(r["unique_files"] for r in rows)
    rr = sum(r["rereads"] for r in rows)
    cr = sum(r["crossings"] for r in rows)
    tt = [r["ttfe"] for r in rows if r["ttfe"] != ""]
    return {
        "lineages": len(rows),
        "total_turns": sum(r["turns"] for r in rows),
        "acq_events": tk, "unique_files_sum": uq,
        "reread_rate_global": round(rr / tk, 4) if tk else 0,
        "boundary_crossings": cr,
        "crossing_rate_global": round(cr / tk, 4) if tk else 0,
        "verify_runs": sum(r["verify"] for r in rows),
        "lineages_with_edit": len(tt),
        "ttfe_median": _med(tt) if tt else None,
        "explore_chars": sum(r["explore_chars"] for r in rows),
        "codified_read_events": sum(r["codified_reads"] for r in rows),
    }


MEAS = {"generated_utc": datetime.now(timezone.utc).isoformat(),
        "transcript_files": len(files), "lines": lines_total, "json_parse_errors": parse_err,
        "dedup": DEDUP,
        "regime_tool_only": summarize(R["tool"]),
        "regime_tool_plus_shell": summarize(R["both"])}

TESTS = {}
for reg in ("tool", "both"):
    rows = [r for r in R[reg] if r["acq_events"] >= 5]
    TESTS[reg] = {"n_subset": len(rows), "subset_rule": "acq_events >= 5"}
    fam = []
    for xn, xf in [("unique_files", lambda r: float(r["unique_files"])),
                   ("modules_touched", lambda r: float(r["modules"])),
                   ("boundary_crossings", lambda r: float(r["crossings"])),
                   ("crossing_rate", lambda r: r["crossing_rate"]),
                   ("reread_rate", lambda r: r["reread_rate"])]:
        for yn, yf in [("explore_chars_per_edit", lambda r: r["explore_per_edit"]),
                       ("turns", lambda r: float(r["turns"])),
                       ("cache_read_tokens", lambda r: float(r["cr_tok"])),
                       ("verify_runs", lambda r: float(r["verify"]))]:
            d = spearman([xf(r) for r in rows], [yf(r) for r in rows])
            if d:
                TESTS[reg][f"{xn} ~ {yn}"] = d
                fam.append((f"{xn} ~ {yn}", d))
        sub = [r for r in rows if r["ttfe"] != ""]
        d = spearman([xf(r) for r in sub], [float(r["ttfe"]) for r in sub])
        if d:
            TESTS[reg][f"{xn} ~ time_to_first_edit"] = d
            fam.append((f"{xn} ~ time_to_first_edit", d))
    # delegation
    dy = [r for r in rows if r["delegations"] > 0]
    dn = [r for r in rows if r["delegations"] == 0]
    d = mannwhitney([r["explore_per_edit"] for r in dn], [r["explore_per_edit"] for r in dy])
    TESTS[reg]["delegation: explore_per_edit (no vs yes)"] = d
    if "p_raw" in d:
        fam.append(("delegation_explore_per_edit", d))
    d2 = mannwhitney([r["crossing_rate"] for r in dn], [r["crossing_rate"] for r in dy])
    TESTS[reg]["delegation: crossing_rate (no vs yes)"] = d2
    if "p_raw" in d2:
        fam.append(("delegation_crossing_rate", d2))
    # codified context
    cy = [r for r in rows if r["codified_reads"] > 0]
    cn = [r for r in rows if r["codified_reads"] == 0]
    d3 = mannwhitney([r["explore_per_edit"] for r in cn], [r["explore_per_edit"] for r in cy])
    TESTS[reg]["codified: explore_per_edit (no vs yes)"] = d3
    if "p_raw" in d3:
        fam.append(("codified_explore_per_edit", d3))
    TESTS[reg]["_fdr_family_size"] = bh_fdr(fam)
    TESTS[reg]["_fdr_survivors"] = sorted(
        [lb for lb, d in fam if d.get("sig_fdr05")],
        key=lambda lb: dict(fam)[lb]["q_bh"])

json.dump(MEAS, open(os.path.join(RES, "final_measurements.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
json.dump(TESTS, open(os.path.join(RES, "final_associations.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print(json.dumps(MEAS, ensure_ascii=False, indent=2))
print("\n================ ASSOCIATIONS (BH-FDR) ================")
print(json.dumps(TESTS, ensure_ascii=False, indent=2))
