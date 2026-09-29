#!/usr/bin/env python
"""Export the CLAP music-search model to ONNX for `r3x-music` (plan D10, Phase 4).

Offline, local, free: it loads `laion/clap-htsat-unfused` from the Hugging Face cache (the
same model CantinaOS's `semantic_music_search.py` uses) and writes, into OUT_DIR
(default `~/.cache/dj-r3x/clap/`):

  clap_text.onnx    input_ids, attention_mask (int64, [1, L]) -> text_embeds [1, 512]
                    (`get_text_features`, *not* normalised - the Python ranker uses it raw)
  clap_audio.onnx   waveform (float32, [B, 480000] = 10 s at 48 kHz) -> audio_embeds [B, 512]
                    The log-mel front end is inside the graph (conv1d DFT + slaney mel + dB),
                    so Rust feeds raw samples and never reimplements the feature extractor.
  tokenizer.json    the RoBERTa tokenizer, for the `tokenizers` crate
  reference.json    parity data: the Python index (track names + vectors from
                    `~/.cache/dj-r3x/semantic_music_index.npz`), the Python rankings for a
                    fixed query set, torch text vectors for those queries, and a torch audio
                    vector for a deterministic test signal.

Usage (from the repo root):
    venv/bin/python scripts/export_clap_onnx.py [--out DIR] [--index PATH]

Uses the legacy TorchScript exporter (`dynamo=False`); needs `pip install onnx` in the venv.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

import numpy as np  # noqa: E402
import torch  # noqa: E402
from transformers import ClapModel, ClapProcessor  # noqa: E402

MODEL_ID = "laion/clap-htsat-unfused"
SR = 48_000
SAMPLES = SR * 10
NEGATIVE_WEIGHT = 0.5

# Fixed query set for the Rust ranking check: (query, negative_query or None).
QUERIES = [
    ("upbeat dance music", None),
    ("calm relaxing ambient", None),
    ("funky cantina jazz", None),
    ("dark intense electronic", None),
    ("happy playful party", None),
    ("energetic music", "slow"),
    ("space synth", "vocals"),
    ("robotic droid music", None),
]


class AudioEncoder(torch.nn.Module):
    """Raw 10 s / 48 kHz waveform -> CLAP audio embedding, feature extractor included.

    Matches `ClapFeatureExtractor._np_extract_fbank_features` for an exactly-10 s input
    (`rand_trunc` path, no truncation or padding): periodic Hann, n_fft 1024, hop 480,
    center/reflect padding, power 2, slaney mel filters, 10*log10(max(mel, 1e-10)).
    """

    def __init__(self, model: ClapModel, mel_filters: np.ndarray, n_fft: int, hop: int):
        super().__init__()
        self.model = model
        self.n_fft = n_fft
        self.hop = hop
        n = torch.arange(n_fft, dtype=torch.float64)
        window = 0.5 - 0.5 * torch.cos(2 * math.pi * n / n_fft)  # periodic Hann
        k = torch.arange(n_fft // 2 + 1, dtype=torch.float64)
        ang = 2 * math.pi * k[:, None] * n[None, :] / n_fft
        kernel = torch.cat([torch.cos(ang) * window, -torch.sin(ang) * window], dim=0)
        self.register_buffer("kernel", kernel.float().unsqueeze(1))  # [2*513, 1, 1024]
        self.register_buffer("mel", torch.from_numpy(mel_filters.astype(np.float32)))  # [513, 64]

    def forward(self, waveform: torch.Tensor) -> torch.Tensor:
        x = waveform.unsqueeze(1)  # [B, 1, N]
        pad = self.n_fft // 2
        x = torch.nn.functional.pad(x, (pad, pad), mode="reflect")
        spec = torch.nn.functional.conv1d(x, self.kernel, stride=self.hop)  # [B, 1026, T]
        bins = self.n_fft // 2 + 1
        power = spec[:, :bins] ** 2 + spec[:, bins:] ** 2  # [B, 513, T]
        mel = torch.matmul(power.transpose(1, 2), self.mel)  # [B, T, 64]
        db = 10.0 * torch.log10(torch.clamp(mel, min=1e-10))
        feats = db.unsqueeze(1)  # [B, 1, T, 64]
        is_longer = torch.zeros(waveform.shape[0], 1, dtype=torch.bool)
        out = self.model.get_audio_features(input_features=feats, is_longer=is_longer)
        return out.pooler_output if hasattr(out, "pooler_output") else out


class TextEncoder(torch.nn.Module):
    def __init__(self, model: ClapModel):
        super().__init__()
        self.model = model

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        out = self.model.get_text_features(input_ids=input_ids, attention_mask=attention_mask)
        return out.pooler_output if hasattr(out, "pooler_output") else out


def test_signal() -> np.ndarray:
    """Deterministic 10 s probe: two tones plus a click track, identical in Rust."""
    t = np.arange(SAMPLES, dtype=np.float64) / SR
    x = 0.3 * np.sin(2 * np.pi * 220.0 * t) + 0.2 * np.sin(2 * np.pi * 1330.0 * t)
    x[(np.arange(SAMPLES) % 24_000) < 480] += 0.4
    return x.astype(np.float32)


def text_features(proc, model, query: str) -> np.ndarray:
    inputs = proc(text=[query], return_tensors="pt", padding=True)
    with torch.inference_mode():
        out = model.get_text_features(**inputs)
        out = out.pooler_output if hasattr(out, "pooler_output") else out
    return out[0].numpy().astype(np.float32)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="~/.cache/dj-r3x/clap")
    ap.add_argument("--index", default="~/.cache/dj-r3x/semantic_music_index.npz")
    ap.add_argument("--opset", type=int, default=17)
    args = ap.parse_args()
    out = Path(os.path.expanduser(args.out))
    out.mkdir(parents=True, exist_ok=True)

    proc = ClapProcessor.from_pretrained(MODEL_ID)
    model = ClapModel.from_pretrained(MODEL_ID).eval()
    fe = proc.feature_extractor

    # --- text ---------------------------------------------------------------------------------
    text = TextEncoder(model).eval()
    sample = proc(text=["upbeat dance music"], return_tensors="pt", padding=True)
    torch.onnx.export(
        text,
        (sample["input_ids"], sample["attention_mask"]),
        str(out / "clap_text.onnx"),
        input_names=["input_ids", "attention_mask"],
        output_names=["text_embeds"],
        dynamic_axes={"input_ids": {1: "len"}, "attention_mask": {1: "len"}},
        opset_version=args.opset,
        dynamo=False,
    )
    print("wrote clap_text.onnx", file=sys.stderr)

    # --- audio --------------------------------------------------------------------------------
    audio = AudioEncoder(model, fe.mel_filters_slaney, fe.fft_window_size, fe.hop_length).eval()
    probe = test_signal()
    with torch.inference_mode():
        ours = audio(torch.from_numpy(probe)[None]).numpy()[0]
        feats = fe(probe, sampling_rate=SR, return_tensors="pt")
        ref = model.get_audio_features(**feats)
        ref = (ref.pooler_output if hasattr(ref, "pooler_output") else ref).numpy()[0]
    cos = float(ours @ ref / (np.linalg.norm(ours) * np.linalg.norm(ref)))
    print(f"in-graph front end vs ClapFeatureExtractor: cosine {cos:.6f}", file=sys.stderr)
    if cos < 0.999:
        print("front end mismatch; not exporting audio", file=sys.stderr)
        return 1
    torch.onnx.export(
        audio,
        (torch.from_numpy(np.stack([probe, probe])),),
        str(out / "clap_audio.onnx"),
        input_names=["waveform"],
        output_names=["audio_embeds"],
        dynamic_axes={"waveform": {0: "batch"}},
        opset_version=args.opset,
        dynamo=False,
    )
    print("wrote clap_audio.onnx", file=sys.stderr)

    # --- tokenizer ----------------------------------------------------------------------------
    tok_dir = out / "tokenizer"
    proc.tokenizer.save_pretrained(str(tok_dir))
    tj = tok_dir / "tokenizer.json"
    if not tj.exists():
        print("tokenizer.json not produced (slow tokenizer?)", file=sys.stderr)
        return 1
    (out / "tokenizer.json").write_bytes(tj.read_bytes())

    # --- parity reference ---------------------------------------------------------------------
    ref_doc: dict = {
        "model": MODEL_ID,
        "negative_weight": NEGATIVE_WEIGHT,
        "probe_audio_embed": ref.tolist(),
        "queries": [],
    }
    index_path = Path(os.path.expanduser(args.index))
    names: list[str] = []
    emb = None
    if index_path.exists():
        with np.load(index_path, allow_pickle=False) as idx:
            names = [str(n) for n in idx["track_names"].tolist()]
            emb = idx["embeddings"].astype(np.float32)
        ref_doc["track_names"] = names
        ref_doc["track_embeddings"] = emb.tolist()
    for q, neg in QUERIES:
        pos = text_features(proc, model, q)
        entry = {"query": q, "negative": neg, "text_embed": pos.tolist(),
                 "input_ids": proc(text=[q])["input_ids"][0]}
        if emb is not None:
            score = emb @ pos
            if neg:
                score = score - NEGATIVE_WEIGHT * (emb @ text_features(proc, model, neg))
            order = np.argsort(score)[::-1]
            entry["ranking"] = [names[i] for i in order]
            entry["scores"] = [float(score[i]) for i in order]
        ref_doc["queries"].append(entry)
    (out / "reference.json").write_text(json.dumps(ref_doc))
    print(f"wrote reference.json ({len(names)} indexed tracks, {len(QUERIES)} queries)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
