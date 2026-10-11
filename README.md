# vnovel-database

Community-submitted game fingerprints for the
[vnovel](https://gitlab.com/matchaxnb/vnovel) libretro core.

The core identifies a visual novel by the hash of the script/data file it
launches (`nscript.dat`, `pscript.dat`, `data.xp3`). The core repository ships
the canonical database (`dat/vnovel.dat` → `vnovel.rdb`); this repo collects
additions so anyone can contribute without a merge request against the core.

## Submitting a game

From a checkout of the core repository, point `tools/hash-game.py` at the
game's folder — the one containing `nscript.dat` / `pscript.dat` / `data.xp3`:

```bash
tools/hash-game.py ~/VN/MyGame/Game --submit -      # print a block
tools/hash-game.py ~/VN/MyGame/Game --email -       # print a mail body
```

Either paste the block into a new file under `submissions/` and open a merge
request, or mail the `--email` output to the maintainers. Both contain the same
`game (...)` fragment; the comments are provenance and are stripped when the
fragment is merged into the canonical database.

A submission looks like this:

```
game (
	name "My Game"
	description "My Game"
	// collected 2026-10-11 by vnovel hash-game.py; name from game.id
	rom ( name nscript.dat size 4992002 crc 671BFC0B md5 d15f... sha1 86da... )
)
```

- **`name`** is what RetroArch will show. The tool derives it in ONScripter's
  own order of preference: `game.id` → `ons.cfg gameid=` → `;gameid` directive
  → `versionstr` → `caption` (KiriKiri: `setup.ini` `AppName=`, then the
  launcher `.exe` basename). Override with `--name`.
- **`crc`** is the match key. Two records with the same crc are the same
  content; the merge reports name disagreements for a human to settle.

Do **not** rename the `rom` file to match your copy — the fingerprint is of the
file's bytes, and its `name` field is recorded so the database says which file
it was.

## Layout

| Path | Contents |
|---|---|
| `submissions/` | One `.dat` fragment per contributed game (or a single file per contributor). |
| `scripts/merge-submissions.py` | Aggregates `submissions/*.dat` into the canonical database. |
| `scripts/hash-game.py` | Copy of the core's submission tool (kept in step with it). |

## For maintainers

```bash
# aggregate everything submitted into the canonical dat
scripts/merge-submissions.py submissions/*.dat -o vnovel.dat

# then, in the core repo:
python3 tools/make-rdb.py vnovel.dat vnovel.rdb
```

The merge is idempotent and deduplicates by crc, so re-running it over an
unchanged set reproduces the same database.

## License

The submitted fingerprints are facts about files (hash, size, name) and are
released to the public domain / CC0. The tooling is GPLv2, matching the core.
