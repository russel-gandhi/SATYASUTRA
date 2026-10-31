import collections, io, random, sys, zipfile
from pathlib import Path
import httpx

url = "https://huggingface.co/datasets/bitmind/FaceForensicsC23/resolve/main/FaceForensics++_C23.zip"
out = Path(sys.argv[1]); per = int(sys.argv[2])
http = httpx.Client(follow_redirects=True, timeout=120)
final = str(http.head(url).url)
size = int(http.head(final).headers["content-length"])


class Ranged(io.RawIOBase):
    def __init__(self):
        self.pos = 0
    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.pos
    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else size + off
        return self.pos
    def read(self, n=-1):
        if n < 0 or self.pos + n > size:
            n = size - self.pos
        if n <= 0:
            return b""
        r = http.get(final, headers={"Range": f"bytes={self.pos}-{self.pos + n - 1}"})
        data = r.content
        self.pos += len(data)
        return data
    def readinto(self, b):
        d = self.read(len(b)); b[:len(d)] = d; return len(d)


z = zipfile.ZipFile(io.BufferedReader(Ranged(), 1 << 20))
groups = collections.defaultdict(list)
for n in z.namelist():
    if n.endswith(".mp4"):
        groups["/".join(n.split("/")[1:-1])].append(n)
print({k: len(v) for k, v in groups.items()}, flush=True)
rng = random.Random(11)
for g, names in groups.items():
    take = per * 3 if g.startswith("real") else per
    for n in rng.sample(names, min(take, len(names))):
        d = out / ("real" if g.startswith("real") else "fake") / g.replace("/", "_")
        d.mkdir(parents=True, exist_ok=True)
        (d / Path(n).name).write_bytes(z.read(n))
    print("got", g, flush=True)
