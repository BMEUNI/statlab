"""`python -m statlab_tools`: load settings, build the app, run uvicorn.

On `ToolSettingsError`, print the message to stderr and exit 2 -- fail fast (D20); exit code 2
distinguishes a config error from a crash (which would be 1, uvicorn's own default).
"""

from __future__ import annotations

import sys

import uvicorn

from statlab_tools.server import create_app
from statlab_tools.settings import ToolSettingsError, load_tool_settings


def main() -> int:
    try:
        settings = load_tool_settings()
    except ToolSettingsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
