import numpy as np


def float32_vector_to_bytes(vec: np.ndarray) -> bytes:
    v = np.asarray(vec, dtype=np.float32).reshape(-1)
    return v.tobytes()


def bytes_to_float32_vector(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.float32).copy()
