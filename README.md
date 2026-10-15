# Measuring Context Assembly Cost in Coding-Agent Trajectories — derived data and analysis scripts

Data and code package for the paper

> YeoHoon Yoon and Kyung-Sung Kim. *Measuring Context Assembly Cost in Coding-Agent Trajectories:
> Limits of Identifying Software-Structure Effects from Observation.* Submitted to *Science of Computer
> Programming*.

Authors: **YeoHoon Yoon** and **Kyung-Sung Kim**, aSSIST University.
Package DOI: [10.5281/zenodo.23386434](https://doi.org/10.5281/zenodo.23386434) (v1.0.0; all versions:
[10.5281/zenodo.23386433](https://doi.org/10.5281/zenodo.23386433)). Licence: CC BY 4.0 (see `LICENSE`).

## 1. What is included

| Folder / file | Content | Generating script |
|---|---|---|
| `data/final_lineage_cac.csv` | Per-lineage CAC measurements, **broader (tool + shell) path-resolution regime**, 203 lineages, canonical snapshot | `s06_lineage_cac_final.py` |
| `data/b3_lineages.csv` | Per-lineage measurements, **tool-only regime**, 110 lineages with `acq_events >= 5`, reduced column set (see §4) | `s08_b3_matching.py` |
| `data/pilot_sessions.csv` | Per-session (before lineage merge) primitives, 339 sessions | `s01_cac_primitives_main.py` (written as `cac_pilot_sessions.csv`) |
| `data/factor_labels.csv` | Per-session codified-context and grouping labels, 339 sessions | `s04_codified_context_label.py` |
| `data/pilot_subagents_20260904.csv` | Per-sub-agent primitives, scan of 2026-09-04 (215 transcripts) | `s02_cac_primitives_subagent.py` (written as `cac_pilot_subagents.csv`) |
| `data/pilot_subagents_20260905.csv` | Per-sub-agent primitives, rescan of 2026-09-05 (211 transcripts) — **source of the sub-agent counts in the paper** | `s02_cac_primitives_subagent.py` |
| `results/final_measurements.json` | Corpus size, lineage-merge (dedup) statistics, aggregate measurements under both regimes | `s06` |
| `results/final_associations.json` | The 28-test family under each regime with BH-FDR q values (both regimes) | `s06` |
| `results/pilot_summary_main.json` | Session-scan summary (window, tool counts, explore-character share, anonymised module counts) | `s01` (written as `cac_pilot_summary.json`) |
| `results/pilot_summary_subagent_20260904.json`, `..._20260905.json` | Sub-agent scan summaries for the two scan dates | `s02` (written as `cac_pilot_sub_summary.json`) |
| `results/pilot_associations.json` | Session-level pilot associations; sub-agent vs main read/write ratios | `s03_pilot_associations.py` |
| `results/factor_associations.json` | Factor B (grouping) and factor C (codified context) session-level checks | `s04` |
| `results/path_coverage.json` | Share of file access visible in tool arguments vs shell commands | `s05_path_coverage_audit.py` |
| `results/b3_matching.json` | Codified-context association under scope control (stratification, regression, matching) | `s08` |
| `results/b5_reemission.json` | Characterisation of re-emitted tool-call identifiers | `s09_b5_reemission.py` |
| `results/b3bc_dose_subagent.json` | Dose-response and parent + sub-agent accounting of the codified-context association | `s10_b3bc_dose_subagent.py` |
| `results/prior_extraction_duplication.json` | Duplication structure in an earlier extraction of an overlapping window (237 identifiers duplicated within a single session) | `s12_prior_extraction_duplication.py` |
| `results/c2c3_regex_depth.json` | Shell-path regex audit (300 sampled commands) and delegation depth | `s13_c2c3_regex_depth.py` |
| `results/supplementary_counts.json` | Quantities cited in the paper that have no generating script (recorded in working notes) plus counts computed by the release build (sub-agent rescan difference, window length) | — (see `provenance` fields) |
| `supplementary/S1_test_family.csv` | **S1**: enumeration of the 28-test family with statistic, n, p, BH q and survival flag per regime | `s15_fdr_threshold_tier.py` |
| `supplementary/S2_threshold_grid.csv` | **S2**: acquisition-threshold sensitivity (thresholds 1, 2, 3, 5, 10, 20 × BH α 0.01/0.05/0.10), all sources and regimes | `s15` |
| `supplementary/S3_path_tiers.json`, `S3_path_tiers.csv` | **S3**: path-confidence tier analysis (T1 tool argument / T2 shell path existing / T3 shell path absent) | `s15` |
| `supplementary/s15_fdr_threshold_tier.json` | Full `s15` output (family enumeration, all grids, reproduction check, reconstruction, tiers) | `s15` |
| `second_product/a3_codex.json` | Second-product check on Codex CLI session logs (resume duplication, shell paths, delegation) | `s14_a3_codex.py` |
| `scripts/` | Extraction, lineage-merge, path-resolution and analysis scripts `s01`–`s06`, `s08`–`s10`, `s12`–`s15`, shared helper `cac_common.py`, `config.example.json`, `requirements.txt` | — |
| `NUMBERS_MAP.csv` | Map from every numeric claim in the abstract, §1, §3.1–§3.4 and §4.1–§4.5 to file, key/column and generating script, with the value found in the released file | — |
| `SHA256SUMS.txt` | SHA-256 checksums of all files in the package | — |

All text files are UTF-8 without byte-order mark. Field names follow the scripts; `ttfe` = turns to first edit,
`acq_events` = file-acquisition events, `explore_per_edit` = tool-result characters of acquisition calls / (edits + 1),
`cr_tok` = cache-read tokens.

## 2. Snapshot dates

| Item | Date (UTC) |
|---|---|
| Observation window of the main-agent transcripts | 2026-05-30T02:06Z – 2026-09-03T23:56Z |
| **Canonical snapshot** (s06: `final_*`; cut-off used by s15) | **2026-09-04T01:19:25Z** |
| Session scan s01/s03/s04/s05 (`pilot_sessions.csv`, `factor_labels.csv`, `path_coverage.json`) | 2026-09-04 |
| Sub-agent scan 1 (`*_20260904.*`, 215 transcripts; used by `pilot_associations.json`) | 2026-09-04 |
| Sub-agent rescan (`*_20260905.*`, 211 transcripts; the sub-agent counts reported in the paper) | 2026-09-05 |
| s08–s10 (`b3_*`, `b5_*`, `b3bc_*`) run against the transcript directory as it stood on | 2026-09-05 |
| Earlier extraction of an overlapping window (input of s12) — snapshot / analysed | 2026-08-27 / 2026-09-05 |
| s13 (`c2c3_regex_depth.json`), s14 (`a3_codex.json`) | 2026-09-06 |
| s15 (S1–S3) run; on-disk existence for path tiers judged on | 2026-09-17 |

## 3. Path-resolution regimes

* **Tool-only** (`tool`, `regime_tool_only`): file acquisitions are the `file_path`/`path` arguments of
  Read/NotebookRead/Grep/Glob calls.
* **Broader / tool + shell** (`both`, `regime_tool_plus_shell`): additionally, paths extracted by regular
  expression from PowerShell/Bash commands.
* In `explore_per_edit` the numerator counts tool-result characters only, which is why the codified-context
  analyses (s08, s10) are interpreted in the tool-only regime.
* s15 additionally splits acquisitions into path-confidence tiers T1/T2/T3 (S3).

## 4. Which regimes have per-lineage rows

* **Broader (tool + shell) regime:** full per-lineage table, `data/final_lineage_cac.csv` (203 lineages,
  canonical snapshot). The S2 grid rows with `source = exact_csv` are recomputed exactly from this table
  (`exact_reproduction_check` in `s15_fdr_threshold_tier.json` confirms that threshold 5 reproduces all 28
  canonical ρ and q values).
* **Tool-only regime:** `s06` did not write per-lineage rows for this regime. The tool-only 28-test family
  (n = 109 at `acq_events >= 5`) is therefore released **as test statistics only**
  (`results/final_associations.json` → `tool`; `supplementary/S1_test_family.csv`; aggregates in
  `results/final_measurements.json` → `regime_tool_only`).
  `data/b3_lineages.csv` does contain tool-only per-lineage rows, but it is a different, reduced table:
  produced by `s08` on the 2026-09-05 transcript state, restricted to 110 lineages with `acq_events >= 5`,
  with columns `turns, acq_events, unique_files, modules, edits, codified_reads, explore_chars,
  explore_per_edit` only (no crossings, re-reads, verification runs, TTFE or token counts). It reproduces the
  s08 codified-context analyses, **not** the tool-only 28-test family.
* **Session level:** `data/pilot_sessions.csv` + `data/factor_labels.csv` give all 339 sessions before lineage
  merge (tool-argument paths only; the S2 rows with `source = s01_session_csv`).

## 5. Identifiers and hashing

Session, lineage, sub-agent and parent-session identifiers, and the session identifiers in the
duplication analyses, are replaced by `HMAC-SHA256(salt, raw_id)` truncated to the first 16 hex characters.
A lineage identifier equals the hashed identifier of the lineage's root session, so lineage and session
tables join on the same column values; sub-agent identifiers hash the transcript file stem.
**The salt is withheld**, so hashes cannot be reversed or recomputed from public information. The scripts
apply the same transformation (`cac_common.hash_id`, salt from environment variable `CAC_SALT`), so their
output has the released form.

Further pseudonymisation applied to the released files:

* `pilot_summary_main.json` → `top_modules`: the 30 module (directory) names are replaced by rank labels
  `module_01` … `module_30` (by frequency); counts are unchanged.
* Sub-agent tables: start/end timestamps are coarsened to the UTC date (`start_date_utc`, `end_date_utc`).
* `pilot_sessions.csv`: the per-session working-directory count (`cwds`) and git-branch names (`branches`) are
  dropped; `versions` is the number of distinct CLI versions seen in the session.
* `c2c3_regex_depth.json`: the example lists of raw command fragments are replaced by their counts.
* Absolute paths in `source`/`root` fields are replaced by placeholders.
* Korean-language notes and labels in the original outputs were translated to English; no numeric value was changed.

## 6. What is not released and why

* **Raw transcripts** (main-agent and sub-agent JSONL), the mutation records of the earlier extraction and the
  Codex CLI session logs. They are working logs of a private software project and contain proprietary
  file paths, command lines, file contents and credentials.
* The project-specific configuration values (repository directory name, directory lists used for factor B,
  the verification-command pattern, default working directory, the raw ids of the resumed session pair).
  `scripts/config.example.json` shows the structure with placeholders.
* The salt and the id → hash and module → label maps.
* Planning artefacts that are not used by the paper: power-analysis outputs and R scripts, a literature-derived
  variance prior used only for power analysis, an API cost / run-time model for a future experiment (s07),
  and figure templates.

## 7. Scripts

`scripts/` contains `s01`–`s06`, `s08`–`s10` and `s12`–`s15` (s07 and s11 produce outputs not used in this
paper and are omitted), parametrised from the
originals: input locations are command-line arguments or environment variables
(`--transcript-root` / `CAC_TRANSCRIPT_ROOT`, `--codex-root` / `CAC_CODEX_ROOT`,
`--mutations-file` / `CAC_MUTATIONS_FILE`, `--config` / `CAC_CONFIG`, `--results-dir` / `CAC_RESULTS_DIR`),
project-specific names are read from `config.json`, identifiers are hashed on output, and comments and output
notes are in English. The analytic logic is otherwise unchanged. Standard library only (Python ≥ 3.8).

Dependencies between scripts: `s03` reads `pilot_sessions.csv` and a sub-agent table; `s15` reads
`final_lineage_cac.csv`, `final_associations.json`, `pilot_sessions.csv` and `factor_labels.csv` from
`--results-dir` and re-reads surviving transcripts. All other scripts read transcripts directly.

**The scripts cannot be re-run from this package**: they require the withheld transcript snapshot, the
withheld configuration and the withheld salt. They document exactly how every released number was produced.

## 8. Known limitations

* **Snapshot mixing for sub-agent counts.** Sub-agent quantities in the paper (211 transcripts, read/write
  ratio, read-only share) come from the 2026-09-05 rescan, while the main-corpus measurements come from the
  2026-09-04 canonical snapshot. Between the two scans 7 sub-agent transcripts disappeared and 3 new ones
  appeared (`results/supplementary_counts.json`). `pilot_associations.json` was computed with the 2026-09-04
  scan (215). Both scans are released and labelled by date.
* s08–s10 ran against the 2026-09-05 transcript state, so their subsets differ slightly from the canonical
  snapshot (e.g. tool-only subset n = 110 in `b3_matching.json` vs 109 in `final_associations.json`).
* **Raw transcripts were later partly deleted** (133 of the 339 canonical sessions were no longer present on
  2026-09-17). Re-extraction will therefore not reproduce the published counts. s15 reconstructed the
  tool-only regime and the path tiers from the 206 surviving sessions (reported as such in S1–S3).
* Path-tier existence (T2/T3) and the C2 existence rate depend on the disk state at run time
  (2026-09-17 and 2026-09-06 respectively), not on the canonical snapshot.
* Some quantities have no generating script and were recorded in working notes; they are collected in
  `results/supplementary_counts.json` with `provenance = "recorded in working notes, no generating script"`.

## 9. Citation

Please cite the paper (above) and this package: Yoon, Y. & Kim, K.-S. (2026). *Measuring Context Assembly Cost in
Coding-Agent Trajectories — derived data and analysis scripts* [Data set]. Zenodo. https://doi.org/10.5281/zenodo.23386434
See `CITATION.cff`.
