#!/usr/bin/env python3
"""Compile a clrmamepro DAT into a libretro .rdb database.

RetroArch identifies content by scanning a per-system .rdb (see
database = "..." in the core's .info). This converts our dat/vnovel.dat
into a .rdb whose records carry the fingerprint of the game's script/data
file (nscript.dat, pscript.dat or data.xp3) -- the same file the core's
engine detection reads.

The .rdb is MessagePack (see libretro-db/rmsgpack.c) with a 16-byte header:

    offset 0   "RARCHDB\\0"            magic
    offset 8   metadata_offset         big-endian u64 (msgpack order; the
                                       reader applies swap_if_little64)
    offset 16  record 0 .. record N-1  msgpack maps
               sentinel                0xc0 (RDT_NULL)
               metadata map             {"count": <records>}
               index block(s)           optional, appended after metadata

so the writer must emit records first and seek back to fill the offset.

Usage:
    tools/make-rdb.py dat/vnovel.dat vnovel.rdb

By default the output carries a crc index, which is what RetroArch's
scanner binary-searches; pass --index none for a plain walk-only file.
"""

import argparse
import struct
import sys

MAGIC = b"RARCHDB\x00"
SENTINEL = b"\xc0"
HEADER_SIZE = 16

# DAT scalar fields -> rdb fields. Kept in step with the mapping table in
# RetroArch's libretro-db/c_converter.c so our records look like everyone
# else's.
STRING_FIELDS = (
    "name", "description", "genre", "language", "region", "media",
    "developer", "publisher", "origin", "franchise", "barcode",
    "esrb_rating", "pegi_rating", "cero_rating",
)
UINT_FIELDS = ("releaseyear", "releasemonth", "users", "rumble", "analog")
HEX_FIELDS = {"crc": 4, "md5": 16, "sha1": 20}


# --- MessagePack primitives (mirrors libretro-db/rmsgpack.c) --------------
def mp_uint(v):
    if v <= 0xFF:
        return struct.pack(">BB", 0xCC, v)
    if v <= 0xFFFF:
        return struct.pack(">BH", 0xCD, v)
    if v <= 0xFFFFFFFF:
        return struct.pack(">BI", 0xCE, v)
    return struct.pack(">BQ", 0xCF, v)


def mp_string(s):
    b = s.encode("utf-8")
    n = len(b)
    if n < 32:
        return struct.pack(">B", 0xA0 | n) + b
    if n <= 0xFF:
        return struct.pack(">BB", 0xD9, n) + b
    if n <= 0xFFFF:
        return struct.pack(">BH", 0xDA, n) + b
    return struct.pack(">BI", 0xDB, n) + b


def mp_bin(b):
    n = len(b)
    if n <= 0xFF:
        return struct.pack(">BB", 0xC4, n) + b
    if n <= 0xFFFF:
        return struct.pack(">BH", 0xC5, n) + b
    return struct.pack(">BI", 0xC6, n) + b


def mp_map(n):
    # The metadata block and every record have well under 16 pairs, but a
    # record with many rom attributes could not, so handle the wide forms.
    if n < 16:
        return struct.pack(">B", 0x80 | n)
    if n <= 0xFFFF:
        return struct.pack(">BH", 0xDE, n)
    return struct.pack(">BI", 0xDF, n)


# --- clrmamepro DAT parsing ----------------------------------------------
class DatParser:
    """A shallow clrmamepro scanner.

    Hand-rolled rather than regex: values contain parentheses ("(English
    Translation)") and real DATs carry unbalanced punctuation in unquoted
    values (the shipped RPG Maker.dat has Roman numerals in a releaseyear
    position), both of which a naive split mishandles.
    """

    def __init__(self, text):
        self.text = text
        self.i = 0
        self.n = len(text)

    def skip_ws(self):
        t, n = self.text, self.n
        while self.i < n:
            if t[self.i] in " \t\r\n":
                self.i += 1
            elif t.startswith("//", self.i):
                j = t.find("\n", self.i)
                self.i = n if j < 0 else j + 1
            else:
                return

    def read_token(self):
        """A quoted string, or a bare word up to whitespace/')'."""
        t, n = self.text, self.n
        if self.i < n and t[self.i] == '"':
            self.i += 1
            start = self.i
            while self.i < n and t[self.i] != '"':
                self.i += 1
            out = t[start:self.i]
            self.i += 1  # closing quote
            return out
        start = self.i
        while self.i < n and t[self.i] not in ' \t\r\n)"':
            self.i += 1
        return t[start:self.i]

    def skip_block(self):
        """Consume a balanced (...) run, ignoring parens inside strings."""
        depth = 1
        while self.i < self.n and depth:
            if self.text[self.i] == '"':
                self.read_token()
                continue
            if self.text[self.i] == "(":
                depth += 1
            elif self.text[self.i] == ")":
                depth -= 1
            self.i += 1

    def parse_rom(self):
        r = {}
        while True:
            self.skip_ws()
            if self.i >= self.n:
                break
            if self.text[self.i] == ")":
                self.i += 1
                break
            key = self.read_token()
            self.skip_ws()
            r[key] = self.read_token()
        return r

    def parse_block(self):
        """The body of a game(): {scalar: value, 'rom': [{...}]}."""
        game = {}
        while True:
            self.skip_ws()
            if self.i >= self.n:
                break
            if self.text[self.i] == ")":
                self.i += 1
                break
            key = self.read_token()
            self.skip_ws()
            if self.i < self.n and self.text[self.i] == "(":
                self.i += 1
                if key in ("rom", "disk"):
                    game.setdefault("rom", []).append(self.parse_rom())
                else:
                    self.skip_block()
                continue
            game[key] = self.read_token()
        return game

    def games(self):
        out = []
        while True:
            self.skip_ws()
            if self.i >= self.n:
                break
            word = self.read_token()
            self.skip_ws()
            if self.i >= self.n or self.text[self.i] != "(":
                # Top-level scalar (a clrmamepro header key); skip its value.
                self.skip_ws()
                if self.i < self.n and self.text[self.i] != "(":
                    self.read_token()
                continue
            self.i += 1  # consume '('
            if word == "game":
                out.append(self.parse_block())
            else:
                self.skip_block()
        return out


