#!/usr/bin/env python3
"""Private drafts and checked, atomic publication for generation and questions.

The manifest fixes the output path BEFORE an agent starts. Failed drafts stay in
logs/readpaper-work; only publish() writes the public HTML. This is an application
transaction, not an OS sandbox for an agent running with unrestricted tools.
"""
from __future__ import annotations

import argparse
import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import check_html


SLUG_MAX = 60


def slugify(text: str) -> str:
    """ASCII kebab-case, cut at a word boundary; "" if it has no letter."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    slug = ""
    for w in words:
        nxt = f"{slug}-{w}" if slug else w
        if len(nxt) > SLUG_MAX:
            break
        slug = nxt
    return slug if len(slug) >= 3 and re.search(r"[a-z]", slug) else ""


class PublicationError(RuntimeError):
    """A draft cannot safely replace the public page."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Document(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.opened = set()
        self.closed = set()
        self.images = []
        self.utf8 = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self.opened.add(tag)
        attrs = dict(attrs)
        if tag == "meta":
            self.utf8 |= attrs.get("charset", "").lower() == "utf-8"
            self.utf8 |= "charset=utf-8" in attrs.get("content", "").lower()
        if tag == "img" and attrs.get("src"):
            self.images.append(attrs["src"])

    def handle_endtag(self, tag: str) -> None:
        self.closed.add(tag)


