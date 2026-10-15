"""S10 (B3-b / B3-c) - Dose-response of the reversed codified-context association + reproduction
including sub-agent trajectories.

B3-b  Instead of binary "accessed or not", use the **amount read** for a dose-response check.
      A binary label cannot distinguish amount or quality of reading (B3 limitation L-4).
      If cost rises with dose, a pure selection explanation becomes harder to sustain.

B3-c  Does the association reproduce when sub-agent trajectories are added to their parent
      lineage? Most sub-agents make zero edits, so adding them changes exploration cost per edit
      substantially. This checks whether the reversal survives full accounting.

Regime: tool-only (the numerator of explore_per_edit counts tool-result characters only)

Output (in --results-dir): b3bc_dose_subagent.json  (no identifiers are written)

Usage: python s10_b3bc_dose_subagent.py --transcript-root <dir> --config config.json --results-dir <dir>
"""
import json, os, re, math, sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cac_common import setup  # noqa: E402

ARGS, CFG = setup(__doc__, need_config=True)
SRC = ARGS.transcript_root
RES = ARGS.results_dir

READ_TOOLS = {"Read", "NotebookRead"}
SEARCH_TOOLS = {"Grep", "Glob"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
PATH_KEYS = ("file_path", "notebook_path", "path", "filePath")
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


def scan(path, sid, origin, ev, msgs, res_chars, tc_sessions):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("type") == "assistant":
                msgs.append((sid, o.get("uuid"), origin))
                for b in blocks(o.get("message")):
                    if b.get("type") != "tool_use":
                        continue
                    tid, name = b.get("id"), (b.get("name") or "")
                    if tid and origin == "parent":
                        tc_sessions[tid].add(sid)
                    if name in READ_TOOLS or name in SEARCH_TOOLS:
                        p = first_path(b.get("input") or {})
                        if p:
                            ev.append((sid, tid, "ACQ", origin, p))
                    elif name in WRITE_TOOLS:
                        ev.append((sid, tid, "EDIT", origin, None))
            elif o.get("type") == "user":
                for b in blocks(o.get("message")):
                    if b.get("type") == "tool_result" and b.get("tool_use_id"):
                        c = b.get("content"); L = 0
                        if isinstance(c, str):
                            L = len(c)
                        elif isinstance(c, list):
                            L = sum(len(x.get("text") or "") for x in c
                                    if isinstance(x, dict) and x.get("type") == "text")
                        tid = b["tool_use_id"]
                        res_chars[tid] = max(res_chars.get(tid, 0), L)


ev, msgs, res_chars, tc_sessions = [], [], {}, defaultdict(set)
top = [f for f in os.listdir(SRC) if f.endswith(".jsonl")]
for fn in top:
    scan(os.path.join(SRC, fn), fn[:-6], "parent", ev, msgs, res_chars, tc_sessions)
n_sub = 0
for root, dirs, fns in os.walk(SRC):
    if os.path.basename(root) != "subagents":
        continue
    parent_sid = os.path.basename(os.path.dirname(root))
    for fn in fns:
        if not fn.endswith(".jsonl"):
            continue
        n_sub += 1
        scan(os.path.join(root, fn), parent_sid, "sub", ev, msgs, res_chars, tc_sessions)

uf = UF()
for fn in top: uf.find(fn[:-6])
for tid, sids in tc_sessions.items():
    s = sorted(sids)
    for x in s[1:]: uf.union(s[0], x)
lin_of = {s: uf.find(s) for s in uf.p}

seen, dev = set(), []
for sid, tid, kind, origin, p in ev:
    l = lin_of.get(sid, sid)
    k = (l, tid, kind, origin)
    if k in seen: continue
    seen.add(k); dev.append((l, tid, kind, origin, p))
seen_m, turns = set(), defaultdict(int)
for sid, uu, origin in msgs:
    l = lin_of.get(sid, sid)
    if (l, uu, origin) in seen_m: continue
    seen_m.add((l, uu, origin)); turns[l] += 1

by_lin = defaultdict(list)
for e in dev: by_lin[e[0]].append(e)


def build(include_sub):
    rows = []
    for lin, es in by_lin.items():
        if include_sub:
            sel = es
        else:
            sel = [e for e in es if e[3] == "parent"]
        acq = [e for e in sel if e[2] == "ACQ"]
        edits = sum(1 for e in sel if e[2] == "EDIT")
        paths = [e[4] for e in acq if e[4]]
        if len(paths) < 5:
            continue
        cod = [e for e in acq if e[4] and is_codified(e[4])]
        rows.append(dict(
            lineage=lin, turns=turns[lin], acq=len(acq),
            unique_files=len(set(key(p) for p in paths)),
            modules=len(set(module_of(p) for p in paths)),
            edits=edits,
            codified_reads=len(cod),
            codified_chars=sum(res_chars.get(e[1], 0) for e in cod),
            explore_chars=sum(res_chars.get(e[1], 0) for e in acq)))
    for r in rows:
        r["explore_per_edit"] = r["explore_chars"] / (r["edits"] + 1)
    return rows


# ---------------- statistics ----------------
def ranks(xs):
    idx = sorted(range(len(xs)), key=lambda i: xs[i]); r = [0.0]*len(xs); i = 0
    while i < len(idx):
        j = i
        while j+1 < len(idx) and xs[idx[j+1]] == xs[idx[i]]: j += 1
        for k2 in range(i, j+1): r[idx[k2]] = (i+j)/2.0 + 1.0
        i = j+1
    return r


def _phi(z): return 0.5*(1+math.erf(z/math.sqrt(2)))
def _med(v):
    s = sorted(v); n = len(s)
    return s[n//2] if n % 2 else (s[n//2-1]+s[n//2])/2.0


def spearman(x, y):
    n = len(x)
    if n < 8: return None
    rx, ry = ranks(x), ranks(y)
    mx, my = sum(rx)/n, sum(ry)/n
    num = sum((a-mx)*(b-my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a-mx)**2 for a in rx)); dy = math.sqrt(sum((b-my)**2 for b in ry))
    if not dx or not dy: return None
    rho = num/(dx*dy); z = rho*math.sqrt(n-1)
    return {"rho": round(rho, 4), "n": n, "p": round(2*(1-_phi(abs(z))), 6)}


def mw(a, b):
    if len(a) < 4 or len(b) < 4: return {"skipped": f"n={len(a)}/{len(b)}"}
    r = ranks(a+b); na, nb = len(a), len(b)
    U = sum(r[:na]) - na*(na+1)/2.0
    sd = math.sqrt(na*nb*(na+nb+1)/12.0); z = (U-na*nb/2.0)/sd if sd else 0
    return {"n_a": na, "n_b": nb, "median_a": round(_med(a), 1), "median_b": round(_med(b), 1),
            "ratio": round(_med(b)/_med(a), 3) if _med(a) else None,
            "z": round(z, 3), "p": round(2*(1-_phi(abs(z))), 6)}


def ols(y, X, names):
    n, k = len(y), len(X[0])+1
    A = [[1.0]+list(x) for x in X]
    XtX = [[sum(A[i][a]*A[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    Xty = [sum(A[i][a]*y[i] for i in range(n)) for a in range(k)]
    M = [XtX[i][:]+[Xty[i]] for i in range(k)]
    for c in range(k):
        piv = max(range(c, k), key=lambda r: abs(M[r][c])); M[c], M[piv] = M[piv], M[c]
        if abs(M[c][c]) < 1e-12: return None
        for r in range(k):
            if r == c: continue
            f = M[r][c]/M[c][c]
            for j in range(c, k+1): M[r][j] -= f*M[c][j]
    beta = [M[i][k]/M[i][i] for i in range(k)]
    resid = [y[i]-sum(A[i][j]*beta[j] for j in range(k)) for i in range(n)]
    s2 = sum(r*r for r in resid)/(n-k)
    I = [[1.0 if a == b else 0.0 for b in range(k)] for a in range(k)]
    M2 = [XtX[i][:]+I[i] for i in range(k)]
    for c in range(k):
        piv = max(range(c, k), key=lambda r: abs(M2[r][c])); M2[c], M2[piv] = M2[piv], M2[c]
        d = M2[c][c]
        for j in range(2*k): M2[c][j] /= d
        for r in range(k):
            if r == c: continue
            f = M2[r][c]
            for j in range(2*k): M2[r][j] -= f*M2[c][j]
    se = [math.sqrt(s2*M2[i][k+i]) for i in range(k)]
    out = {}
    for i, nm in enumerate(["(intercept)"]+names):
        t = beta[i]/se[i] if se[i] else 0
        out[nm] = {"beta": round(beta[i], 4), "p": round(2*(1-_phi(abs(t))), 6)}
    mean = sum(y)/n
    out["_n"] = n
    out["_r2"] = round(1 - sum(r*r for r in resid)/sum((v-mean)**2 for v in y), 4)
    return out


OUT = {"generated_utc": datetime.now(timezone.utc).isoformat(),
       "subagent_files_scanned": n_sub,
       "note": "Tool-only regime. The numerator of explore_per_edit counts tool-result characters only."}

for label, inc in (("B3b_parent_only", False), ("B3c_parent_plus_subagents", True)):
    rows = build(inc)
    yes = [r for r in rows if r["codified_reads"] > 0]
    no = [r for r in rows if r["codified_reads"] == 0]
    R = {"n": len(rows), "n_codified": len(yes), "n_plain": len(no),
         "median_edits": round(_med([float(r["edits"]) for r in rows]), 1),
         "median_acq": round(_med([float(r["acq"]) for r in rows]), 1)}

    # binary (reproduction check)
    R["binary_explore_per_edit"] = mw([r["explore_per_edit"] for r in no],
                                      [r["explore_per_edit"] for r in yes])

    # --- B3-b dose-response ---
    R["dose_spearman_reads_vs_cost"] = spearman(
        [float(r["codified_reads"]) for r in rows], [r["explore_per_edit"] for r in rows])
    R["dose_spearman_chars_vs_cost"] = spearman(
        [float(r["codified_chars"]) for r in rows], [r["explore_per_edit"] for r in rows])
    # within the accessing group only (zeros removed: pure dose effect)
    if len(yes) >= 8:
        R["dose_within_codified_reads"] = spearman(
            [float(r["codified_reads"]) for r in yes], [r["explore_per_edit"] for r in yes])
        R["dose_within_codified_chars"] = spearman(
            [float(r["codified_chars"]) for r in yes], [r["explore_per_edit"] for r in yes])
    # tertiles
    if len(yes) >= 12:
        s = sorted(yes, key=lambda r: r["codified_chars"])
        t = len(s)//3
        g = [s[:t], s[t:2*t], s[2*t:]]
        R["dose_tertiles_codified_chars"] = [
            {"tertile": i+1, "n": len(x),
             "median_codified_chars": round(_med([float(r["codified_chars"]) for r in x]), 1),
             "median_explore_per_edit": round(_med([r["explore_per_edit"] for r in x]), 1)}
            for i, x in enumerate(g)]
    # dose regression (scope-controlled)
    reg = [r for r in rows if r["explore_per_edit"] > 0]
    y = [math.log(r["explore_per_edit"]) for r in reg]
    X = [[math.log(1+r["codified_chars"]), math.log(r["unique_files"]+1),
          math.log(r["turns"]+1)] for r in reg]
    R["dose_regression"] = ols(y, X, ["log_codified_chars", "log_unique_files", "log_turns"])

    OUT[label] = R

# B3-c comparison
p, s = OUT["B3b_parent_only"], OUT["B3c_parent_plus_subagents"]
OUT["B3c_comparison"] = {
    "binary_ratio_parent_only": p["binary_explore_per_edit"].get("ratio"),
    "binary_ratio_with_subagents": s["binary_explore_per_edit"].get("ratio"),
    "binary_p_parent_only": p["binary_explore_per_edit"].get("p"),
    "binary_p_with_subagents": s["binary_explore_per_edit"].get("p"),
    "median_edits_parent_only": p["median_edits"],
    "median_edits_with_subagents": s["median_edits"],
    "median_acq_parent_only": p["median_acq"],
    "median_acq_with_subagents": s["median_acq"],
}
json.dump(OUT, open(os.path.join(RES, "b3bc_dose_subagent.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print(json.dumps(OUT, ensure_ascii=False, indent=2))
