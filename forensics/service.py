"""TruFor as a small local HTTP service, run in the separate Python 3.11 + CUDA environment (forensics/venv311).

    forensics/venv311/Scripts/python forensics/service.py --port 8100

POST /analyze   body: raw image bytes  ->  JSON {score, map_mean, map_p95, width, height, device, seconds, heatmap_png_b64}
GET  /health    ->  {"status": "ok", "device": ...}

`score` is TruFor's image-level manipulation score in 0..1 (higher means more likely manipulated); `heatmap_png_b64` is an RGBA
PNG that is transparent where the model sees no manipulation and red where it does, sized like the (possibly shrunk) input.
TruFor detects splicing, copy-move and retouching; it is not an AI-image-generation detector, and the API says so.
TruFor, GRIP-UNINA, University Federico II of Naples. Licence: informational and nonprofit use only, with credit to the authors.
"""

import argparse
import base64
import io
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

import numpy as np
import torch
from PIL import Image
from torch.nn import functional as F

HERE = os.path.dirname(os.path.realpath(__file__))
TRUFOR = os.path.join(HERE, "trufor", "TruFor_train_test")
sys.path.insert(0, TRUFOR)
os.chdir(TRUFOR)

from lib.config import config, update_config  # noqa: E402
from lib.utils import get_model  # noqa: E402

MAX_BODY = 25 * 1024 * 1024
MODEL_FILE = os.path.join("pretrained_models", "weights", "trufor.pth.tar")


class Args:
    experiment = "trufor_ph3"
    opts = ["TEST.MODEL_FILE", MODEL_FILE]


class Engine:
    def __init__(self, max_side: int, cpu: bool):
        self.device = "cpu" if cpu or not torch.cuda.is_available() else "cuda:0"
        self.max_side = max_side
        update_config(config, Args)
        # weights_only stays on. The checkpoint's pickle was read without running it (pickletools): beyond tensors it only
        # references a numpy scalar and dtype (the epoch number), so exactly those are allowed and nothing else.
        scalar = (np._core.multiarray.scalar, "numpy.core.multiarray.scalar")
        with torch.serialization.safe_globals([scalar, np.dtype, type(np.dtype("float64"))]):
            checkpoint = torch.load(MODEL_FILE, map_location=torch.device(self.device), weights_only=True)
        self.model = get_model(config)
        self.model.load_state_dict(checkpoint["state_dict"])
        self.model = self.model.to(self.device).eval()
        self.lock = Lock()                         # one image at a time: the GPU has 8 GB, not room for parallel batches

    def analyze(self, data: bytes) -> dict:
        started = time.time()
        img = Image.open(io.BytesIO(data)).convert("RGB")
        if max(img.size) > self.max_side:
            scale = self.max_side / max(img.size)
            img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
        rgb = torch.tensor(np.array(img).transpose(2, 0, 1), dtype=torch.float).unsqueeze(0) / 256.0
        with self.lock, torch.no_grad():
            pred, _conf, det, _npp = self.model(rgb.to(self.device), save_np=False)
            pmap = F.softmax(torch.squeeze(pred, 0), dim=0)[1].cpu().numpy()
            score = torch.sigmoid(det).item() if det is not None else None
        alpha = (np.clip(pmap, 0, 1) * 255).astype(np.uint8)
        rgba = np.zeros((*pmap.shape, 4), dtype=np.uint8)
        rgba[..., 0], rgba[..., 3] = 255, alpha
        buf = io.BytesIO()
        Image.fromarray(rgba, "RGBA").save(buf, format="PNG")
        return {
            "score": score,
            "map_mean": float(pmap.mean()),
            "map_p95": float(np.percentile(pmap, 95)),
            "width": img.width, "height": img.height,
            "device": self.device,
            "seconds": round(time.time() - started, 2),
            "heatmap_png_b64": base64.b64encode(buf.getvalue()).decode("ascii"),
        }


def handler(engine: Engine):
    class H(BaseHTTPRequestHandler):
        def _send(self, code: int, body: dict):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/health":
                self._send(200, {"status": "ok", "device": engine.device})
            else:
                self._send(404, {"detail": "not found"})

        def do_POST(self):
            if self.path != "/analyze":
                return self._send(404, {"detail": "not found"})
            size = int(self.headers.get("Content-Length") or 0)
            if not 0 < size <= MAX_BODY:
                return self._send(413, {"detail": f"send 1 to {MAX_BODY} bytes of image"})
            try:
                self._send(200, engine.analyze(self.rfile.read(size)))
            except Exception as exc:                       # the class only: never echo text that could carry paths or content
                self._send(422, {"detail": f"could not analyse the image ({type(exc).__name__})"})

        def log_message(self, fmt, *a):
            sys.stderr.write("%s %s\n" % (self.command, self.path))

    return H


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--max-side", type=int, default=1024, help="shrink larger images so the longest side is this many pixels")
    ap.add_argument("--cpu", action="store_true")
    args = ap.parse_args()
    engine = Engine(args.max_side, args.cpu)
    print(f"TruFor loaded on {engine.device}; listening on 127.0.0.1:{args.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), handler(engine)).serve_forever()


if __name__ == "__main__":
    main()
