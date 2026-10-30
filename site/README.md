# site/ — the build of the Steltic India pages of stelticai.com

One command builds, from the gold outputs of the repos, everything a publish of `india.html` and `solutions_india/`
needs: the upload zip, the rollback zip (the live copy of every file the upload replaces) and the upload
instructions. It replaces the October 2026 scripts that lived outside every repo (`build_hr_wave2.py`,
`patch_india2.py`, `build_nl2.sh`, `package2.sh`, `package2b.sh`), with their paths as inputs.

```
python site/build_site.py build --config site/publishes/2026-10_third_wave.json [--self-contained] [--out <folder>]
python site/build_site.py verify --manifest <out>/MANIFEST.json           # after the upload
python site/build_site.py scan <out>/public_html                         # the public-text scan alone
python site/build_site.py compare --base <gold snapshot> --new <gold jobs> --hr-engine <steltic_india/steel_engine>
```

## Inputs (the publish config, `site/publishes/*.json`)

| key | what |
|---|---|
| `live.mirror`, `live.url` | the live site: a local copy of `public_html` and/or the site itself. It gives the base page, the three `index.json` (titles, order, links) and the rollback copies. With `--offline` the site is never read. |
| `page.base`, `page.patches` | the page to patch (default: the live `india.html`) and the exact-match patch files applied to it in order (`site/patches/`) |
| `hr.jobs`, `hr.titles`, `hr.meta_notes`, `hr.system_names`, `hr.extra` | the hot-rolled gold job folders, the titles files, a note added to a meta line, the lateral-system words the site uses (`{"SMRF": "SMF"}`), new examples not on the site yet (`{id, title, after}`) |
| `cfs.publish` | the cold-formed publish folder steltic_CFS_india builds (`index.json` and one folder per example) |
| `nl.runs`, `nl.sources`, `nl.repo`, `nl.python`, `nl.engine_dir`, `nl.titles` | the nonlinear run folders (re-runs first), the steltic_nonlinear_india checkout and interpreter, the HR engine it uses; `nl.rebuild_ddm` (default on) rebuilds each `ddm_report.html` into a temporary folder; `nl.regenerate` runs `snl report` in the run folders first (it writes there: off by default) |
| `self_contained` | add the report figures and every viewer the pages link, so the zip alone reproduces the India pages |
| `vars` | `${NAME}` in any string; the environment and `--var NAME=VALUE` override them |

## Outputs (in `out`)

- `<name>.zip`: paths `india.html` and `solutions_india/…`, folders 755, files 644, sorted entries (with
  `SOURCE_DATE_EPOCH` set, the same inputs give the same bytes);
- `<rollback_name>.zip`: the live copy of every replaced file and `NEW_FILES_TO_DELETE_ON_ROLLBACK.txt`;
- `UPLOAD_INSTRUCTIONS.txt`: cPanel upload, the post-upload check, the rollback, the build's checks;
- `MANIFEST.json`: every file with its size and SHA-256 (what `verify` compares with the site), the inputs, the
  repos' commits and the checks; `public_html/` and `rollback/`, the trees the zips were made from.

## Checks the build prints (and lists in the instructions)

- `PROBLEM`: an example not complete, an index link that does not resolve; `MISSING` (self-contained): a page links
  a file found in neither the build nor its source. Either makes the exit code 3.
- `CHECK page says …`: a count the page states in prose ("39 worked Indian designs", "20 Indian example briefs …
  plus four sub-units", "24 nonlinear runs") no longer matches what the build publishes: write the page patch.
- `CHECK public text`: a file path, "EOR", "owner ruling", a ticket id, a raw dict, a cfg key, a JSON or script name,
  the old IS 875 (Part 1):1987, "(was None)" in a page, report or index (report only; the viewers and the HR reports'
  activity log are not read).

## The page patch

`site/patches/*.patch` hold blocks applied in order; each FIND must occur exactly once in the page as it stands (the
earlier publishes' `rep()` discipline), or the build stops:

```
@@@ FIND
<exact text of the live page>
@@@ REPLACE
<new text>
@@@ END
```

`india_2026-10_second_wave.patch` is the second-wave patch: applied to the first-wave page it gives the page live since
2026-10-24 byte for byte. `india_2026-10_third_wave.patch` is the next one (empty until the third-wave text lands).

## Tools

- `tools/live_page_check.py`: the live page in a headless browser (page errors, failed requests, the list counts).
- `tools/heavy.sh`: the slot runner for analyses on a small box (`HEAVY_LOCKS`, `HEAVY_SLOTS`).

Tests: `tests/test_site_build.py` (fixtures only, no network).
