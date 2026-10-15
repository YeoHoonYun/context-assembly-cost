"""S08 (B3) - Matching analysis inside the pilot corpus: does the reversed codified-context
association survive control for task scope?

Background
  Pilot observation: lineages that read codified assets (CLAUDE.md/_manifests/.claude/memory)
  had 1.8-2.6x higher exploration cost per edit (q=0.015 / 0.0031).
  The manuscript explains this as **selection**: harder, broader tasks are the ones in which
  documentation gets read.

Question (B3)
  Does the reversed association **remain after controlling for task scope**?
  If it remains, the selection explanation is weakened; if it vanishes, it is strengthened.

Caution
  The pilot has no required scope (there are no designed tasks). Proxies are used:
  unique files / modules / turns. These are observed, not required, and therefore
  **possibly post-treatment variables** - this is stated with the results.

Three methods
  (1) stratification : codified vs plain within quartiles of unique files
  (2) regression     : log(explore cost per edit) ~ codified + log(unique files) + modules + log(turns)
  (3) nearest match  : 1:1 caliper matching on unique files, then within-pair comparison

Regime
  The numerator of explore_per_edit counts **tool-result characters** only. The analysis is
  therefore valid in the **tool-only regime** only; scope indicators are reported for both regimes.

Output (in --results-dir): b3_matching.json / b3_lineages.csv
  b3_lineages.csv holds the tool-only regime, lineages with acq_events >= 5 only, and a reduced
  column set (lineage = salted hash of the root session id).

Usage: python s08_b3_matching.py --transcript-root <dir> --config config.json --results-dir <dir>
Requires env CAC_SALT.
"""
import json, os, re, csv, math, sys
from collections import defaultdict
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
PATH_KEYS = ("file_path", "notebook_path", "path", "filePath")

MUT_VERBS = re.compile(
    r"(?<![\w-])(Set-Content|Add-Content|Out-File|New-Item|Remove-Item|Move-Item|Copy-Item|"
    r"Rename-Item|Clear-Content|robocopy|xcopy|rm|mv|cp|touch|mkdir|rmdir|dd|truncate|install|"
    r"tee|sed\s+-i|perl\s+-i)(?![\w-])", re.IGNORECASE)
REDIRECT = re.compile(r"(?<![0-9>])>>?(?!\s*&)\s*([\"']?)([A-Za-z]:\\[^\"'\s|;)]+|/[^\"'\s|;)]+|[\w.\\/-]+\.\w+)\1")
ABS_PATH = re.compile(r"([A-Za-z]:\\[^\"'\s|;,)]+)")
REL_FILE = re.compile(r"(?<![\w\\/:.])([\w.\-]+[\\/][\w.\\/-]*\.\w{1,6}|[\w.\-]+\.(?:py|md|json|jsonl|csv|ps1|bat|txt|ya?ml|log))\b")
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
    return f"{drive}:/{parts[1]}" if len(parts) >= 2 else f"{drive}:/"


def is_codified(p):
    q = p.lower()
    return ("claude.md" in q or "\\_manifests\\" in q or "\\.claude\\" in q
            or "\\memory\\" in q or q.endswith("skill.md") or "agents.md" in q)


def shell_paths(cmd):
    if not cmd:
        return False, []
    mut = bool(MUT_VERBS.search(cmd) or REDIRECT.search(cmd))
    ps = [m.group(1) for m in ABS_PATH.finditer(cmd)] + [m.group(1) for m in REL_FILE.finditer(cmd)]
    out, seen = [], set()
    for p in ps:
        p = norm(p)
        if p and key(p) not in seen:
            seen.add(key(p)); out.append(p)
    return mut, out


def blocks(m):
    c = (m or {}).get("content")
    if isinstance(c, list):
        for b in c:
            if isinstance(b, dict):
                yield b