def parse_dat(text):
    return DatParser(text).games()


def hex_to_bin(h, width):
    h = h.strip().upper()
    if len(h) != width * 2:
        raise ValueError(f"expected {width * 2} hex digits, got {len(h)!r}")
    return bytes.fromhex(h)


def record_for(game):
    """The record pairs for one DAT game(), plus the crc it indexes on.

    The index needs the raw 4-byte crc, so it is returned alongside the
    encoded pairs rather than recovered by decoding them again.
    """
    pairs = []
    crc = None
    for key in STRING_FIELDS:
        v = game.get(key)
        if v:
            pairs.append((key, mp_string(v)))
    for key in UINT_FIELDS:
        v = game.get(key)
        if v:
            pairs.append((key, mp_uint(int(v))))

    for rom in game.get("rom") or []:
        name = rom.get("name")
        if name:
            pairs.append(("rom_name", mp_string(name)))
        if rom.get("size"):
            pairs.append(("size", mp_uint(int(rom["size"]))))
        for dat_key, width in HEX_FIELDS.items():
            if rom.get(dat_key):
                raw = hex_to_bin(rom[dat_key], width)
                pairs.append((dat_key, mp_bin(raw)))
                if dat_key == "crc":
                    crc = raw
        if rom.get("serial"):
            pairs.append(("serial", mp_bin(rom["serial"].encode("ascii"))))
    return pairs, crc


def write_rdb(path, games, index_key=None):
    """Emit records, sentinel, metadata and an optional index block.

    An index lets libretrodb_find_entry() binary-search by @index_key
    (RetroArch's scanner does this for rdb-backed lookups) instead of
    walking every record; the block is appended after the metadata and its
    size is carried in its header, so the reader finds it from
    first_index_offset. Omitting it is still a valid database, just slower.
    """
    offsets = []
    crcs = []
    with open(path, "wb") as f:
        f.write(b"\x00" * HEADER_SIZE)  # placeholder header
        for game in games:
            pairs, crc = record_for(game)
            if not pairs:
                continue
            offsets.append(f.tell())
            if crc is not None:
                crcs.append((crc, f.tell()))
            f.write(mp_map(len(pairs)))
            for k, v in pairs:
                f.write(mp_string(k))
                f.write(v)
        f.write(SENTINEL)  # end-of-records
        metadata_offset = f.tell()
        f.write(mp_map(1))
        f.write(mp_string("count"))
        f.write(mp_uint(len(offsets)))

        if index_key == "crc" and crcs:
            write_index(f, "crc", crcs)

        f.seek(0)
        f.write(MAGIC[:8])
        # Big-endian, like every other width in the format: the reader
        # applies swap_if_little64() (RetroArch/libretro-db/libretrodb.c),
        # which byteswaps on little-endian CPUs.
        f.write(struct.pack(">Q", metadata_offset))
    return offsets


def write_index(f, name, entries):
    """Append the msgpack index block libretrodb_create_index() writes.

    Layout: a map {"name", "key_size", "next", "count"} -- @next being the
    byte length of the sorted entries that follow, each a key_size-byte key
    then a uint64 record offset. Keys must be ascending: the reader binary-
    searches them (binsearch() in libretrodb.c).
    """
    entries = sorted(entries, key=lambda e: e[0])
    key_size = len(entries[0][0])
    for key, _ in entries:
        if len(key) != key_size:
            raise ValueError("index keys must be uniform width")
    next_bytes = len(entries) * (key_size + 8)

    f.write(mp_map(4))
    f.write(mp_string("name"))
    f.write(mp_string(name))
    f.write(mp_string("key_size"))
    f.write(mp_uint(key_size))
    f.write(mp_string("next"))
    f.write(mp_uint(next_bytes))
    f.write(mp_string("count"))
    f.write(mp_uint(len(entries)))

    for key, offset in entries:
        f.write(key)
        # Raw little-endian, not msgpack: node_iter() in libretrodb.c
        # writes the offset to the file verbatim from memory.
        f.write(struct.pack("<Q", offset))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dat")
    ap.add_argument("rdb")
    ap.add_argument("--index", default="crc",
                    help="field to index for lookups (default: crc; "
                         "'none' to omit)")
    ap.add_argument("--list", action="store_true", help="print records only")
    args = ap.parse_args()

    text = open(args.dat, "r", encoding="utf-8").read()
    games = parse_dat(text)
    if args.list:
        for g in games:
            print(g.get("name"), "->", g.get("rom"))
        return 0
    if not games:
        print(f"{args.dat}: no game() blocks found", file=sys.stderr)
        return 1

    index_key = None if args.index in ("", "none") else args.index
    offsets = write_rdb(args.rdb, games, index_key)
    indexed = " (crc index)" if index_key == "crc" else ""
    print(f"{args.rdb}: {len(offsets)} records{indexed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
