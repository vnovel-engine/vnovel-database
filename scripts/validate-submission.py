#!/usr/bin/env python3
"""Validate a submission fragment: a contributor's .dat, as untrusted input.

A submission service (or CI, or a maintainer's inbox) must treat these files
as hostile: the text is arbitrary, and its only job is to become rows in the
database. This checks a fragment can be trusted to merge *without changing the
canonical database beyond appending records*:

  * it parses as clrmamepro at all;
  * every game() has a name and a well-formed crc/md5/sha1/size;
  * the rom file name is one of the files the core actually hashes;
  * no duplicate crcs *within* the fragment;
  * field count is small (no megabyte-long description);
  * the file is plain text, reasonable in size, and has no NUL/binary;
  * nothing in the record can escape the DAT grammar (quotes, newlines).

Exit status is non-zero if any fragment is rejected, so it can gate a merge.

Usage:
    tools/validate-submission.py FILE [FILE ...]
    tools/validate-submission.py --json FILE      # machine-readable report
"""

import argparse
import importlib.util
import json
import os
import re
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "merge_submissions", os.path.join(_here, "merge-submissions.py"))
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)

# The files the core identifies content by (engine detection + rdb match).
KNOWN_ROMS = {"nscript.dat", "pscript.dat", "nscript.___", "data.xp3"}

HEX = {"crc": (8, 16), "md5": 32, "sha1": 40}
MAX_BYTES = 256 * 1024      # a fragment is a few lines; anything larger is abuse
MAX_FIELDS = 40             # a record has ~12 fields; headroom, not a target
MAX_VALUE_LEN = 512         # names/descriptions; guards against bloat
MAX_GAMES = 5000            # a single submission should not be a whole archive


class Reject(Exception):
    pass


def validate_text(text, path="<input>"):
    """Return the list of parsed games, or raise Reject with a reason."""
    if "\x00" in text:
        raise Reject("contains NUL bytes (not a text DAT)")
    if len(text.encode("utf-8", "surrogateescape")) > MAX_BYTES:
        raise Reject(f"larger than {MAX_BYTES} bytes")

    try:
        games = _m.parse_games(text)
    except Exception as e:
        raise Reject(f"does not parse as clrmamepro: {e}")

    if not games:
        raise Reject("no game() blocks")
    if len(games) > MAX_GAMES:
        raise Reject(f"{len(games)} game() blocks (limit {MAX_GAMES})")

    seen = set()
    for i, g in enumerate(games):
        where = f"game #{i + 1}"
        name = (g.get("name") or "").strip()
        if not name:
            raise Reject(f"{where}: no name")
        if len(name) > MAX_VALUE_LEN:
            raise Reject(f"{where}: name is {len(name)} chars")
        if len(g) > MAX_FIELDS:
            raise Reject(f"{where}: {len(g)} fields")
        for k, v in g.items():
            if k == "rom":
                continue
            if isinstance(v, str) and len(v) > MAX_VALUE_LEN:
                raise Reject(f"{where}: field '{k}' is {len(v)} chars")

        roms = g.get("rom") or []
        if not roms:
            raise Reject(f"{where}: no rom()")
        for rom in roms:
            rn = (rom.get("name") or "").strip()
            # The name is informational, but a mismatch almost always means the
            # contributor hashed the wrong file, which is worth catching now.
            if rn not in KNOWN_ROMS:
                raise Reject(f"{where}: rom name {rn!r} is not one of "
                             f"{sorted(KNOWN_ROMS)} (hashed the wrong file?)")
            crc = (rom.get("crc") or "").strip().upper()
            if not crc:
                raise Reject(f"{where}: rom has no crc")
            if not re.fullmatch(r"[0-9A-F]{8}", crc):
                raise Reject(f"{where}: crc {crc!r} is not 8 hex digits")
            if crc in seen:
                raise Reject(f"{where}: crc {crc} appears twice in this file")
            seen.add(crc)
            for field in ("md5", "sha1"):
                v = (rom.get(field) or "").strip()
                if v and not re.fullmatch(rf"[0-9A-Fa-f]{{{HEX[field]}}}", v):
                    raise Reject(f"{where}: {field} {v!r} is not "
                                 f"{HEX[field]} hex digits")
            size = (rom.get("size") or "").strip()
            if size and not size.isdigit():
                raise Reject(f"{where}: size {size!r} is not a number")
        # A value containing a quote or newline would break the DAT grammar on
        # the way back out; the parser already stripped quotes, so check for
        # the residual control characters.
        for v in list(g.values()) + [x for r in roms for x in r.values()]:
            if isinstance(v, str) and ("\n" in v or "\r" in v or '"' in v):
                raise Reject(f"{where}: a field contains a quote or newline")
    return games


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    args = ap.parse_args()

    report, bad = [], 0
    for path in args.files:
        try:
            text = open(path, encoding="utf-8", errors="strict").read()
        except UnicodeDecodeError:
            report.append({"file": path, "ok": False, "reason": "not UTF-8 text"})
            bad += 1
            continue
        except OSError as e:
            report.append({"file": path, "ok": False, "reason": str(e)})
            bad += 1
            continue
        try:
            games = validate_text(text, path)
            report.append({"file": path, "ok": True, "games": len(games)})
        except Reject as e:
            report.append({"file": path, "ok": False, "reason": str(e)})
            bad += 1

    if args.json:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        for r in report:
            print(f"{'OK  ' if r['ok'] else 'BAD '} {r['file']}"
                  + (f"  ({r['games']} game(s))" if r["ok"] else f"  — {r['reason']}"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
