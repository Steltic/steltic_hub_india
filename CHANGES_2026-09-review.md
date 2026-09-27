# IS corpus: built from your own licensed PDFs, nothing to clone (2026-09-27)

The IS corpus is no longer cloned from a separate repository: BIS standards are licensed and are not
redistributed. The hub ships the corpus tooling. Each user builds the corpus from PDFs they licensed.

- **The module is bundled.**
  - The module id `engineering_rag_india` is kept, so `server.requires` and
    `{server.engineering_rag_india}` in HR, CFS and NL are unchanged.
  - It is named "IS corpus (your own conversions)".
  - `source.bundled`: no url, no clone, no private flag.
- **The tooling is bundled** in `catalog/engineering_rag_india/scripts/` without licensed data:
  - no transcriptions, section tables, figure data or per-stem notes;
  - such data files are optional, and the corpus-fix step supplies them.
- **An empty corpus on install.**
  - `post_install` lays out an empty workspace and builds an empty index.
  - The grounding server answers "not in the corpus" on it.
  - An update refreshes `scripts/*.py` only; corpus data and `scripts/*.json` stay.
- **The workflow** (README *Building the IS corpus*):
  1. the first pass in the hub (Admin → Standards or Convert, Rebuild index, Validate corpus);
  2. the corpus fix by a frontier LLM agent, with `CORPUS_FIX_LLM_INSTRUCTIONS.md` (new, at the root);
  3. **Import fixed corpus**, a new tab: it checks, backs up, replaces, rebuilds and validates.
- **Rebuild index**:
  - skips the per-document PDF repair when the PDF is not found;
  - has a *Skip the per-document repair* checkbox.
- **Validate corpus** probes only the stems you converted, with id lookups and short key phrases.
- **Admin → Standards**: the text now describes the first pass. Its default folder is `grokbot/pdfs`.

# steltic-hub-india 0.2.2: USA sync (2026-09-27)

Branch `fix/2026-09-review`, based on the delivered zip (India 0.1.0, which was already at USA 9997de4 in substance).

- **fd1b3fe (USA f1b1708):** added the Nonlinear Review tab. It runs against the India corpus (`{server.engineering_rag_india}`), uses IS wording and gives no verdict.
- **7d6ba3a (USA cf81db7):**
  - tab actions: Revise reports on the NL Run tab, and the running rail;
  - Unix launcher `unix/steltic_india.sh` with a `.desktop` entry and `install.sh`: India data dir, port 8301, and `logs/` created first;
  - the Revise action queries the IS corpus.
- **c9a2cc3 (USA b97999a):** actions declare their fields, which fixes the Revise crash; the 8-search review default is dropped.
- **70f075c (USA 9e0f596):**
  - Collect-before-Run gate: `run.requires hinge_params_collected.json`;
  - the Collect action queries the IS corpus and the package;
  - hinge backbones are labelled as modelling assumptions.
- **Skipped:** USA fbb5508 and 30dbaa3. Their net effect is a re-escaped US HR catalog.
- **Already in:** USA 9997de4.
- **Version:** 0.1.0 → 0.2.2.

**Known issue (not fixed):** the Windows launcher installs the US `steltic-hub` package when not run from a checkout.

## Commits (oldest first; subjects only — hashes change when the branch is replayed onto GitHub)
- USA-SYNC f1b1708: Nonlinear Review tab, adapted for India: IS corpus, no verdict
- USA-SYNC cf81db7: tab actions, Revise on the NL Run tab, running rail, unix launcher, adapted for India
- USA-SYNC b97999a: actions declare the fields they pass, no default search cap on Review, adapted for India
- USA-SYNC 9e0f596: Collect-before-Run gate on hinge_params_collected.json, adapted for India: IS values, IS corpus
- Tests (Windows): simulate a native crash with ExitProcess(NTSTATUS); clear developer RAG/LLM env in the event-line test
- IS corpus module: bundle the corpus tooling, without the licensed data
- IS corpus module ships with the hub: an empty corpus you build yourself, nothing to download
- IS corpus: Import fixed corpus tab (scripts/import_corpus_zip.py)
- Admin: the standards queue is the corpus's first pass, the corpus is built from your own PDFs
- Add CORPUS_FIX_LLM_INSTRUCTIONS.md; align validate and import with it
- README, CHANGES: the IS corpus is built from your own licensed PDFs; the recommended workflow
- IS corpus serve_http.py: the corpus is a workspace, not a checkout
- CORPUS_FIX_LLM_INSTRUCTIONS.md: final version (matches the hub's bundled corpus module: validate probes, optional scripts/*.json, Import fixed corpus tab)
