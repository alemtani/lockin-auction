# Working on this repo

- Merge finished work straight to `main` and push it; don't open a pull request or wait to be asked.
  The daily email runs from `main`, so a change isn't live until it's there. Run
  `python3 -m unittest discover season` first, and don't merge with failing tests.
- `data/season/` is written by the daily workflow. Replays (`season/daily.py --date ... --dry-run`)
  overwrite files there; restore them with `git checkout -- data/` before committing.
