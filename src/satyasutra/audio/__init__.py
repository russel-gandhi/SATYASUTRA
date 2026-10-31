"""Audio authenticity: a pretrained anti-spoofing model scores speech windows. Experimental, see spoof.py for what it can and cannot say."""

from .spoof import SpoofDetector, SpoofResult, window_starts

__all__ = ["SpoofDetector", "SpoofResult", "window_starts"]
