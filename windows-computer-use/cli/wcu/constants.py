"""Shared constants for the wcu CLI.

Single source of truth for the refusal contract shared by the CLI (which
maps error codes to envelope status) and the session server (which decides
whether to return updated evidence for reconsideration).
"""

# Error codes that mean "the proposal was refused; no input was dispatched".
# These are the engine's guard codes: the proposal no longer applies to the
# current desktop state, so the tool returns updated evidence and the caller
# reconsiders. Caller errors (malformed arguments, unsupported operations)
# are not refusals and are reported as errors.
REFUSAL_CODES = frozenset(
    {
        "stale_observation",
        "geometry_changed",
        "foreground_changed",
        "target_occluded",
        "invalid_coordinates",
        "window_gone",
        "desktop_inaccessible",
        "focus_refused",
        "unknown_key",
    }
)
