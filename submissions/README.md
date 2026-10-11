# Submissions

Drop one `.dat` fragment per contributed game here (or one file per
contributor, whichever is tidier). Generate a fragment with:

```bash
tools/hash-game.py /path/to/GameFolder --submit submissions/<something>.dat
```

The maintainers aggregate these with `scripts/merge-submissions.py` into
`vnovel.dat` and rebuild the `.rdb` in the core repository.
