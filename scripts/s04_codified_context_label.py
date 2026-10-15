"""S04 - Session labelling for factor C (codified context) and factor B (grouping) + associations.

Labels each session by whether it accessed codified assets (CLAUDE.md / _manifests / .claude /
memory) and tests associations with exploration cost per edit and TTFE. No random assignment
-> correlations only.

Factor B: reads under <repo>/scripts/<dir>/ are bucketed as 'layer' (technically grouped
directories) or 'feature' (feature grouped directories). The directory names are
project-specific and are read from the config (layer_dirs: 7 names, feature_dirs: 13 names).

Output (in --results-dir): factor_labels.csv (session ids salted-hashed), factor_associations.json

Usage: python s04_codified_context_label.py --transcript-root <dir> --config config.json --results-dir <dir>
Requires env CAC_SALT.
"""
import json, os, csv, math, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cac_common import setup, hash_id  # noqa: E402

ARGS, CFG = setup(__doc__, need_config=True)
SRC = ARGS.transcript_root
RES = ARGS.results_dir

READ = {"Read", "NotebookRead", "Grep", "Glob"}
WRITE = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
PATH_KEYS = ("file_path", "notebook_path", "path", "filePath")

LAYER = set(CFG["layer_dirs"])
FEATURE = set(CFG["feature_dirs"])


def is_codified(p):
    q = p.lower().replace("/", "\\")
    return ("claude.md" in q or "\\_manifests\\" in q or "\\.claude\\" in q
            or "\\memory\\" in q or q.endswith("skill.md") or "agents.md" in q)


def scripts_bucket(p):
    q = p.lower().replace("/", "\\")
    i = q.find("\\scripts\\")
    if i < 0:
        return None
    rest = q[i + 9:].split("\\")
    if not rest:
        return None
    seg = rest[0]
    if seg in LAYER:
        return "layer"
    if seg in FEATURE:
        return "feature"
    return None


def blocks(m):
    c = (m or {}).get("content")
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


# ---------- statistics ----------
def ranks(xs):
    idx = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and xs[idx[j + 1]] == xs[idx[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            r[idx[k]] = avg
        i = j + 1
    return r


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _med(v):
    s = sorted(v); n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def mw(a, b):
    if len(a) < 5 or len(b) < 5:
        return {"skipped": f"n_a={len(a)}, n_b={len(b)} (<5)"}
    r = ranks(a + b)
    na, nb = len(a), len(b)
    U = sum(r[:na]) - na * (na + 1) / 2.0
    sd = math.sqrt(na * nb * (na + nb + 1) / 12.0)
    z = (U - na * nb / 2.0) / sd if sd else 0.0
    return dict(n_a=na, n_b=nb, median_a=round(_med(a), 1), median_b=round(_med(b), 1),
                z=round(z, 3), p=round(2 * (1 - _phi(abs(z))), 6),
                rank_biserial=round(2 * U / (na * nb) - 1, 4))


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
    return dict(rho=round(rho, 4), n=n, p=round(2 * (1 - _phi(abs(z))), 6))


# ---------- scan ----------
rows = []
for fn in os.listdir(SRC):
    if not fn.endswith(".jsonl"):
        continue
    sid = fn[:-6]
    turns = writes = codi = reads = 0
    lay = feat = 0
    explore_chars = 0
    first_edit = None
    id2t = {}
    paths = []
    with open(os.path.join(SRC, fn), "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("type") == "assistant":
                turns += 1
                for b in blocks(o.get("message")):
                    if b.get("type") != "tool_use":
                        continue
                    n = b.get("name") or ""
                    id2t[b.get("id")] = n
                    if n in READ:
                        reads += 1
                        p = first_path(b.get("input") or {})
                        if p:
                            paths.append(p)
                            if is_codified(p):
                                codi += 1
                            sb = scripts_bucket(p)
                            if sb == "layer":
                                lay += 1
                            elif sb == "feature":
                                feat += 1
                    elif n in WRITE:
                        writes += 1
                        if first_edit is None:
                            first_edit = turns
            elif o.get("type") == "user":
                for b in blocks(o.get("message")):
                    if b.get("type") != "tool_result":
                        continue
                    if id2t.get(b.get("tool_use_id")) in READ:
                        c = b.get("content")
                        if isinstance(c, str):
                            explore_chars += len(c)
                        elif isinstance(c, list):
                            for x in c:
                                if isinstance(x, dict) and x.get("type") == "text":
                                    explore_chars += len(x.get("text") or "")
    if turns == 0:
        continue
    keys = [os.path.normpath(p).lower() for p in paths]
    rows.append(dict(session=sid, turns=turns, reads=reads, writes=writes,
                     path_events=len(keys), unique_files=len(set(keys)),
                     codified_reads=codi,
                     codified_share=round(codi / len(keys), 4) if keys else 0.0,
                     layer_reads=lay, feature_reads=feat,
                     explore_chars=explore_chars,
                     explore_per_edit=round(explore_chars / (writes + 1), 1),
                     ttfe=first_edit if first_edit else ""))

with open(os.path.join(RES, "factor_labels.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows([{**r, "session": hash_id(r["session"])} for r in rows])

A = [r for r in rows if r["path_events"] >= 5]
OUT = {"note": "No random assignment -> correlations only. Factor C is confounded with time, CLI version and model.",
       "n_all": len(rows), "n_subset": len(A), "subset_rule": "path_events >= 5"}

yes = [r for r in A if r["codified_reads"] > 0]
no = [r for r in A if r["codified_reads"] == 0]
OUT["C_codified_access"] = dict(
    n_with=len(yes), n_without=len(no),
    total_codified_read_events=sum(r["codified_reads"] for r in rows),
    sessions_with_any_codified_read=sum(1 for r in rows if r["codified_reads"] > 0),
    explore_per_edit=mw([r["explore_per_edit"] for r in no],
                        [r["explore_per_edit"] for r in yes]),
    unique_files=mw([float(r["unique_files"]) for r in no],
                    [float(r["unique_files"]) for r in yes]),
    ttfe=mw([float(r["ttfe"]) for r in no if r["ttfe"] != ""],
            [float(r["ttfe"]) for r in yes if r["ttfe"] != ""]))
OUT["C_dose"] = dict(
    codified_share_vs_explore_per_edit=spearman([r["codified_share"] for r in A],
                                                [r["explore_per_edit"] for r in A]),
    codified_reads_vs_unique_files=spearman([float(r["codified_reads"]) for r in A],
                                            [float(r["unique_files"]) for r in A]))

lay_s = [r for r in A if r["layer_reads"] > r["feature_reads"] and r["layer_reads"] > 0]
fea_s = [r for r in A if r["feature_reads"] > r["layer_reads"] and r["feature_reads"] > 0]
OUT["B_grouping"] = dict(
    total_layer_read_events=sum(r["layer_reads"] for r in rows),
    total_feature_read_events=sum(r["feature_reads"] for r in rows),
    n_layer_dominant=len(lay_s), n_feature_dominant=len(fea_s),
    explore_per_edit=mw([r["explore_per_edit"] for r in lay_s],
                        [r["explore_per_edit"] for r in fea_s]))

json.dump(OUT, open(os.path.join(RES, "factor_associations.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print(json.dumps(OUT, ensure_ascii=False, indent=2))
