"""S03 - CAC -> cost associations in the pilot observational data.

Purpose: check whether CAC primitives are associated with cost / latency in the 339 sessions.
         There is no random assignment, so these are **correlations**, not causal effects;
         the output states this limitation.

Input  (in --results-dir): pilot_sessions.csv (s01 output, renamed), pilot_subagents csv (s02 output)
Output (in --results-dir): pilot_associations.json + console table

No dependencies (standard library only). Spearman / Mann-Whitney implemented here.

Usage: python s03_pilot_associations.py --results-dir <dir> [--sessions-csv pilot_sessions.csv]
       [--subagents-csv pilot_subagents_20260904.csv]
The released pilot_associations.json was computed with the 2026-09-04 sub-agent scan (n=215).
"""
import csv, json, os, math, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cac_common import setup  # noqa: E402

ARGS, CFG = setup(__doc__, transcript_root=False, extra=[
    (("--sessions-csv",), {"default": "pilot_sessions.csv"}),
    (("--subagents-csv",), {"default": "pilot_subagents_20260904.csv"})])
RES = ARGS.results_dir


# ---------- statistics utilities (standard library only) ----------
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


def spearman(xs, ys):
    """returns (rho, n, z, p_two_sided_approx)"""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 8:
        return None, n, None, None
    rx = ranks([p[0] for p in pairs])
    ry = ranks([p[1] for p in pairs])
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if dx == 0 or dy == 0:
        return None, n, None, None
    rho = num / (dx * dy)
    z = rho * math.sqrt(n - 1)
    p = 2 * (1 - _phi(abs(z)))
    return round(rho, 4), n, round(z, 3), round(p, 6)


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def mannwhitney(a, b):
    """Normal-approximation Mann-Whitney U. returns (n_a, n_b, med_a, med_b, U, z, p)"""
    if len(a) < 5 or len(b) < 5:
        return None
    allv = a + b
    r = ranks(allv)
    ra = sum(r[:len(a)])
    na, nb = len(a), len(b)
    U = ra - na * (na + 1) / 2.0
    mu = na * nb / 2.0
    # no tie correction (conservative)
    sd = math.sqrt(na * nb * (na + nb + 1) / 12.0)
    z = (U - mu) / sd if sd else 0.0
    p = 2 * (1 - _phi(abs(z)))
    return dict(n_a=na, n_b=nb, median_a=round(_med(a), 3), median_b=round(_med(b), 3),
                U=round(U, 1), z=round(z, 3), p=round(p, 6),
                rank_biserial=round(2 * U / (na * nb) - 1, 4))


def _med(v):
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def f(v, cast=float, default=None):
    try:
        if v == "" or v is None:
            return default
        return cast(v)
    except Exception:
        return default


# ---------- load ----------
rows = list(csv.DictReader(open(os.path.join(RES, ARGS.sessions_csv), encoding="utf-8-sig")))
subs = list(csv.DictReader(open(os.path.join(RES, ARGS.subagents_csv), encoding="utf-8-sig")))

S = []
for r in rows:
    d = dict(
        session=r["session"],
        turns=f(r["turns"], int, 0),
        read_events=f(r["read_events"], int, 0),
        unique_files=f(r["unique_files"], int, 0),
        rereads=f(r["rereads"], int, 0),
        reread_rate=f(r["reread_rate"]),
        modules=f(r["modules_touched"], int, 0),
        crossings=f(r["boundary_crossings"], int, 0),
        crossing_rate=f(r["crossing_rate"]),
        drives=f(r["drives_touched"], int, 0),
        ttfe=f(r["time_to_first_edit_turns"], int, None),
        explore_chars=f(r["explore_chars"], int, 0),
        all_chars=f(r["all_result_chars"], int, 0),
        explore_share=f(r["explore_char_share"]),
        writes=f(r["writes"], int, 0),
        reads=f(r["reads"], int, 0),
        searches=f(r["searches"], int, 0),
        shells=f(r["shells"], int, 0),
        deleg=f(r["delegations"], int, 0),
        verify=f(r["verify_runs"], int, 0),
        out_tok=f(r["out_tokens"], int, 0),
        cc_tok=f(r["cache_create_tokens"], int, 0),
        cr_tok=f(r["cache_read_tokens"], int, 0),
    )
    S.append(d)

OUT = {"note": "No random assignment -> all results are correlations. No causal claims.",
       "n_sessions_total": len(S)}

# ---------- analysis 1: boundary crossing <-> cost (sessions with actual exploration) ----------
A = [s for s in S if s["read_events"] >= 5]
OUT["A_subset_rule"] = "read_events >= 5"
OUT["A_n"] = len(A)

