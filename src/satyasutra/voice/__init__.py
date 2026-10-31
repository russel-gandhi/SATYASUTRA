"""Speech to text for voice notes."""

from .transcribe import Transcriber, Transcript, VoiceError, second_opinion

__all__ = ["Transcriber", "Transcript", "VoiceError", "second_opinion"]