def first_path(inp):
    for k in PATH_KEYS:
        v = inp.get(k)
        if isinstance(v, str) and len(v) > 2:
            return norm(v)
    return None


# ---------------- extraction ----------------
class UF:
    def __init__(self): self.p = {}
    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]; x = self.p[x]
        return x
    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb: self.p[rb] = ra


events, msgs, res_chars, tc_sessions = [], [], {}, defaultdict(set)
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
            if o.get("type") == "assistant":
                msgs.append((sid, o.get("uuid")))
                for b in blocks(o.get("message")):
                    if b.get("type") != "tool_use":
                        continue
                    tid, name = b.get("id"), (b.get("name") or "")
                    if tid: tc_sessions[tid].add(sid)
                    inp = b.get("input") or {}
                    if name in READ_TOOLS or name in SEARCH_TOOLS:
                        p = first_path(inp)
                        if p: events.append((sid, tid, "ACQ", "tool", p))
                    elif name in WRITE_TOOLS:
                        events.append((sid, tid, "EDIT", "tool", None))
                    elif name in SHELL_TOOLS:
                        mut, ps = shell_paths(inp.get("command") or "")
                        if mut: events.append((sid, tid, "EDIT", "shell", None))
                        for i, p in enumerate(ps):
                            events.append((sid, f"{tid}#{i}", "EDIT_P" if mut else "ACQ", "shell", p))
            elif o.get("type") == "user":
                for b in blocks(o.get("message")):
                    if b.get("type") == "tool_result" and b.get("tool_use_id"):
                        c = b.get("content"); L = 0
                        if isinstance(c, str): L = len(c)
                        elif isinstance(c, list):
                            L = sum(len(x.get("text") or "") for x in c
                                    if isinstance(x, dict) and x.get("type") == "text")
                        res_chars[b["tool_use_id"]] = max(res_chars.get(b["tool_use_id"], 0), L)

uf = UF()
for fn in files: uf.find(fn[:-6])
for tid, sids in tc_sessions.items():
    s = sorted(sids)
    for x in s[1:]: uf.union(s[0], x)
lin_of = {s: uf.find(s) for s in uf.p}

seen, ev = set(), []
for sid, tid, kind, src, p in events:
    l = lin_of[sid]; k = (l, tid, kind)
    if k in seen: continue
    seen.add(k); ev.append((l, tid, kind, src, p))
seen_m, turns = set(), defaultdict(int)
for sid, uuid in msgs:
    l = lin_of[sid]
    if (l, uuid) in seen_m: continue
    seen_m.add((l, uuid)); turns[l] += 1

by_lin = defaultdict(list)
for e in ev: by_lin[e[0]].append(e)


def build(regime):
    rows = []
    for lin, es in by_lin.items():
        if turns[lin] == 0: continue
        acq = [e for e in es if e[2] == "ACQ" and (regime == "both" or e[3] == "tool")]
        edits = sum(1 for e in es if e[2] in ("EDIT", "EDIT_P"))
        paths = [e[4] for e in acq if e[4]]
        if not paths: continue
        keys = [key(p) for p in paths]
        mods = [module_of(p) for p in paths]
        chars = sum(res_chars.get(e[1], 0) for e in acq)
        rows.append(dict(
            lineage=lin, turns=turns[lin], acq_events=len(acq),
            unique_files=len(set(keys)), modules=len(set(mods)),
            edits=edits, codified_reads=sum(1 for p in paths if is_codified(p)),
            explore_chars=chars, explore_per_edit=chars / (edits + 1)))
    return rows


