# Releasing

The shop laptop runs **releases**, not whatever is on `main` today. A release
is a tag on `main` that has passed CI, has a changelog entry, and has been
looked over with the machine in mind.

## Version numbers

`MAJOR.MINOR.PATCH`, in three places that must agree (`tests/test_release.py`
checks): `src/omniturn_cam/__init__.py` (`__version__`; `pyproject.toml` reads
it from there), `package.xml` (`<version>`, what FreeCAD's Addon Manager
shows), and a `## [X.Y.Z]` heading in `CHANGELOG.md`.

- **PATCH**: bug fixes, no change to job files, no intended G-code change.
- **MINOR**: new features or operations; any intended change to G-code output.
- **MAJOR**: job/tool/machine files that older versions can't read, or
  behaviour an operator has to relearn.

## Cutting a release

1. Check `main` is green on GitHub.
2. Open a PR (branch `release-X.Y.Z`) that:
   - sets the version in `__init__.py` and `package.xml`;
   - moves the **Unreleased** entries in `CHANGELOG.md` under
     `## [X.Y.Z] - YYYY-MM-DD`, leaving an empty **Unreleased** at the top;
   - lists, under **G-code output**, every program in `tests/golden/expected/`
     that changed since the last release (`git diff vLAST -- tests/golden/expected/`).
3. Merge it after review.
4. Tag the merge commit on `main` and push the tag:

   ```bash
   git checkout main && git pull
   git tag -a vX.Y.Z -m "omniturn-cam X.Y.Z"
   git push origin vX.Y.Z
   ```

5. The `release` workflow runs the full test suite again, checks the tag
   matches the version and the changelog, and publishes a GitHub release with
   that changelog section and the source archive.

## Installing a release at the shop

See [SHOP-INSTALL.md](../SHOP-INSTALL.md): `git checkout vX.Y.Z` (or the
release zip), then reinstall. Before cutting metal with a new release:

- Read the **G-code output** section of its changelog entry.
- Re-post a known-good job and compare the program with the one that ran
  last time (`diff OLD.cnc NEW.cnc`). Any difference should be one the
  changelog explains.
- Prove out anything that changed: F4 Verify, then single block with the
  feed override down. Then record it in `tests/golden/verified.toml` if it's
  a golden job.

To go back: `git checkout vPREVIOUS` and reinstall.
