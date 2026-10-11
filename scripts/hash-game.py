#!/usr/bin/env python3
"""Fingerprint a visual-novel game folder and emit a vnovel.dat game() block.

The .rdb distinguishes games by the hash of the script/data file
(nscript.dat / pscript.dat / data.xp3) the core launches. This walks a game
folder, finds that file, prints its size/crc/md5/sha1, and derives the best
available display name, so contributing an entry is copy-paste.

Name precedence (matches ONScripter's own order of preference):
  1. game.id            (a sidecar file; ONScripter/PONScripter convention)
  2. ons.cfg  gameid=...
  3. ";gameid"          (a directive in the script header)
  4. versionstr         ("^Title Ver1.0","^Copyright ...", shown in About)
  5. caption            (the window title; also the About fallback)
  KiriKiri: setup.ini AppName=, else the <Game>.exe basename.

Usage:
    tools/hash-game.py /path/to/GameFolder
    tools/hash-game.py /path/to/GameFolder --name "Override Title"
    tools/hash-game.py /path/to/GameFolder --append dat/vnovel.dat
"""

import argparse
import hashlib
import os
import re
import sys
import unicodedata
import zlib

# The file the core keys engine detection off, per engine. data.xp3 first only
# in the sense that a folder with both is a KiriKiri game; the others are named.
SCRIPT_FILES = ("nscript.dat", "pscript.dat", "nscript.___", "data.xp3")
XP3_FORMAT = "xp3"

# Script text is XORed with 0x84 in ONScripter/PONScripter .dat files; the
# plain-text forms (0.txt etc.) and pscript.dat-under-PON are read as-is, and
# xp3/setup.ini are not scripts at all.
HEAD_BYTES = 1 << 16


def find_script(folder):
    for name in SCRIPT_FILES:
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            return name, p
    return None, None


def read_head(path, name):
    """Decode the first bytes of a script file for name extraction."""
    with open(path, "rb") as f:
        raw = f.read(HEAD_BYTES)
    if name.endswith(".xp3"):
        return ""  # compressed container; nothing to parse as text
    try:
        text = bytes(b ^ 0x84 for b in raw).decode("utf-8", "replace")
    except Exception:
        return ""
    # Heuristic: a decoded nscript.dat starts with ';' or a directive. If the
    # XOR produced garbage, fall back to reading it as plain text (some ports
    # ship an unencrypted script).
    if text.count("\x00") > len(text) // 10:
        try:
            text = raw.decode("utf-8", "replace")
        except Exception:
            pass
    return text


def first_group(pattern, text, flags=re.M):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def derive_name(folder, script_name, head):
    """Best-effort display name, in ONScripter's own order of preference."""
    p = os.path.join(folder, "game.id")
    if os.path.isfile(p):
        line = open(p, encoding="utf-8", errors="replace").readline().strip()
        if line:
            return line, "game.id"

    p = os.path.join(folder, "ons.cfg")
    if os.path.isfile(p):
        for line in open(p, encoding="utf-8", errors="replace"):
            m = re.match(r"\s*gameid\s*=\s*(.+)", line, re.I)
            if m and m.group(1).strip():
                return m.group(1).strip(), "ons.cfg gameid"

    if head:
        v = first_group(r"^\s*;gameid[ \t]*(.+)$", head)
        if v:
            return v, ";gameid"
        v = first_group(r'^\s*versionstr\s+"\^?([^"]{2,80})"', head)
        if v:
            return v, "versionstr"
        v = first_group(r'^\s*caption\s+"([^"]{2,80})"', head)
        if v:
            return v, "caption"

    # KiriKiri: installer metadata, else the launcher's basename. setup.ini is
    # written by InstallShield on a Japanese host, so AppName is cp932 (the
    # title is often all full-width, e.g. "Fate／stay night").
    p = os.path.join(folder, "setup.ini")
    if os.path.isfile(p):
        for enc in ("utf-8", "cp932"):
            try:
                text = open(p, encoding=enc).read()
            except UnicodeDecodeError:
                continue
            m = re.search(r"^AppName=(.+)$", text, re.M)
            if m and m.group(1).strip():
                return m.group(1).strip(), "setup.ini AppName"
            break
    for entry in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if entry.lower().endswith(".exe") and entry.lower() not in (
                "setup.exe", "userconf.exe", "uninstall.exe"):
            return os.path.splitext(entry)[0], "exe basename"

    return None, None