class Publication:
    """A fixed public destination, its starting revision, and a private draft."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = Path(workdir).resolve()
        self.record = json.loads((self.workdir / "publication.json").read_text())
        self.root = Path(self.record["root"])
        self.target = self.root / self.record["name"]
        self.stage = self.workdir / "html-expl" / self.record["name"]
        self.asset_rel = self.record["asset_rel"]
        self.assets = self.workdir / "html-expl" / self.asset_rel
        self.baseline = self.workdir / "before.html"
        self.slug_file = self.workdir / "slug.txt"

    def _descriptive_name(self, text: str) -> str:
        """YYYYMMDD-<slug>.html from the agent's slug.txt, else the <title>.

        Generation drafts under a placeholder name; the public name is chosen
        only now, so it never overwrites an existing page (suffix -2, -3, ...).
        """
        slug = ""
        if self.slug_file.is_file() and not self.slug_file.is_symlink():
            slug = slugify(self.slug_file.read_text(encoding="utf-8", errors="replace")[:200])
        if not slug:
            m = re.search(r"<title>([^<]*)</title>", text, re.I)
            # Drop arXiv ids etc.; keep the first few English words of the title.
            words = re.findall(r"[A-Za-z][A-Za-z0-9]*", m.group(1) if m else "")
            slug = slugify(" ".join(w for w in words if w.lower() != "arxiv")[:SLUG_MAX])
        if not slug:
            return self.record["name"]
        date = self.record["date"]
        name, i = f"{date}-{slug}.html", 2
        while (self.root / name).exists() or (self.root / name).is_symlink():
            name, i = f"{date}-{slug}-{i}.html", i + 1
        return name

    def _validate(self, candidate: Path) -> str:
        # Work on a frozen copy, so the exact bytes checked are the bytes published.
        info = check_html.analyse(str(candidate))
        check_html.apply_fixes(str(candidate), info)
        info = check_html.analyse(str(candidate))
        if info["findings"]:
            raise PublicationError("HTML check: " + ", ".join(
                f["id"] for f in info["findings"]))
        text = candidate.read_text(encoding="utf-8")
        doc = Document()
        doc.feed(text)
        required = {"html", "head", "title", "body", "h1"}
        if not required <= doc.opened or not required <= doc.closed or not doc.utf8:
            raise PublicationError("incomplete HTML: need UTF-8 and closed html/head/title/body/h1")
        for src in doc.images:
            url = urlsplit(src)
            if url.scheme or url.netloc or not url.path:
                continue
            relative = unquote(url.path).lstrip("/")
            public = (self.root / relative).resolve()
            if not public.is_relative_to(self.root):
                raise PublicationError(f"image path outside public root: {src}")
            staged = (self.stage.parent / relative).resolve()
            if not staged.is_relative_to(self.stage.parent):
                raise PublicationError(f"image path outside draft root: {src}")
            # A generation may supply only its own new, versioned asset directory.
            own_asset = self.asset_rel and relative.startswith(self.asset_rel + "/")
            found = staged if own_asset and staged.is_file() else public
            if not found.is_file() or not found.stat().st_size:
                raise PublicationError(f"missing image: {src}")
        return text

    def publish(self) -> str:
        """Validate a frozen draft, reject stale edits, and atomically replace HTML."""
        try:
            if self.stage.is_symlink():
                raise PublicationError("draft must not be a symlink")
            data = self.stage.read_bytes()
            data.decode("utf-8", errors="strict")
            fd, name = tempfile.mkstemp(prefix="validated-", suffix=".html", dir=self.workdir)
            candidate = Path(name)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            after = self._validate(candidate)
            if self.record.get("rename"):
                self.target = self.root / self._descriptive_name(after)
            locks = self.workdir.parent / "locks"
            locks.mkdir(exist_ok=True)
            key = digest(str(self.target).encode())
            with (locks / f"{key}.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if self.target.is_symlink():
                    raise PublicationError("public target must not be a symlink")
                current = digest(self.target.read_bytes()) if self.target.exists() else None
                if current != self.record["expected"]:
                    raise PublicationError("public page changed during this run; draft kept, retry from the new version")
                if self.asset_rel and self.assets.exists():
                    public_assets = self.root / self.asset_rel
                    if public_assets.exists():
                        raise PublicationError("asset revision already exists")
                    public_assets.parent.mkdir(parents=True, exist_ok=True)
                    # Copy first: validation failures and retries retain the private assets.
                    shutil.copytree(self.assets, public_assets)
                # Candidate and destination are on the same filesystem. Readers get
                # either the whole old page or the whole validated new page.
                os.chmod(candidate, 0o644)
                os.replace(candidate, self.target)
            return after
        except (OSError, UnicodeError, ValueError) as exc:
            raise PublicationError(str(exc)) from exc


def _prepare(project: Path, root: Path, name: str, dest: str = "", rename: bool = False) -> Publication:
    project, root = Path(project).resolve(), Path(root).resolve()
    if Path(name).name != name or not name.endswith(".html") or name.startswith((".", "_")) or name == "index.html":
        raise PublicationError("invalid explainer filename")
    target = root / name
    if target.is_symlink():
        raise PublicationError("public target must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    workdir = project / "logs" / "readpaper-work" / run_id
    if workdir.is_relative_to(root):
        raise PublicationError("draft directory must be outside the served root")
    (workdir / "html-expl").mkdir(parents=True)
    before = target.read_bytes() if target.exists() else None
    if before is not None:
        (workdir / "before.html").write_bytes(before)
    record = {"root": str(root), "name": name,
              "expected": digest(before) if before is not None else None,
              "asset_rel": f"figs/{dest}/{run_id}" if dest else "",
              "rename": rename and before is None,
              "date": f"{datetime.date.today():%Y%m%d}"}
    (workdir / "publication.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return Publication(workdir)


def prepare_edit(project: Path, root: Path, name: str) -> Publication:
    """Snapshot a page and put its editable copy outside the HTTP document root."""
    work = _prepare(project, root, name)
    if not work.baseline.exists():
        raise PublicationError("public page is missing")
    shutil.copy2(work.baseline, work.stage)
    return work


def prepare_generation(project: Path, dest: str, target: str = "") -> Publication:
    """Choose the exact generation output before invoking the agent."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", dest):
        raise PublicationError("invalid paper identity")
    root = Path(project).resolve() / "html-expl"
    if target:
        requested = Path(target)
        if not requested.is_absolute():
            requested = Path(project) / requested
        if requested.parent.resolve() != root:
            raise PublicationError("generation target must be directly under html-expl")
        name = requested.name
        return _prepare(project, root, name, dest)
    # Placeholder only: publish() renames to YYYYMMDD-<slug>.html (see slug.txt).
    name = f"{datetime.date.today():%Y%m%d}-readpaper-{dest}.html"
    return _prepare(project, root, name, dest, rename=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("project")
    prepare.add_argument("dest")
    prepare.add_argument("--target", default="")
    publish = commands.add_parser("publish")
    publish.add_argument("workdir")
    paths = commands.add_parser("paths")
    paths.add_argument("workdir")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            print(prepare_generation(Path(args.project), args.dest, args.target).workdir)
        else:
            work = Publication(Path(args.workdir))
            if args.command == "publish":
                work.publish()
                print(work.target)
            else:
                print(work.stage)
                print(work.assets)
                print(work.asset_rel)
                print(work.slug_file)
        return 0
    except (PublicationError, OSError, ValueError) as exc:
        print(f"publication failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
