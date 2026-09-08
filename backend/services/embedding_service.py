"""
SETU Embedding Service — multilingual text embeddings and semantic similarity.

Generates deterministic sentence embeddings for normalized report texts
using paraphrase-multilingual-MiniLM-L12-v2 (offline-first, zero cloud calls).
Provides serialization, deserialization, and bounded cosine similarity.
"""

from __future__ import annotations

import json
import math
from typing import Any, Optional, Sequence, Union

from sentence_transformers import SentenceTransformer

from config import EMBEDDING_MODEL


# Module-level singleton model cache
_MODEL: Optional[SentenceTransformer] = None


def get_embedding_model(model_name: str = EMBEDDING_MODEL) -> SentenceTransformer:
    """
    Load and cache the sentence-transformers model instance from local cache.

    Enforces the offline-first runtime requirement by specifying local_files_only=True.
    The runtime MUST NOT access external networks or attempt ad-hoc downloads.
    If the model is not found in the local cache, an actionable RuntimeError is raised.
    """
    global _MODEL
    if _MODEL is None:
        try:
            _MODEL = SentenceTransformer(model_name, local_files_only=True)
        except Exception as exc:
            raise RuntimeError(
                f"Embedding model '{model_name}' is not available in the local Hugging Face cache. "
                "SETU operates offline and does not download models at runtime. "
                f"Please provision the model during environment setup using: "
                f"python -c \"from sentence_transformers import SentenceTransformer; SentenceTransformer('{model_name}')\""
            ) from exc
    return _MODEL


def clear_embedding_model_cache() -> None:
    """Clear the cached model instance (primarily for testing)."""
    global _MODEL
    _MODEL = None


def generate_embedding(
    text: Optional[str],
    model: Optional[SentenceTransformer] = None,
) -> list[float]:
    """
    Generate a normalized embedding vector for a given text.

    Args:
        text: Normalized report text to embed.
        model: Optional pre-loaded SentenceTransformer instance.

    Returns:
        A list of floats representing the 384-dimensional embedding.
        If text is empty or None, returns a zero vector of dimension 384.
    """
    if not text or not text.strip():
        # 384 dimensions for paraphrase-multilingual-MiniLM-L12-v2
        return [0.0] * 384

    active_model = model if model is not None else get_embedding_model()
    # normalize_embeddings=True yields unit-norm vectors for consistent cosine distance
    emb = active_model.encode(
        text.strip(),
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    return [float(x) for x in emb.tolist()]


def generate_embeddings(
    texts: Sequence[str],
    model: Optional[SentenceTransformer] = None,
) -> list[list[float]]:
    """
    Batch generate embeddings for multiple texts.

    Args:
        texts: Collection of normalized report texts.
        model: Optional pre-loaded SentenceTransformer instance.

    Returns:
        List of 384-dimensional float vectors.
    """
    if not texts:
        return []

    active_model = model if model is not None else get_embedding_model()
    cleaned = [t.strip() if t and t.strip() else "" for t in texts]

    # Find non-empty indices to encode
    non_empty_indices = [i for i, t in enumerate(cleaned) if t]
    non_empty_texts = [cleaned[i] for i in non_empty_indices]

    results: list[list[float]] = [[0.0] * 384 for _ in texts]

    if non_empty_texts:
        encoded = active_model.encode(
            non_empty_texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        for idx, orig_idx in enumerate(non_empty_indices):
            results[orig_idx] = [float(x) for x in encoded[idx].tolist()]

    return results


def serialize_embedding(embedding: Optional[Sequence[float]]) -> Optional[str]:
    """
    Serialize an embedding vector to a JSON string for DB storage.

    Returns None if embedding is None or empty.
    """
    if embedding is None or len(embedding) == 0:
        return None
    return json.dumps([float(x) for x in embedding])


def deserialize_embedding(data: Union[str, Sequence[float], None]) -> Optional[list[float]]:
    """
    Deserialize a stored embedding from JSON string or list to list of floats.

    Returns None if data is missing or invalid.
    """
    if data is None:
        return None
    if isinstance(data, (list, tuple)):
        return [float(x) for x in data]
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
            if isinstance(parsed, list):
                return [float(x) for x in parsed]
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
    return None


def cosine_similarity(
    vec1: Optional[Sequence[float]],
    vec2: Optional[Sequence[float]],
) -> float:
    """
    Compute cosine similarity between two embedding vectors.

    Returns cosine similarity clamped to the [0.0, 1.0] matching range.
    Returns 0.0 if either vector is None, empty, or has zero norm.
    """
    if vec1 is None or vec2 is None:
        return 0.0
    if len(vec1) == 0 or len(vec2) == 0:
        return 0.0
    if len(vec1) != len(vec2):
        return 0.0

    dot = 0.0
    norm1_sq = 0.0
    norm2_sq = 0.0

    for a, b in zip(vec1, vec2):
        dot += a * b
        norm1_sq += a * a
        norm2_sq += b * b

    if norm1_sq <= 0.0 or norm2_sq <= 0.0:
        return 0.0

    denom = math.sqrt(norm1_sq) * math.sqrt(norm2_sq)
    if denom <= 0.0:
        return 0.0

    cos_sim = dot / denom
    # Clamp cosine similarity to the [0.0, 1.0] matching range
    return max(0.0, min(1.0, cos_sim))
