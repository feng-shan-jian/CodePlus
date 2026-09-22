"""Optional adapter for the installed CodePlus host, imported only on demand."""

try:
    from codeplus.run_policy import RunExecutionPolicy as _HostPolicy
except ImportError as error:
    raise ImportError(
        'The knowledge adapter requires the locally built CodePlus host with '
        'RunExecutionPolicy support. Install its wheel into this environment; '
        'do not install an unrelated same-name package from an index.'
    ) from error
