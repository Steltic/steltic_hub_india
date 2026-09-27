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

## Commits (oldest first)
- USA-SYNC f1b1708: Nonlinear Review tab, adapted for India: IS corpus, no verdict
- USA-SYNC cf81db7: tab actions, Revise on the NL Run tab, running rail, unix launcher, adapted for India
- USA-SYNC b97999a: actions declare the fields they pass, no default search cap on Review, adapted for India
- USA-SYNC 9e0f596: Collect-before-Run gate on hinge_params_collected.json, adapted for India: IS values, IS corpus
