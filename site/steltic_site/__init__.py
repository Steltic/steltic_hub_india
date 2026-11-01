"""steltic_site -- the build of the Steltic India pages of stelticai.com (india.html and solutions_india/).

One command builds, from the repos' gold outputs, the upload zip, the rollback zip (the live copy of every file the
upload replaces) and the upload instructions:

    python site/build_site.py build --config site/publishes/<publish>.json [--out <folder>] [--self-contained]

Parts (each a module here):
  page.py           india.html: the live page patched by exact-match patch files (site/patches/*.patch)
  hr.py             solutions_india/hr/: index.json and the per-example files from the hot-rolled gold job folders
  cfs.py            solutions_india/cfs/: the cold-formed publish folder (built by steltic_CFS_india), copied
  nl.py             solutions_india/nl/: the nonlinear pages and index.json from the nonlinear run folders
  selfcontained.py  the report figures and every viewer the pages link (optional: --self-contained)
  package.py        permissions, the zips, the rollback set, the manifest and the upload instructions
  live.py           the live site: a local mirror or the site itself (fetched; file:// in tests); post-upload check
  scan.py           the public-text scan of every page, report and index
  compare.py        the gold before / after comparison (status, ratios, drift, kg/m2, sizes)

Nothing here analyses anything, and nothing reaches the network unless a live URL is given.
"""
__version__ = "1.0.0"
