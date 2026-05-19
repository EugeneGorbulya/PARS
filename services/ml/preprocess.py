from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class TabularScaler:
    mean: np.ndarray
    std: np.ndarray

    @classmethod
    def fit(cls, x: np.ndarray) -> "TabularScaler":
        mean = x.mean(axis=0).astype(np.float32)
        std = (x.std(axis=0) + 1e-6).astype(np.float32)
        return cls(mean, std)

    def transform(self, x: np.ndarray) -> np.ndarray:
        return ((x.astype(np.float32) - self.mean) / self.std).astype(np.float32)

    def state_dict(self) -> dict:
        return {"mean": self.mean.tolist(), "std": self.std.tolist()}

    @classmethod
    def from_state_dict(cls, d: dict) -> "TabularScaler":
        return cls(np.array(d["mean"], dtype=np.float32), np.array(d["std"], dtype=np.float32))
