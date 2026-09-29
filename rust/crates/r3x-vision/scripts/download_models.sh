#!/usr/bin/env bash
# Fetch the r3x-vision models (free, permissive licences) into R3X_VISION_MODELS
# (default ~/.cache/dj-r3x/vision) and verify their sha256. Never committed.
#   YuNet 2023mar  face detector   MIT         (OpenCV zoo)
#   SFace 2021dec  face embeddings Apache-2.0  (OpenCV zoo)
set -euo pipefail
DIR="${R3X_VISION_MODELS:-$HOME/.cache/dj-r3x/vision}"
mkdir -p "$DIR"
fetch() { # file url sha256
  local f="$DIR/$1"
  if [[ -f "$f" ]] && shasum -a 256 "$f" | grep -q "^$3 "; then echo "ok  $1"; return; fi
  curl -fsSL -o "$f.part" "$2"
  if ! shasum -a 256 "$f.part" | grep -q "^$3 "; then echo "sha256 mismatch for $1" >&2; rm -f "$f.part"; exit 1; fi
  mv "$f.part" "$f"; echo "got $1"
}
fetch yunet.onnx https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx \
  8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4
fetch sface.onnx https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx \
  0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79
