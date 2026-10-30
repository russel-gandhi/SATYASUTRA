# Licences of added dependencies

Checked before adding, per ticket F1-10. "Checked" means the licence text was read on the date shown, not assumed.

| Dependency | Used for | Licence | Checked |
|---|---|---|---|
| `pdqhash` 0.2.8 (PyPI, Python bindings by Fausto Morales) | PDQ perceptual hash of images | MIT (the `LICENSE` file in the source package) | 2026-10-31 |
| PDQ algorithm and C++ source bundled inside `pdqhash` (Meta, `facebook/ThreatExchange`) | the hash itself | BSD (the repository `LICENSE`, "BSD License For ThreatExchange software", read at `raw.githubusercontent.com/facebook/ThreatExchange/main/LICENSE`). The `pdqhash` source package ships no copy of it, only Meta copyright headers in the source files | 2026-10-31 |
| `Pillow` 13.0.0 | decoding, resizing and re-encoding images | not read for this ticket | not checked |
| `numpy` 2.5.4 | DCT for pHash, array handling | not read for this ticket | not checked |

Notes

- `pdqhash` has no prebuilt wheel for this machine (Python 3.14, Windows): pip built it from source, which needs a C++ compiler. Any
  other machine or CI image that installs the project must be able to do the same, or install a wheel if one exists for its Python.
- `pdqhash` also bundles `CImg.h`, which is dual-licensed CeCILL / CeCILL-C. It is part of Meta's C++ tools, not of the hash computation
  called from Python. Its terms were not studied here; read them before redistributing a built wheel.
- Pillow and numpy are widely used permissive-licence libraries, but that is general knowledge, not something this ticket verified.
