"""Evaluate code already admitted and confirmed by the native application."""

from typing import Any

from contracts.generated.protocol import Bar, Metadata


def evaluate(source: str, bars: list[Bar], params: Metadata) -> object:
    """Call the user's compute(bars, params) using the interpreter's local authority."""
    namespace: dict[str, Any] = {'__name__': '__scopehub_indicator__'}
    exec(compile(source, '<trusted indicator>', 'exec', dont_inherit=True), namespace)
    compute = namespace.get('compute')
    if not callable(compute):
        raise TypeError('Missing compute entry point.')
    return compute(bars, params)
