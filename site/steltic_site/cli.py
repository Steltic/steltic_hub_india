"""The site build's command line.

    python site/build_site.py build   --config site/publishes/<publish>.json [options]
    python site/build_site.py verify  --manifest <out>/MANIFEST.json [--url https://stelticai.com/] [--no-links]
    python site/build_site.py scan    <public_html folder>
    python site/build_site.py compare --base <gold snapshot> --new <gold jobs> --hr-engine <steltic_india/steel_engine>

The publish config (JSON) names every input; `${NAME}` in a string is replaced by vars[NAME] (the config's own
"vars", overridden by the environment and by --var NAME=VALUE); a relative path is taken from the config file's
folder. Keys (see site/publishes/*.json):

  name, rollback_name   the zip base names
  out                   the build folder (public_html/, rollback/, the zips, MANIFEST.json, UPLOAD_INSTRUCTIONS.txt)
  self_contained        add the report figures and every viewer the pages link (default false: an overlay)
  parts                 ["page", "hr", "cfs", "nl"] (default: all)
  live.mirror           a local copy of the live public_html (the base page, the indexes, the rollback copies)
  live.url              the site, read for whatever the mirror lacks (https://stelticai.com/); none: offline
  page.base             the page to patch (default: the live india.html); page.patches: exact-match patch files,
                        each a path or {path, when_hr_ids / when_cfs_ids / when_nl_ids}: applied only when those ids
                        are published by the build (the example counts move with a new example)
  hr.jobs               the hot-rolled gold job folders; hr.titles: titles files (a path, or {path, optional: true}
                        for one that may not exist yet); hr.meta_notes {id: note}; hr.system_names {"SMRF": "SMF"};
                        hr.extra [{id, title, after, optional}] (new examples; optional: left out until the gold
                        job folder has its package)
  cfs.publish           the cold-formed publish folder (index.json + one folder per example)
  nl.runs               the base run folder (build_site_index --runs); nl.sources [{dir, copy: [extra files]}] in
                        priority order (a source may take only listed "ids", the ids present in its "marker_dir",
                        or only run folders holding its "require" files; its own "engine_dir"); nl.repo / nl.python / nl.engine_dir (the NL checkout, its interpreter, the HR
                        engine it uses); nl.rebuild_ddm (default true); nl.regenerate (`snl report` in the run folders,
                        default false); nl.titles; nl.extra_ids; nl.optional_ids (published once a source has the
                        run); nl.index (a prebuilt index instead of rebuilding it)
  repos                 {name: checkout} recorded in the manifest with their commit
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys

from . import cfs as CFS
from . import hr as HR
from . import live as LIVE
from . import nl as NL
from . import package as PKG
from . import page as PAGE
from . import scan as SCAN
from . import selfcontained as SC
from .util import BuildError, load_json, titles as load_titles

PARTS = ("page", "hr", "cfs", "nl")


# ---------------------------------------------------------------------------------------------------- the config
def _subst(v, vars_):
    if isinstance(v, str):
        def rep(m):
            k = m.group(1)
            if k not in vars_:
                raise BuildError("the config uses ${%s}, which is not set (config vars, the environment or --var)" % k)
            return str(vars_[k])
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", rep, v)
    if isinstance(v, list):
        return [_subst(x, vars_) for x in v]
    if isinstance(v, dict):
        return {k: _subst(x, vars_) for k, x in v.items()}
    return v


PATH_KEYS = {("out",), ("live", "mirror"), ("page", "base"), ("page", "patches"), ("hr", "jobs"), ("hr", "titles"),
             ("cfs", "publish"), ("nl", "runs"), ("nl", "repo"), ("nl", "engine_dir"), ("nl", "titles"),
             ("nl", "index"), ("hr", "index")}


def load_config(path: str, overrides: dict | None = None, var_args=None) -> dict:
    cfg = load_json(path) if path else {}
    vars_ = dict(cfg.get("vars") or {})
    for k in list(vars_):
        if k in os.environ:
            vars_[k] = os.environ[k]
    for kv in var_args or []:
        k, _, v = kv.partition("=")
        vars_[k] = v
    cfg = _subst({k: v for k, v in cfg.items() if k != "vars"}, vars_)
    base = os.path.dirname(os.path.abspath(path)) if path else os.getcwd()

    def absol(p):
        if isinstance(p, dict):                          # {"path": ..., ...}: a patch or titles entry with options
            return dict(p, path=absol(p.get("path")))
        return p if (not p or os.path.isabs(p) or "://" in p) else os.path.normpath(os.path.join(base, p))
    for keys in PATH_KEYS:
        d = cfg
        for k in keys[:-1]:
            d = d.get(k) if isinstance(d.get(k), dict) else None
            if d is None:
                break
        if d is None or keys[-1] not in d or d[keys[-1]] is None:
            continue
        v = d[keys[-1]]
        d[keys[-1]] = [absol(x) for x in v] if isinstance(v, list) else absol(v)
    for s in (cfg.get("nl") or {}).get("sources") or []:
        s["dir"] = absol(s["dir"])
        for k in ("marker_dir", "engine_dir"):
            if s.get(k):
                s[k] = absol(s[k])
    for k, v in (cfg.get("repos") or {}).items():
        cfg["repos"][k] = absol(v)
    for k, v in (overrides or {}).items():
        if v is None:
            continue
        d = cfg
        ks = k.split(".")
        for kk in ks[:-1]:
            d = d.setdefault(kk, {})
        d[ks[-1]] = v
    return cfg


def _git(path):
    try:
        sha = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
        dirty = subprocess.run(["git", "-C", path, "status", "--porcelain", "--untracked-files=no"], capture_output=True,
                               text=True, timeout=60)
        return {"path": path, "commit": sha.stdout.strip() or None, "dirty": bool(dirty.stdout.strip())}
    except (OSError, subprocess.SubprocessError):
        return {"path": path, "commit": None}


# ---------------------------------------------------------------------------------------------------- build
def build(cfg: dict, log=print) -> dict:
    name = cfg.get("name") or "steltic_india_publish"
    rb_name = cfg.get("rollback_name") or ("steltic_india_ROLLBACK_before_" + name)
    out = cfg.get("out")
    if not out:
        raise BuildError("no out folder (config 'out' or --out)")
    parts = list(cfg.get("parts") or PARTS)
    bad = [p for p in parts if p not in PARTS]
    if bad:
        raise BuildError("unknown part(s) %s (parts are %s)" % (bad, ", ".join(PARTS)))
    lv = cfg.get("live") or {}
    live = LIVE.Live(mirror=lv.get("mirror"), url=lv.get("url"), log=log)
    pub = os.path.join(out, "public_html")
    rb = os.path.join(out, "rollback")
    for d in (pub, rb):
        if os.path.exists(d):
            shutil.rmtree(d)
    os.makedirs(os.path.join(pub, "solutions_india"))
    inputs = os.path.join(out, "inputs")
    os.makedirs(inputs, exist_ok=True)
    checks, sources, report, notes = [], {}, {"parts": parts}, []

    def live_file(rel, required=True):
        dst = os.path.join(inputs, "live_" + rel.replace("/", "_"))
        if live.save(rel, dst):
            return dst
        if required:
            raise BuildError("the live site has no %s (%s)" % (rel, live.describe()))
        return None

    ids = {"hr": [], "cfs": [], "nl": []}
    # -- hot-rolled
    if "hr" in parts:
        h = cfg.get("hr") or {}
        idx = h.get("index") or live_file("solutions_india/hr/index.json")
        ents, probs, src = HR.build(load_json(idx), h["jobs"], os.path.join(pub, "solutions_india", "hr"),
                                    titles=load_titles(h.get("titles"), notes), meta_notes=h.get("meta_notes"),
                                    system_names=h.get("system_names"), extra=h.get("extra"), notes=notes,
                                    log=log)
        ids["hr"] = [e["id"] for e in ents]
        checks += ["PROBLEM hot-rolled: " + p for p in probs]
        sources.update({"solutions_india/hr/" + k: v for k, v in src.items()})
    # -- cold-formed
    if "cfs" in parts:
        ents, probs, src = CFS.build(cfg["cfs"]["publish"], os.path.join(pub, "solutions_india", "cfs"), log=log)
        ids["cfs"] = [e["id"] for e in ents]
        checks += ["PROBLEM cold-formed: " + p for p in probs]
        sources.update({"solutions_india/cfs/" + k: v for k, v in src.items()})
    # -- nonlinear
    if "nl" in parts:
        n = cfg.get("nl") or {}
        prev = live_file("solutions_india/nl/index.json")
        nl_ids = [e["id"] for e in load_json(prev).get("examples") or []]
        nl_ids += [x for x in n.get("extra_ids") or [] if x not in nl_ids]
        srcs = n.get("sources") or [{"dir": n["runs"]}]
        for x in n.get("optional_ids") or []:
            # a run that may not exist yet (a new example's nonlinear run): published once a source has it
            if x not in nl_ids:
                if NL.pick_source(x, srcs)[0]:
                    nl_ids.append(x)
                else:
                    notes.append("NOTE nonlinear: %s has no run folder in the sources yet: left out" % x)
        ents, probs, src = NL.build(nl_ids, os.path.join(pub, "solutions_india", "nl"), sources=srcs, runs=n["runs"],
                                    previous_index=prev, nl_repo=n.get("repo"), python=n.get("python"),
                                    engine_dir=n.get("engine_dir"), prebuilt_index=n.get("index"),
                                    rebuild_ddm=bool(n.get("rebuild_ddm", True)), regenerate=bool(n.get("regenerate")),
                                    titles=load_titles(n.get("titles"), notes), work_dir=inputs, log=log)
        ids["nl"] = [e["id"] for e in ents]
        checks += ["PROBLEM nonlinear: " + p for p in probs]
        sources.update({"solutions_india/nl/" + k: v for k, v in src.items()})
    # -- the page
    if "page" in parts:
        pg = cfg.get("page") or {}
        base = pg.get("base") or live_file("india.html")
        # what the site will list: the indexes this build publishes (or, for a part not rebuilt, the live ones)
        cnt = {}
        for k in ("hr", "cfs", "nl"):
            if ids[k]:
                cnt[k] = ids[k]
            else:
                p = live_file("solutions_india/%s/index.json" % k, required=False)
                cnt[k] = [e["id"] for e in load_json(p).get("examples") or []] if p else []
        done = PAGE.patch_page(base, pg.get("patches"), os.path.join(pub, "india.html"), published=cnt)
        report["page"] = {"base": base, "patches": done}
        for line in done:
            log("page: " + line)
        notes += [x for x in done if x.startswith("NOTE")]
        with open(os.path.join(pub, "india.html"), encoding="utf-8") as f:
            text = f.read()
        checks += PAGE.count_checks(text, cnt["hr"], cnt["cfs"], cnt["nl"])
        report["page_assets"] = SC.page_assets(os.path.join(pub, "india.html"))
    if not os.listdir(os.path.join(pub, "solutions_india")):
        os.rmdir(os.path.join(pub, "solutions_india"))
    # -- self-contained
    added, missing = [], []
    if cfg.get("self_contained"):
        added, missing = SC.complete(pub, sources, log=log)
    else:
        missing = _dangling(pub)
        if missing:
            log("overlay: %d link(s) of the pages point at files not in this zip (they must be on the site already)"
                % len(missing))
    report["self_contained_added"] = len(added)
    if cfg.get("self_contained"):
        checks += ["MISSING %s links %s (in neither the build nor its source)" % m for m in missing]
    # -- public text
    hits = SCAN.scan(pub, log=log, max_lines=40)
    checks += ["CHECK public text: %s [%s]: ...%s..." % h for h in hits]
    # -- package
    PKG.set_permissions(pub)
    zip_path = os.path.join(out, name + ".zip")
    zinfo = PKG.make_zip(pub, zip_path)
    rinfo = PKG.rollback(pub, live, rb, log=log)
    rb_path = os.path.join(out, rb_name + ".zip")
    PKG.make_zip(rb, rb_path, tops=("india.html", "solutions_india", PKG.NEW_LIST))
    notes = list(dict.fromkeys(notes))                  # a titles file read for two parts is noted once
    for x in notes:
        log(x)
    checks = notes + checks
    info = {"built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "self_contained": bool(cfg.get("self_contained")), "parts": parts, "live": live.describe(),
            "pub_root": pub, "zip": zip_path, "rollback_zip": rb_path, "checks": checks,
            "counts": {k: len(v) for k, v in ids.items()}, "page": report.get("page"),
            "page_assets": report.get("page_assets") or [], "self_contained_added": added,
            "overlay_links_to_site": [list(m) for m in missing] if not cfg.get("self_contained") else [],
            "repos": {k: _git(v) for k, v in (cfg.get("repos") or {}).items()},
            "config": {k: v for k, v in cfg.items() if k != "repos"}}
    man = PKG.manifest(pub, name=name, info=info, new=rinfo["new"], sources=sources)
    PKG.write_json(man, os.path.join(out, "MANIFEST.json"))
    site_url = (lv.get("url") or "https://stelticai.com/")
    verify_cmd = "python site/build_site.py verify --manifest %s --url %s" % (os.path.join(out, "MANIFEST.json"), site_url)
    txt = PKG.instructions(name=name, zip_path=zip_path, zip_info=zinfo, rb_path=rb_path, rb_info=rinfo, man=man,
                           checks=checks, assets=report.get("page_assets") or [], site_url=site_url,
                           self_contained=bool(cfg.get("self_contained")), verify_cmd=verify_cmd)
    with open(os.path.join(out, "UPLOAD_INSTRUCTIONS.txt"), "w", encoding="utf-8") as f:
        f.write(txt)
    log("zip: %s (%d files, %.1f MB)" % (zip_path, zinfo["files"], zinfo["zip_bytes"] / 1e6))
    log("rollback: %s" % rb_path)
    log("instructions: %s" % os.path.join(out, "UPLOAD_INSTRUCTIONS.txt"))
    log("checks: %d" % len(checks))
    return {"zip": zip_path, "rollback": rb_path, "manifest": man, "checks": checks, "zip_info": zinfo,
            "rollback_info": rinfo, "added": added, "missing": missing}


def _dangling(pub):
    """[(page, link)] of the relative links of the published pages whose target is not in the build."""
    out = []
    for dp, dn, fn in os.walk(pub):
        for f in fn:
            if not SC.is_page(f) or os.path.getsize(os.path.join(dp, f)) > SC.MAX_PAGE_BYTES:
                continue
            p = os.path.join(dp, f)
            if os.path.relpath(p, pub) == "india.html":
                continue
            with open(p, encoding="utf-8", errors="replace") as fh:
                t = fh.read()
            for link, attr in SC.relative_links(t):
                tgt = os.path.normpath(os.path.join(dp, link))
                if attr and not os.path.exists(tgt) and not os.path.relpath(tgt, pub).startswith(".."):
                    out.append((os.path.relpath(p, pub).replace(os.sep, "/"), link))
    return out


# ---------------------------------------------------------------------------------------------------- commands
def cmd_build(a):
    over = {"out": a.out, "name": a.name, "live.mirror": a.live_mirror, "live.url": a.live_url,
            "parts": [x.strip() for x in a.parts.split(",")] if a.parts else None}
    if a.self_contained is not None:
        over["self_contained"] = a.self_contained
    cfg = load_config(a.config, over, a.var)
    if a.offline:
        (cfg.get("live") or {}).pop("url", None)
    if not cfg.get("out"):
        raise BuildError("no out folder (config 'out' or --out)")
    r = build(cfg)
    return 0 if not any(c.startswith(("PROBLEM", "MISSING")) for c in r["checks"]) else 3


def cmd_verify(a):
    man = load_json(a.manifest)
    url = a.url or (man.get("config") or {}).get("live", {}).get("url") or "https://stelticai.com/"
    pages = None
    pub = man.get("pub_root")
    if not a.no_links and pub and os.path.isdir(pub):
        pages = [f["path"] for f in man["files"] if f["path"].endswith(("index.json",)) or
                 (SC.is_page(os.path.basename(f["path"])) and f["bytes"] <= SC.MAX_PAGE_BYTES)]
    r = LIVE.verify(man["files"], url, pages=pages, pub_root=pub)
    for k in ("mismatch", "missing", "links_bad"):
        for x in r[k][:50]:
            print("%s %s" % (k.upper(), x))
    return 0 if not (r["mismatch"] or r["missing"] or r["links_bad"]) else 1


def cmd_scan(a):
    hits = SCAN.scan(a.root, max_lines=a.max)
    return 0 if not hits else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="build_site.py", description="Build the Steltic India pages of the site.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build the upload zip, the rollback zip and the upload instructions")
    b.add_argument("--config", required=True, help="the publish config (site/publishes/*.json)")
    b.add_argument("--out", help="the build folder (overrides the config)")
    b.add_argument("--name", help="the zip base name (overrides the config)")
    b.add_argument("--live-mirror", help="a local copy of the live public_html")
    b.add_argument("--live-url", help="the site (read for what the mirror lacks)")
    b.add_argument("--offline", action="store_true", help="never read the site: the mirror only")
    g = b.add_mutually_exclusive_group()
    g.add_argument("--self-contained", dest="self_contained", action="store_true", default=None,
                   help="add the report figures and every viewer the pages link")
    g.add_argument("--overlay", dest="self_contained", action="store_false", help="only the changed files")
    b.add_argument("--parts", help="comma-separated parts to build (page,hr,cfs,nl)")
    b.add_argument("--var", action="append", default=[], metavar="NAME=VALUE", help="set a config variable")
    b.set_defaults(fn=cmd_build)
    v = sub.add_parser("verify", help="after the upload: every file matches the live copy, every link resolves")
    v.add_argument("--manifest", required=True)
    v.add_argument("--url")
    v.add_argument("--no-links", action="store_true")
    v.set_defaults(fn=cmd_verify)
    s = sub.add_parser("scan", help="the public-text scan of a public_html folder")
    s.add_argument("root")
    s.add_argument("--max", type=int, default=200)
    s.set_defaults(fn=cmd_scan)
    c = sub.add_parser("compare", help="the gold before / after comparison", add_help=False)
    c.set_defaults(fn=None)
    a, rest = ap.parse_known_args(argv)
    if a.cmd == "compare":
        from . import compare
        return compare.main(rest)
    if rest:
        ap.error("unrecognized arguments: %s" % " ".join(rest))
    try:
        return a.fn(a)
    except BuildError as e:
        print("BUILD STOPPED: %s" % e, file=sys.stderr)
        return 2
