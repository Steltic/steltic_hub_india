"""The live site: the base of a publish (india.html, the three index.json) and the copies the rollback keeps.

Read from a local mirror of public_html when one is given (a folder verified identical to the site, e.g. the previous
publish's tree), else fetched from the site itself (https://stelticai.com/). Any URL urllib opens works, so the tests
use file:// URLs and never reach the network.

verify() is the post-upload check: every file of the publish matches the live copy (size and SHA-256; a connection
reset is retried, one file at a time), and every relative link of the published pages and indexes resolves.
"""
from __future__ import annotations

import hashlib
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .util import BuildError

USER_AGENT = "steltic-site-build/1.0"


class Live:
    def __init__(self, mirror: str | None = None, url: str | None = None, timeout: float = 180.0, retries: int = 3,
                 log=print):
        self.mirror = mirror or None
        self.url = (url.rstrip("/") + "/") if url else None
        self.timeout, self.retries, self.log = timeout, retries, log
        self.fetched = 0
        if not self.mirror and not self.url:
            raise BuildError("the live site is needed (the base page, the indexes, the rollback copies): give a local "
                             "mirror of public_html or the site URL")
        if self.mirror and not os.path.isdir(self.mirror):
            raise BuildError("the live mirror %s is not a folder" % self.mirror)

    def describe(self) -> str:
        return " + ".join(x for x in ((("mirror " + self.mirror) if self.mirror else ""),
                                      (("site " + self.url) if self.url else "")) if x)

    def fetch(self, rel: str) -> bytes | None:
        """The file at <url>/<rel>, None when the site answers 404 (or there is no URL)."""
        if not self.url:
            return None
        u = self.url + urllib.parse.quote(rel)
        err = None
        for k in range(self.retries):
            try:
                req = urllib.request.Request(u, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = r.read()
                self.fetched += 1
                return data
            except urllib.error.HTTPError as e:
                if e.code in (404, 410):
                    return None
                err = e
            except urllib.error.URLError as e:
                if isinstance(e.reason, FileNotFoundError) or "No such file" in str(e.reason):
                    return None                          # file:// URL: the file is not there
                err = e
            except (ConnectionError, TimeoutError, OSError) as e:
                err = e
            time.sleep(1.5 * (k + 1))
        raise BuildError("the live copy of %s could not be read: %s" % (rel, err))

    def exists(self, rel: str) -> bool:
        """True when the site serves rel (HEAD; a server that refuses HEAD is asked with GET)."""
        if not self.url:
            return False
        u = self.url + urllib.parse.quote(rel)
        if not u.startswith(("http://", "https://")):
            return self.fetch(rel) is not None
        for k in range(self.retries):
            try:
                req = urllib.request.Request(u, method="HEAD", headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return 200 <= r.status < 300
            except urllib.error.HTTPError as e:
                if e.code in (404, 410):
                    return False
                if e.code in (405, 501):
                    return self.fetch(rel) is not None
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                pass
            time.sleep(1.5 * (k + 1))
        raise BuildError("the site could not be asked for %s" % rel)

    def get(self, rel: str) -> bytes | None:
        """The live copy of a site path (mirror first, then the site); None when the site has no such file."""
        if self.mirror:
            p = os.path.join(self.mirror, *rel.split("/"))
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    return f.read()
        return self.fetch(rel)

    def save(self, rel: str, dst: str) -> bool:
        data = self.get(rel)
        if data is None:
            return False
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        with open(dst, "wb") as f:
            f.write(data)
        return True


def verify(files: list[dict], url: str, pages: list[str] | None = None, pub_root: str | None = None, log=print,
           fetch=None, exists=None) -> dict:
    """The post-upload check. files: the manifest's [{path, bytes, sha256}]; pages: the published pages and
    indexes whose relative links are checked (paths relative to pub_root). Returns {'mismatch': [...],
    'missing': [...], 'links_bad': [...], 'checked': n, 'links_checked': n}."""
    live = Live(url=url, log=log)
    get = fetch or live.fetch
    has = exists or live.exists
    out = {"mismatch": [], "missing": [], "links_bad": [], "checked": 0, "links_checked": 0}
    for rec in files:
        data = get(rec["path"])
        out["checked"] += 1
        if data is None:
            out["missing"].append(rec["path"])
        elif len(data) != rec["bytes"] or hashlib.sha256(data).hexdigest() != rec["sha256"]:
            out["mismatch"].append(rec["path"])
    if pages and pub_root:
        from .selfcontained import relative_links
        import json
        targets = set()
        for rel in pages:
            p = os.path.join(pub_root, rel)
            if rel.endswith("index.json"):
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
                base = os.path.dirname(rel)
                for e in d.get("examples") or []:
                    for k, v in e.items():
                        if isinstance(v, str) and k != "id" and "/" in v and v.endswith((".html", ".std", ".csv", ".svg")):
                            targets.add(os.path.normpath(os.path.join(base, v)).replace(os.sep, "/"))
            else:
                with open(p, encoding="utf-8", errors="replace") as f:
                    text = f.read()
                for link, attr in relative_links(text):
                    if attr:
                        targets.add(os.path.normpath(os.path.join(os.path.dirname(rel), link.split("?")[0]))
                                    .replace(os.sep, "/"))
        for t in sorted(targets):
            if t.startswith("../"):
                continue
            out["links_checked"] += 1
            if not has(t):
                out["links_bad"].append(t)
    log("verify: %d file(s) checked, %d differ, %d missing; %d link(s) checked, %d bad"
        % (out["checked"], len(out["mismatch"]), len(out["missing"]), out["links_checked"], len(out["links_bad"])))
    return out