def per_edit(s):
    return s["explore_chars"] / (s["writes"] + 1)

tests = {}
for xname, xf in [("crossing_rate", lambda s: s["crossing_rate"]),
                  ("boundary_crossings", lambda s: s["crossings"]),
                  ("modules_touched", lambda s: s["modules"]),
                  ("reread_rate", lambda s: s["reread_rate"]),
                  ("unique_files", lambda s: s["unique_files"])]:
    for yname, yf in [("explore_chars_per_edit", per_edit),
                      ("explore_chars", lambda s: s["explore_chars"]),
                      ("turns", lambda s: s["turns"]),
                      ("cache_read_tokens", lambda s: s["cr_tok"]),
                      ("verify_runs", lambda s: s["verify"])]:
        rho, n, z, p = spearman([xf(s) for s in A], [yf(s) for s in A])
        tests[f"{xname} ~ {yname}"] = dict(rho=rho, n=n, p=p)
OUT["A_spearman"] = tests

# ---------- analysis 2: TTFE (turns to first edit) ----------
B = [s for s in A if s["ttfe"] is not None]
OUT["B_n_sessions_with_edit"] = len(B)
t2 = {}
for xname, xf in [("crossing_rate", lambda s: s["crossing_rate"]),
                  ("modules_touched", lambda s: s["modules"]),
                  ("unique_files", lambda s: s["unique_files"]),
                  ("reread_rate", lambda s: s["reread_rate"])]:
    rho, n, z, p = spearman([xf(s) for s in B], [s["ttfe"] for s in B])
    t2[f"{xname} ~ time_to_first_edit"] = dict(rho=rho, n=n, p=p)
OUT["B_spearman_ttfe"] = t2

# ---------- analysis 3: single-module vs multi-module sessions ----------
single = [per_edit(s) for s in A if s["modules"] <= 1]
multi = [per_edit(s) for s in A if s["modules"] >= 4]
OUT["C_single_vs_multi_module"] = dict(
    definition="A: modules_touched<=1 vs B: modules_touched>=4, y=explore_chars_per_edit",
    result=mannwhitney(single, multi))

# ---------- analysis 4: sessions that delegate (sub-agents) ----------
deleg_yes = [s for s in A if s["deleg"] > 0]
deleg_no = [s for s in A if s["deleg"] == 0]
OUT["D_delegation_sessions"] = dict(
    n_with=len(deleg_yes), n_without=len(deleg_no),
    explore_per_edit=mannwhitney([per_edit(s) for s in deleg_no],
                                 [per_edit(s) for s in deleg_yes]),
    crossing_rate=mannwhitney([s["crossing_rate"] for s in deleg_no],
                              [s["crossing_rate"] for s in deleg_yes]))

# ---------- analysis 5: CAC of the sub-agents themselves ----------
sub = []
for r in subs:
    sub.append(dict(turns=f(r["turns"], int, 0), reads=f(r["reads"], int, 0),
                    writes=f(r["writes"], int, 0),
                    uniq=f(r["unique_paths"], int, 0), rr=f(r["rereads"], int, 0),
                    chars=f(r["result_chars"], int, 0),
                    cr=f(r["cache_read"], int, 0), out=f(r["out_tokens"], int, 0)))
sr = sum(x["reads"] for x in sub); sw = sum(x["writes"] for x in sub)
mr = sum(s["reads"] + s["searches"] for s in S); mw = sum(s["writes"] for s in S)
readonly = [x for x in sub if x["writes"] == 0]
OUT["E_subagent"] = dict(
    n=len(sub),
    read_write_ratio_sub=round(sr / sw, 3) if sw else None,
    read_write_ratio_main=round(mr / mw, 3) if mw else None,
    ratio_multiple=round((sr / sw) / (mr / mw), 2) if sw and mw else None,
    read_only_subagents=len(readonly),
    read_only_share=round(len(readonly) / len(sub), 4),
    median_reads_per_subagent=_med([x["reads"] for x in sub]),
    median_turns_per_subagent=_med([x["turns"] for x in sub]),
    total_result_chars=sum(x["chars"] for x in sub))

# ---------- analysis 6: access to codified context assets ----------
# pilot_sessions.csv has no paths, so only proxies that need no transcript rescan are possible.
OUT["F_note"] = ("Session-level labels for codified context (factor C) need paths and are "
                 "computed separately in S04; out of scope for this script.")

json.dump(OUT, open(os.path.join(RES, "pilot_associations.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print(json.dumps(OUT, ensure_ascii=False, indent=2))
