"""
Zoom model — backward-compat re-export from recording module.
Use app.models.recording.RecordingConnection directly in new code.
"""

from app.models.recording import RecordingConnection  # noqa: F401

# Alias for backward compatibility
ZoomConnection = RecordingConnection
