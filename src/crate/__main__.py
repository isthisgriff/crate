"""Allow `python -m crate` in addition to the installed `crate` script."""

from crate.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
