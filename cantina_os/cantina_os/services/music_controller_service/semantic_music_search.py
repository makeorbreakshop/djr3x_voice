"""CLAP-backed semantic search over the local DJ R3X music library.

Heavy ML dependencies are imported only when the feature initializes. This keeps normal module
imports and unit tests cheap, and lets CantinaOS degrade gracefully if the optional model cannot
load. Track embeddings are cached on disk; only the small text encoder pass runs per request.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from zipfile import BadZipFile

import numpy as np

from cantina_os.models.music_models import MusicTrack

DEFAULT_MODEL_ID = "laion/clap-htsat-unfused"
SAMPLE_RATE = 48_000
SEGMENT_SECONDS = 10
SEGMENT_OFFSETS = (0.15, 0.50, 0.85)


@dataclass(frozen=True)
class SemanticMatch:
    track_name: str
    score: float
    positive_score: float
    negative_score: float | None = None


def rank_semantic_embeddings(
    track_names: Sequence[str],
    track_embeddings: np.ndarray,
    positive_embedding: np.ndarray,
    *,
    negative_embedding: np.ndarray | None = None,
    negative_weight: float = 0.5,
    limit: int = 5,
) -> list[SemanticMatch]:
    """Rank normalized track vectors, optionally subtracting an unwanted quality."""
    positive_scores = track_embeddings @ positive_embedding
    negative_scores = (
        track_embeddings @ negative_embedding
        if negative_embedding is not None
        else None
    )
    combined = positive_scores.copy()
    if negative_scores is not None:
        combined -= float(negative_weight) * negative_scores
    order = np.argsort(combined)[::-1][: max(1, min(int(limit), len(track_names)))]
    return [
        SemanticMatch(
            track_name=track_names[int(index)],
            score=float(combined[int(index)]),
            positive_score=float(positive_scores[int(index)]),
            negative_score=(
                float(negative_scores[int(index)])
                if negative_scores is not None
                else None
            ),
        )
        for index in order
    ]


class SemanticMusicSearch:
    """Load CLAP once, persist audio vectors, and answer warm text searches."""

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        cache_path: str | None = None,
        device: str = "cpu",
        negative_weight: float = 0.5,
        batch_size: int = 4,
        logger: logging.Logger | None = None,
    ) -> None:
        self.model_id = model_id
        self.cache_path = Path(
            cache_path or "~/.cache/dj-r3x/semantic_music_index.npz"
        ).expanduser()
        self.device_name = device
        self.negative_weight = float(negative_weight)
        self.batch_size = max(1, int(batch_size))
        self.logger = logger or logging.getLogger(__name__)
        self.ready = False
        self.track_names: list[str] = []
        self.track_embeddings: np.ndarray | None = None
        self._model = None
        self._processor = None
        self._torch = None
        self._device = None
        self._lock = threading.Lock()

    def initialize(self, tracks: dict[str, MusicTrack]) -> dict[str, float]:
        """Load the text model and load or build the local audio-vector cache."""
        with self._lock:
            started = time.perf_counter()
            self._load_runtime()
            local_tracks = [
                (name, track)
                for name, track in tracks.items()
                if Path(track.path).is_file()
            ]
            if not local_tracks:
                raise RuntimeError(
                    "no local audio tracks are available for semantic indexing"
                )

            fingerprint = self._fingerprint(local_tracks)
            cache_started = time.perf_counter()
            cached = self._load_cache(fingerprint)
            cache_seconds = time.perf_counter() - cache_started
            indexed = False
            if not cached:
                index_started = time.perf_counter()
                self.track_names, self.track_embeddings = self._embed_tracks(
                    local_tracks
                )
                index_seconds = time.perf_counter() - index_started
                self._save_cache(fingerprint)
                indexed = True
            else:
                index_seconds = 0.0

            # Pay the first text-kernel setup cost during startup, not after the user speaks.
            self._embed_text("warm upbeat music")
            self.ready = True
            total = time.perf_counter() - started
            return {
                "track_count": float(len(self.track_names)),
                "cache_seconds": cache_seconds,
                "index_seconds": index_seconds,
                "total_seconds": total,
                "indexed": float(indexed),
            }

    def search(
        self,
        query: str,
        *,
        negative_query: str | None = None,
        limit: int = 5,
    ) -> list[SemanticMatch]:
        if not self.ready or self.track_embeddings is None:
            return []
        with self._lock:
            positive = self._embed_text(query)
            negative = self._embed_text(negative_query) if negative_query else None
            return rank_semantic_embeddings(
                self.track_names,
                self.track_embeddings,
                positive,
                negative_embedding=negative,
                negative_weight=self.negative_weight,
                limit=limit,
            )

    def close(self) -> None:
        with self._lock:
            self.ready = False
            self._model = None
            self._processor = None
            self.track_embeddings = None

    def _load_runtime(self) -> None:
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        try:
            import librosa
            import torch
            from transformers import ClapModel, ClapProcessor
            from transformers.utils import logging as transformers_logging
        except ImportError as exc:
            raise RuntimeError(
                "semantic music dependencies are missing; install torch, transformers, and librosa"
            ) from exc

        if self.device_name == "mps" and not torch.backends.mps.is_available():
            self.logger.warning("MPS unavailable; semantic music search is using CPU")
            self.device_name = "cpu"
        self._torch = torch
        self._librosa = librosa
        self._device = torch.device(self.device_name)
        transformers_logging.disable_progress_bar()
        self._processor = ClapProcessor.from_pretrained(self.model_id)
        self._model = ClapModel.from_pretrained(self.model_id).eval().to(self._device)

    def _fingerprint(self, tracks: Iterable[tuple[str, MusicTrack]]) -> str:
        payload = {
            "model": self.model_id,
            "sample_rate": SAMPLE_RATE,
            "segment_seconds": SEGMENT_SECONDS,
            "segment_offsets": SEGMENT_OFFSETS,
            "tracks": [],
        }
        for name, track in tracks:
            path = Path(track.path)
            stat = path.stat()
            payload["tracks"].append(
                {
                    "name": name,
                    "path": str(path.resolve()),
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                }
            )
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _load_cache(self, fingerprint: str) -> bool:
        if not self.cache_path.exists():
            return False
        try:
            with np.load(self.cache_path, allow_pickle=False) as cached:
                cached_fingerprint = str(cached["fingerprint"].item())
                names = [str(value) for value in cached["track_names"].tolist()]
                embeddings = cached["embeddings"].astype(np.float32)
            if cached_fingerprint != fingerprint or embeddings.shape[0] != len(names):
                return False
            self.track_names = names
            self.track_embeddings = embeddings
            self.logger.info("Loaded semantic music index for %d tracks", len(names))
            return True
        except (BadZipFile, EOFError, KeyError, OSError, TypeError, ValueError) as exc:
            self.logger.warning("Ignoring unreadable semantic music cache: %s", exc)
            return False

    def _save_cache(self, fingerprint: str) -> None:
        assert self.track_embeddings is not None
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        try:
            with temporary.open("wb") as handle:
                np.savez_compressed(
                    handle,
                    fingerprint=np.asarray(fingerprint),
                    track_names=np.asarray(self.track_names),
                    embeddings=self.track_embeddings,
                )
            temporary.replace(self.cache_path)
        except (OSError, TypeError, ValueError) as exc:
            self.logger.warning("Could not save semantic music cache: %s", exc)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                self.logger.debug(
                    "Could not remove temporary semantic cache %s", temporary
                )

    def _embed_tracks(
        self, tracks: list[tuple[str, MusicTrack]]
    ) -> tuple[list[str], np.ndarray]:
        assert (
            self._processor is not None
            and self._model is not None
            and self._torch is not None
        )
        segment_samples = SAMPLE_RATE * SEGMENT_SECONDS
        segments: list[np.ndarray] = []
        owners: list[int] = []
        names: list[str] = []
        for track_index, (name, track) in enumerate(tracks):
            audio, _ = self._librosa.load(track.path, sr=SAMPLE_RATE, mono=True)
            if len(audio) <= segment_samples:
                selected = [
                    np.pad(audio, (0, segment_samples - len(audio))).astype(np.float32)
                ]
            else:
                max_start = len(audio) - segment_samples
                selected = [
                    audio[
                        int(max_start * offset) : int(max_start * offset)
                        + segment_samples
                    ].astype(np.float32)
                    for offset in SEGMENT_OFFSETS
                ]
            names.append(name)
            segments.extend(selected)
            owners.extend([track_index] * len(selected))

        batches = []
        with self._torch.inference_mode():
            for start in range(0, len(segments), self.batch_size):
                inputs = self._processor(
                    audio=segments[start : start + self.batch_size],
                    sampling_rate=SAMPLE_RATE,
                    return_tensors="pt",
                    padding=True,
                )
                inputs = {key: value.to(self._device) for key, value in inputs.items()}
                output = self._model.get_audio_features(**inputs)
                values = (
                    output.pooler_output if hasattr(output, "pooler_output") else output
                )
                batches.append(values.cpu())

        segment_embeddings = self._torch.cat(batches, dim=0)
        track_vectors = []
        for track_index in range(len(tracks)):
            positions = [
                index for index, owner in enumerate(owners) if owner == track_index
            ]
            mean = segment_embeddings[positions].mean(dim=0, keepdim=True)
            track_vectors.append(
                self._torch.nn.functional.normalize(mean, p=2, dim=-1).squeeze(0)
            )
        embeddings = self._torch.stack(track_vectors).numpy().astype(np.float32)
        self.logger.info("Built semantic music index for %d tracks", len(names))
        return names, embeddings

    def _embed_text(self, query: str) -> np.ndarray:
        assert (
            self._processor is not None
            and self._model is not None
            and self._torch is not None
        )
        inputs = self._processor(text=[query], return_tensors="pt", padding=True)
        inputs = {key: value.to(self._device) for key, value in inputs.items()}
        with self._torch.inference_mode():
            output = self._model.get_text_features(**inputs)
            values = (
                output.pooler_output if hasattr(output, "pooler_output") else output
            )
        return values.cpu().numpy().astype(np.float32)[0]