def fingerprints(path):
    """size, crc32, md5, sha1 of the file — what the rdb record carries."""
    size = os.path.getsize(path)
    crc = 0
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
            md5.update(chunk)
            sha1.update(chunk)
    return size, crc & 0xFFFFFFFF, md5.hexdigest(), sha1.hexdigest()


def game_block(title, rom_name, size, crc, md5, sha1):
    return (
        "game (\n"
        f'\tname "{title}"\n'
        f'\tdescription "{title}"\n'
        f"\trom ( name {rom_name} size {size} crc {crc:08X} "
        f"md5 {md5} sha1 {sha1} )\n"
        ")\n"
    )


def canonical_title(s):
    """NFKC-fold fullwidth ASCII (Fate／stay night -> Fate/stay night).

    The name lands in a RetroArch playlist label and its thumbnail filename,
    where a fullwidth solidus is a liability; NFKC maps the compatibility
    forms and leaves CJK ideographs and kana alone."""
    return unicodedata.normalize("NFKC", s).strip()


def submission_block(title, rom_name, size, crc, md5, sha1, source):
    """A block with provenance comments, for pasting into an issue or email.

    The extra comments are pruned on aggregation (and ignored by the DAT
    parser), so the same text serves both as a human-readable submission and
    as a valid clrmamepro fragment.
    """
    return (
        "game (\n"
        f'\tname "{title}"\n'
        f'\tdescription "{title}"\n'
        f"\t// collected {__import__('datetime').date.today().isoformat()}"
        f" by vnovel hash-game.py"
        + (f"; name from {source}" if source else "")
        + "\n"
        f"\trom ( name {rom_name} size {size} crc {crc:08X} "
        f"md5 {md5} sha1 {sha1} )\n"
        ")\n"
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--name", help="override the derived display name")
    ap.add_argument("--append", help="append the block to this DAT file")
    ap.add_argument("--submit", metavar="OUT",
                    help="write a submission fragment here (with provenance "
                         "comments); '-' for stdout. Use this for the "
                         "vnovel-database issue/merge request.")
    ap.add_argument("--email", metavar="OUT",
                    help="write a plain-text submission body here ('-' for "
                         "stdout) suitable for mailing to the maintainers")
    args = ap.parse_args()

    folder = os.path.abspath(args.folder)
    if not os.path.isdir(folder):
        sys.exit(f"not a directory: {folder}")

    rom_name, path = find_script(folder)
    if not rom_name:
        sys.exit(f"no script/data file found in {folder} "
                 f"(looked for {', '.join(SCRIPT_FILES)})")

    head = read_head(path, rom_name)
    derived, source = derive_name(folder, rom_name, head)
    title = canonical_title(args.name or derived or os.path.basename(folder))

    size, crc, md5, sha1 = fingerprints(path)
    print(f"file:  {rom_name}  ({size} bytes)", file=sys.stderr)
    print(f"crc:   {crc:08X}", file=sys.stderr)
    print(f"md5:   {md5}", file=sys.stderr)
    print(f"sha1:  {sha1}", file=sys.stderr)
    print(f"name:  {title!r}"
          + (f"  (from {source})" if source and not args.name else ""),
          file=sys.stderr)

    if args.submit or args.email:
        import datetime
        block = submission_block(title, rom_name, size, crc, md5, sha1, source)
        body = (
            f"vnovel database submission — {title}\n"
            f"generated {datetime.date.today().isoformat()} by tools/hash-game.py\n"
            "\n"
            "Paste the block below into a vnovel-database merge request (or\n"
            "send this whole message to the maintainers). The 'game (...)'\n"
            "block is what gets aggregated; the comments are provenance.\n"
            "\n"
            "```\n" + block + "```\n"
        )
        out = args.email or args.submit
        if out == "-":
            sys.stdout.write(block if args.submit else body)
        else:
            with open(out, "w", encoding="utf-8") as f:
                f.write(block if args.submit else body)
            print(f"wrote {out}", file=sys.stderr)
        return

    block = game_block(title, rom_name, size, crc, md5, sha1)
    if args.append:
        with open(args.append, "a", encoding="utf-8") as f:
            f.write("\n" + block)
        print(f"appended to {args.append}", file=sys.stderr)
    else:
        print(block, end="")


if __name__ == "__main__":
    main()
