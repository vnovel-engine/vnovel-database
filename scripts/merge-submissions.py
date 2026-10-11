#!/usr/bin/env python3
"""Aggregate submitted game() blocks into the canonical vnovel.dat.

Submissions arrive as clrmamepro fragments (from `tools/hash-game.py
--submit`, or pasted into an issue/email). They carry provenance comments,
which are stripped here: the canonical database keeps only the fields that
matter and is emitted in a stable order.

Two records with the same crc are the same content. The first-seen name wins
unless --prefer-non-auto is given, in which case a name that did NOT come
from the "exe basename" fallback beats one that did (a real game.id beats a
guess). Conflicts are reported so a human can adjudicate.

Usage:
    tools/merge-submissions.py dat/vnovel.dat submissions/*.dat -o dat/vnovel.dat
    tools/merge-submissions.py dat/vnovel.dat submission.dat          # to stdout
"""

import argparse
import importlib.util
import os
import sys

# make-rdb.py owns the clrmamepro scanner (and already skips // comments);
# importing it here keeps one parser rather than two that can drift. The
# module name has a hyphen, so load it by path.
_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "make_rdb", os.path.join(_here, "make-rdb.py"))
_make_rdb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_make_rdb)
parse_games = _make_rdb.parse_dat

# Fields we keep, in the order they are written. Provenance comments are
# dropped; unknown fields are not emitted (the submission format only uses
# the ones below).
KEEP = ("name", "description", "developer", "publisher", "releaseyear",
        "origin", "franchise")
ROM_FIELDS = ("name", "size", "crc", "md5", "sha1")


def crc_of(game):
    for rom in game.get("rom", []):
        c = (rom.get("crc") or "").strip().upper()
        if c:
            return c
    return None


def emit(game):
    """Render one merged record."""
    lines = ["game ("]
    for k in KEEP:
        if game.get(k):
            lines.append(f'\t{k} "{game[k]}"')
    for rom in game.get("rom", []):
        if not rom.get("name"):
            continue
        parts = [f'name {rom["name"]}']
        for f in ("size", "crc", "md5", "sha1"):
            if rom.get(f):
                parts.append(f'{f} {rom[f]}')
        lines.append("\trom ( " + " ".join(parts) + " )")
    lines.append(")")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("canonical", help="the current dat/vnovel.dat")
    ap.add_argument("submissions", nargs="+", help="submitted fragments")
    ap.add_argument("-o", "--output", help="write here (default: stdout)")
    ap.add_argument("--prefer-non-auto", action="store_true",
                    help="prefer a name not derived from the exe-basename "
                         "fallback when two submissions disagree")
    args = ap.parse_args()

    merged = {}
    order = []
    conflicts = 0

    def auto_name(g):
        return "exe basename" in (g.get("_source") or "")

    def add(game, path):
        nonlocal conflicts
        crc = crc_of(game)
        if not crc:
            print(f"{path}: skipped a game() with no crc", file=sys.stderr)
            return
        if crc not in merged:
            merged[crc] = game
            order.append(crc)
            return
        old = merged[crc]
        if old.get("name") != game.get("name"):
            conflicts += 1
            keep = old
            if args.prefer_non_auto and auto_name(old) and not auto_name(game):
                keep = merged[crc] = game
            print(f"{path}: crc {crc} already known as {old.get('name')!r}; "
                  f"submission says {game.get('name')!r} -> keeping "
                  f"{keep.get('name')!r}", file=sys.stderr)

    add_all = [(args.canonical,)], [(p,) for p in args.submissions]
    for (path,) in [x for t in add_all for x in t]:
        text = open(path, encoding="utf-8", errors="replace").read()
        for g in parse_games(text):
            add(g, path)

    out = "\n\n".join(emit(merged[c]) for c in order) + "\n"
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"{args.output}: {len(order)} records"
              + (f", {conflicts} name conflict(s)" if conflicts else ""),
              file=sys.stderr)
    else:
        sys.stdout.write(out)


if __name__ == "__main__":
    main()
