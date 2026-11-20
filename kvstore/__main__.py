"""Allow `python -m kvstore` to start the web dashboard."""

from .server import main

if __name__ == "__main__":
    raise SystemExit(main())
