"""Parity data for r3x-vision: OpenCV FaceDetectorYN / FaceRecognizerSF on the same letterboxed
640x640 input the Rust detector uses. Offline, free. From the repo root:

    venv/bin/python rust/crates/r3x-vision/scripts/opencv_reference.py
"""
import json, os, sys
import cv2, numpy as np

M = os.environ.get("R3X_VISION_MODELS", os.path.expanduser("~/.cache/dj-r3x/vision"))
PHOTOS = ["Brandon_000.jpg", "Brandon_009.jpg", "Brandon_012.jpg"]
SRC = "cantina_os/vision_data/training/Brandon"
OUT = "rust/crates/r3x-vision/tests/data/opencv_reference.json"

det = cv2.FaceDetectorYN.create(f"{M}/yunet.onnx", "", (640, 640), 0.6, 0.3, 5000)
rec = cv2.FaceRecognizerSF.create(f"{M}/sface.onnx", "")
out = []
for name in PHOTOS:
    img = cv2.imread(f"{SRC}/{name}")
    h, w = img.shape[:2]
    s = min(640 / w, 640 / h)
    small = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_LINEAR)
    pad = np.zeros((640, 640, 3), np.uint8)
    pad[: small.shape[0], : small.shape[1]] = small
    _, faces = det.detect(pad)
    f = faces[0].copy()
    f[:14] /= s
    feat = rec.feature(rec.alignCrop(img, f)).flatten()
    out.append({"photo": name, "bbox": f[:4].tolist(), "landmarks": f[4:14].tolist(), "score": float(f[14]),
                "embedding": (feat / np.linalg.norm(feat)).round(5).tolist()})
json.dump(out, open(OUT, "w"))
print("wrote", OUT, [(o["photo"], [round(v) for v in o["bbox"]], round(o["score"], 3)) for o in out])
