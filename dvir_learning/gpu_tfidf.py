from __future__ import annotations

import os
import warnings
from typing import Iterable, Any
import numpy as np
import torch
import torch.nn.functional as F
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.feature_extraction.text import TfidfVectorizer

warnings.filterwarnings("ignore", category=UserWarning, module="torch.sparse")
if hasattr(torch.sparse, "check_sparse_tensor_invariants"):
    torch.sparse.check_sparse_tensor_invariants.disable()


def resolve_training_device(device: str | None = None) -> str:
    target = device or os.getenv("DVIR_TRAIN_DEVICE")
    if target:
        target = target.strip().lower()
        if target == "cuda":
            target = "cuda:0"
        if target.startswith("cuda") and not torch.cuda.is_available():
            return "cpu"
        return target
    return "cuda:0" if torch.cuda.is_available() else "cpu"


def _to_torch_sparse(csr_matrix, device: str = "cpu") -> torch.Tensor:
    coo = csr_matrix.tocoo()
    indices = torch.tensor(np.vstack((coo.row, coo.col)), dtype=torch.long)
    values = torch.tensor(coo.data, dtype=torch.float32)
    shape = torch.Size(coo.shape)
    return torch.sparse_coo_tensor(indices, values, shape, device=device).coalesce()


class GPUTextClassifier(BaseEstimator, ClassifierMixin):
    def __init__(
        self,
        vectorizer: Any | None = None,
        training_device: str | None = None,
        max_epochs: int = 10,
        seed: int = 42,
        lr: float = 0.05,
        temperature: float = 1.450,
        **kwargs: Any,
    ):
        self.vectorizer = vectorizer
        self.training_device = training_device
        self.max_epochs = max_epochs
        self.seed = seed
        self.lr = lr
        self.temperature = temperature

        self.vectorizer_ = vectorizer
        self.training_device_ = resolve_training_device(training_device)
        self.classes_: np.ndarray | None = None
        self.w_: torch.Tensor | None = None
        self.b_: torch.Tensor | None = None

    def __getattr__(self, name: str) -> Any:
        # Cross-fallback between legacy unpickled artifacts and new code
        if name == "vectorizer_":
            return self.__dict__.get("vectorizer")
        if name == "vectorizer":
            return self.__dict__.get("vectorizer_")
        if name == "training_device_":
            return self.__dict__.get("training_device", "cuda:0" if torch.cuda.is_available() else "cpu")
        if name == "training_device":
            return self.__dict__.get("training_device_", "cuda:0" if torch.cuda.is_available() else "cpu")
        raise AttributeError(f"\x27{type(self).__name__}\x27 object has no attribute \x27{name}\x27")

    def device_report(self) -> dict[str, Any]:
        dev = getattr(self, "training_device_", getattr(self, "training_device", "cpu"))
        return {
            "device": dev,
            "cuda_available": torch.cuda.is_available(),
            "device_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU",
        }

    def fit(self, texts: Iterable[str], labels: Iterable[Any]) -> "GPUTextClassifier":
        text_list = [str(t) for t in texts]
        label_list = list(labels)

        self.classes_ = np.unique(label_list)
        class_to_idx = {cls: idx for idx, cls in enumerate(self.classes_)}
        y_indices = np.array([class_to_idx[lbl] for lbl in label_list], dtype=np.int64)

        vec = getattr(self, "vectorizer_", None) or getattr(self, "vectorizer", None)
        if vec is None:
            vec = TfidfVectorizer(ngram_range=(1, 2), min_df=1)
            X_sparse = vec.fit_transform(text_list).astype(np.float32).tocsr()
        else:
            if hasattr(vec, "fit_transform"):
                X_sparse = vec.fit_transform(text_list).astype(np.float32).tocsr()
            else:
                X_sparse = vec.transform(text_list).astype(np.float32).tocsr()

        self.vectorizer_ = vec
        self.vectorizer = vec

        device = getattr(self, "training_device_", None) or resolve_training_device(self.training_device)
        self.training_device_ = device
        n_samples, n_features = X_sparse.shape
        n_classes = len(self.classes_)

        torch.manual_seed(self.seed)
        np.random.seed(self.seed)

        try:
            x_tensor = _to_torch_sparse(X_sparse, device)
            y_tensor = torch.tensor(y_indices, dtype=torch.long, device=device)

            w = torch.zeros((n_features, n_classes), dtype=torch.float32, device=device, requires_grad=True)
            b = torch.zeros((n_classes,), dtype=torch.float32, device=device, requires_grad=True)

            optimizer = torch.optim.SGD([w, b], lr=self.lr)
            criterion = torch.nn.CrossEntropyLoss()

            epochs = max(1, int(self.max_epochs))
            for _ in range(epochs):
                optimizer.zero_grad()
                logits = torch.sparse.mm(x_tensor, w) + b
                loss = criterion(logits, y_tensor)
                loss.backward()
                optimizer.step()

            self.w_ = w.detach()
            self.b_ = b.detach()
        except Exception:
            from sklearn.linear_model import SGDClassifier
            base = SGDClassifier(loss="log_loss", max_iter=self.max_epochs, random_state=self.seed)
            base.fit(X_sparse, y_indices)
            if n_classes == 2:
                w_np = np.vstack([-base.coef_, base.coef_]).T
                b_np = np.array([-base.intercept_[0], base.intercept_[0]])
            else:
                w_np = base.coef_.T
                b_np = base.intercept_
            self.w_ = torch.tensor(w_np, dtype=torch.float32, device=device)
            self.b_ = torch.tensor(b_np, dtype=torch.float32, device=device)

        return self

    def predict_proba(self, texts: Iterable[str]) -> np.ndarray:
        text_list = [str(t) for t in texts]
        vec = getattr(self, "vectorizer_", None) or getattr(self, "vectorizer", None)
        w = getattr(self, "w_", None)
        b = getattr(self, "b_", None)

        if vec is None or w is None or b is None:
            raise ValueError("Model is not fitted yet.")

        features = vec.transform(text_list).astype(np.float32).tocsr()
        device = getattr(self, "training_device_", None) or getattr(self, "training_device", "cpu")
        x = _to_torch_sparse(features, device)

        with torch.no_grad():
            w_dev = w.to(device)
            b_dev = b.to(device)
            logits = torch.sparse.mm(x, w_dev) + b_dev
            temp = getattr(self, "temperature", 1.450) or 1.450
            scaled_logits = logits / temp
            probs = F.softmax(scaled_logits, dim=1).cpu().numpy()

        return probs

    def predict(self, texts: Iterable[str]) -> np.ndarray:
        probs = self.predict_proba(texts)
        indices = np.argmax(probs, axis=1)
        return self.classes_[indices]
