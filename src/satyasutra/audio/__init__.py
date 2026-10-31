"""Synthetic-speech check: a wav2vec2 classifier in the GPU service scores speech windows. Experimental, see spoof.py for what it can and cannot say."""

from .spoof import SpoofDetector, SpoofResult

__all__ = ["SpoofDetector", "SpoofResult"]
