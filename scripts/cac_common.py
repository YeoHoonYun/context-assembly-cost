"""Shared helpers for the released CAC scripts.

- Command-line / environment configuration (transcript root, config file, results directory).
- Salted identifier hashing: HMAC-SHA256 keyed with the secret in env CAC_SALT,
  truncated to the first 16 hex characters. Every session, lineage, sub-agent and
  file identifier written by the scripts passes through hash_id(), so the scripts
  emit the same (pseudonymised) form as the released tables. The salt used for the
  released tables is withheld.
"""
import argparse
import hashlib
import hmac
import json
import os

HASH_HEX_CHARS = 16


def hash_id(raw):
    """HMAC-SHA256(salt = env CAC_SALT, message = raw id) -> first 16 hex chars."""
    if raw is None or raw == "":
        return raw
    salt = os.environ.get("CAC_SALT")
    if not salt:
        raise SystemExit("CAC_SALT is not set; identifiers cannot be pseudonymised.")
    return hmac.new(salt.encode("utf-8"), str(raw).encode("utf-8"),
                    hashlib.sha256).hexdigest()[:HASH_HEX_CHARS]


def load_config(path, required):
    """Load the JSON config (see config.example.json)."""
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    if required:
        raise SystemExit(f"config file not found: {path!r} (copy config.example.json to config.json and edit it)")
    return {}


def setup(description, transcript_root=True, codex_root=False, mutations_file=False,
          need_config=False, extra=None):
    """Parse CLI arguments. Returns (args, config).

    --transcript-root  directory with the main-agent transcripts (*.jsonl); env CAC_TRANSCRIPT_ROOT
    --codex-root       directory with second-product (Codex CLI) session logs; env CAC_CODEX_ROOT
    --mutations-file   JSONL of mutation records from an earlier extraction; env CAC_MUTATIONS_FILE
    --config           JSON config (project-specific names); env CAC_CONFIG, default config.json
    --results-dir      where derived tables are read from and written to; env CAC_RESULTS_DIR
    """
    p = argparse.ArgumentParser(description=description)
    if transcript_root:
        p.add_argument("--transcript-root", default=os.environ.get("CAC_TRANSCRIPT_ROOT"))
    if codex_root:
        p.add_argument("--codex-root", default=os.environ.get("CAC_CODEX_ROOT"))
    if mutations_file:
        p.add_argument("--mutations-file", default=os.environ.get("CAC_MUTATIONS_FILE"))
    p.add_argument("--config", default=os.environ.get("CAC_CONFIG", "config.json"))
    p.add_argument("--results-dir", default=os.environ.get("CAC_RESULTS_DIR", "results"))
    for args, kw in (extra or []):
        p.add_argument(*args, **kw)
    a = p.parse_args()
    for name in ("transcript_root", "codex_root", "mutations_file"):
        if hasattr(a, name) and not getattr(a, name):
            raise SystemExit(f"--{name.replace('_', '-')} (or its environment variable) is required")
    os.makedirs(a.results_dir, exist_ok=True)
    return a, load_config(a.config, need_config)