# ---------------- statistics ----------------
def ranks(xs):
    idx = sorted(range(len(xs)), key=lambda i: xs[i]); r = [0.0] * len(xs); i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and xs[idx[j + 1]] == xs[idx[i]]: j += 1
        for k2 in range(i, j + 1): r[idx[k2]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return r


def _phi(z): return 0.5 * (1 + math.erf(z / math.sqrt(2)))
def _med(v):
    s = sorted(v); n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def mw(a, b):
    if len(a) < 4 or len(b) < 4:
        return {"skipped": f"n_a={len(a)}, n_b={len(b)}"}
    r = ranks(a + b); na, nb = len(a), len(b)
    U = sum(r[:na]) - na * (na + 1) / 2.0
    sd = math.sqrt(na * nb * (na + nb + 1) / 12.0)
    z = (U - na * nb / 2.0) / sd if sd else 0.0
    return {"n_no": na, "n_yes": nb, "median_no": round(_med(a), 1), "median_yes": round(_med(b), 1),
            "ratio_yes_over_no": round(_med(b) / _med(a), 3) if _med(a) else None,
            "z": round(z, 3), "p": round(2 * (1 - _phi(abs(z))), 6),
            "rank_biserial": round(2 * U / (na * nb) - 1, 4)}


def ols(y, X, names):
    """Least squares (normal equations + Gaussian elimination). Intercept included."""
    n, k = len(y), len(X[0]) + 1
    A = [[1.0] + list(x) for x in X]
    XtX = [[sum(A[i][a] * A[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    Xty = [sum(A[i][a] * y[i] for i in range(n)) for a in range(k)]
    M = [XtX[i][:] + [Xty[i]] for i in range(k)]
    for c in range(k):
        piv = max(range(c, k), key=lambda r: abs(M[r][c]))
        M[c], M[piv] = M[piv], M[c]
        if abs(M[c][c]) < 1e-12: return None
        for r in range(k):
            if r == c: continue
            f = M[r][c] / M[c][c]
            for j in range(c, k + 1): M[r][j] -= f * M[c][j]
    beta = [M[i][k] / M[i][i] for i in range(k)]
    resid = [y[i] - sum(A[i][j] * beta[j] for j in range(k)) for i in range(n)]
    s2 = sum(r * r for r in resid) / (n - k)
    # diagonal of (X'X)^-1
    I = [[1.0 if a == b else 0.0 for b in range(k)] for a in range(k)]
    M2 = [XtX[i][:] + I[i] for i in range(k)]
    for c in range(k):
        piv = max(range(c, k), key=lambda r: abs(M2[r][c]))
        M2[c], M2[piv] = M2[piv], M2[c]
        d = M2[c][c]
        for j in range(2 * k): M2[c][j] /= d
        for r in range(k):
            if r == c: continue
            f = M2[r][c]
            for j in range(2 * k): M2[r][j] -= f * M2[c][j]
    se = [math.sqrt(s2 * M2[i][k + i]) for i in range(k)]
    out = {}
    for i, nm in enumerate(["(intercept)"] + names):
        t = beta[i] / se[i] if se[i] else 0.0
        out[nm] = {"beta": round(beta[i], 4), "se": round(se[i], 4), "t": round(t, 3),
                   "p": round(2 * (1 - _phi(abs(t))), 6)}
    out["_n"] = n
    out["_r2"] = round(1 - sum(r * r for r in resid) / sum((v - sum(y) / n) ** 2 for v in y), 4)
    return out


OUT = {"generated_utc": datetime.now(timezone.utc).isoformat(),
       "question": "Does the reversed codified-context association remain after controlling for task scope?",
       "caveat": "There is no required scope, so observed scope (unique files, modules, turns) is used as a proxy. These may be post-treatment variables."}

for regime in ("tool", "both"):
    rows = build(regime)
    rows = [r for r in rows if r["acq_events"] >= 5]
    yes = [r for r in rows if r["codified_reads"] > 0]
    no = [r for r in rows if r["codified_reads"] == 0]
    R = {"n": len(rows), "n_codified": len(yes), "n_plain": len(no)}

    # (0) no control - reproduction
    R["raw_explore_per_edit"] = mw([r["explore_per_edit"] for r in no],
                                   [r["explore_per_edit"] for r in yes])
    # does scope itself differ?
    R["scope_unique_files"] = mw([float(r["unique_files"]) for r in no],
                                 [float(r["unique_files"]) for r in yes])
    R["scope_modules"] = mw([float(r["modules"]) for r in no],
                            [float(r["modules"]) for r in yes])
    R["scope_turns"] = mw([float(r["turns"]) for r in no], [float(r["turns"]) for r in yes])

    # (1) stratification - quartiles of unique files
    uf_sorted = sorted(r["unique_files"] for r in rows)
    qs = [uf_sorted[int(len(uf_sorted) * q)] for q in (0.25, 0.5, 0.75)]
    strata = {}
    for i, (lo, hi) in enumerate([(-1, qs[0]), (qs[0], qs[1]), (qs[1], qs[2]), (qs[2], 10**9)]):
        sub = [r for r in rows if lo < r["unique_files"] <= hi]
        sy = [r["explore_per_edit"] for r in sub if r["codified_reads"] > 0]
        sn = [r["explore_per_edit"] for r in sub if r["codified_reads"] == 0]
        strata[f"Q{i+1}_uniquefiles_{lo+1 if lo>=0 else 1}~{hi if hi<10**9 else 'max'}"] = {
            "n": len(sub), **mw(sn, sy)}
    R["stratified_by_unique_files"] = strata

    # (2) regression control
    reg = [r for r in rows if r["explore_per_edit"] > 0]
    y = [math.log(r["explore_per_edit"]) for r in reg]
    X = [[1.0 if r["codified_reads"] > 0 else 0.0,
          math.log(r["unique_files"] + 1), float(r["modules"]), math.log(r["turns"] + 1)]
         for r in reg]
    R["regression_log_explore_per_edit"] = ols(
        y, X, ["codified", "log_unique_files", "modules", "log_turns"])

    # (3) nearest-neighbour matching (unique files, caliper = +-1)
    pool = sorted(no, key=lambda r: r["unique_files"])
    used, pairs = set(), []
    for t in sorted(yes, key=lambda r: r["unique_files"]):
        best, bd = None, None
        for i, c in enumerate(pool):
            if i in used: continue
            d = abs(c["unique_files"] - t["unique_files"])
            if bd is None or d < bd: best, bd = i, d
        if best is not None and bd is not None and bd <= 1:
            used.add(best); pairs.append((t, pool[best]))
    if len(pairs) >= 4:
        dy = [p[0]["explore_per_edit"] for p in pairs]
        dn = [p[1]["explore_per_edit"] for p in pairs]
        diffs = [a - b for a, b in zip(dy, dn)]
        pos = sum(1 for d in diffs if d > 0)
        n = len(diffs)
        z = (pos - n / 2) / math.sqrt(n / 4) if n else 0
        R["matched_caliper1_on_unique_files"] = {
            "n_pairs": n,
            "median_codified": round(_med(dy), 1), "median_plain": round(_med(dn), 1),
            "ratio": round(_med(dy) / _med(dn), 3) if _med(dn) else None,
            "median_paired_diff": round(_med(diffs), 1),
            "sign_test_pos": pos, "sign_test_z": round(z, 3),
            "sign_test_p": round(2 * (1 - _phi(abs(z))), 6)}
    else:
        R["matched_caliper1_on_unique_files"] = {"skipped": f"pairs={len(pairs)}"}

    OUT[f"regime_{regime}"] = R
    if regime == "tool":
        with open(os.path.join(RES, "b3_lineages.csv"), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows([{**r, "lineage": hash_id(r["lineage"])} for r in rows])

OUT["note_regime"] = ("The numerator of explore_per_edit counts tool-result characters only, so only "
                      "the 'tool' regime results are interpreted. 'both' is reported for the scope indicators.")
json.dump(OUT, open(os.path.join(RES, "b3_matching.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print(json.dumps(OUT, ensure_ascii=False, indent=2))
