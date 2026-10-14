"""`python -m ucla_agent.check`: the readiness check; see `ucla_agent.check.command`."""

import sys

from ucla_agent.check.command import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
