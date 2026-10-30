"""MinHash signatures in plain Python. Permutations are derived from their index with a hash, so signatures are the same on
every run and every machine."""

from __future__ import annotations

import hashlib

MERSENNE_PRIME = (1 << 61) - 1
BYTES_PER_WORD = 8


def _word(data: bytes) -> int:
    return int.from_bytes(hashlib.blake2b(data, digest_size=BYTES_PER_WORD).digest(), "big")


class MinHasher:
    def __init__(self, num_perm: int):
        self.num_perm = num_perm
        self._params = [(_word(f"a{i}".encode()) % (MERSENNE_PRIME - 1) + 1, _word(f"b{i}".encode()) % MERSENNE_PRIME) for i in range(num_perm)]

    def signature(self, shingle_set: set[str]) -> tuple[int, ...]:
        if not shingle_set:
            raise ValueError("cannot sign an empty shingle set")
        hashed = [_word(s.encode("utf-8")) for s in shingle_set]
        return tuple(min((a * h + b) % MERSENNE_PRIME for h in hashed) for a, b in self._params)


def estimate_jaccard(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    return sum(x == y for x, y in zip(a, b)) / len(a)


def exact_jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0
