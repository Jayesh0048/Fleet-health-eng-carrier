#!/usr/bin/env python3
"""Smoke-test the DVIR PyTorch classifier on synthetic text, without touching the DB."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import torch
from dvir_learning.gpu_tfidf import GPUTextClassifier, resolve_training_device


def main() -> None:
    device = resolve_training_device()
    if not device.startswith("cuda"):
        raise SystemExit(
            f"FAIL: resolved device is {device}, not CUDA. Check PyTorch and set DVIR_TRAIN_DEVICE=cuda."
        )

    texts = []
    labels = []
    examples = {
        "BRAKES": [
            "brake pedal feels soft and air line leaking",
            "braking response weak air pressure drops",
            "brake chamber leak reported by driver",
            "service brake warning and low air pressure",
            "brake pedal vibration during stopping",
            "air leak near rear brake assembly",
            "brakes require inspection due to weak response",
            "brake system pressure falls when pedal pressed",
        ],
        "TIRES": [
            "front tire tread is worn and sidewall damaged",
            "rear tire pressure low and tread nearly bald",
            "flat tire found during pre trip inspection",
            "tire sidewall bulge requires replacement",
            "uneven tire wear on left rear wheel",
            "tire pressure warning and visible puncture",
            "worn tread measured on steer axle tire",
            "driver reports vibration from damaged tire",
        ],
        "COOLING": [
            "engine coolant leaking near radiator hose",
            "temperature high and radiator coolant low",
            "coolant reservoir empty after engine overheating",
            "radiator fan not cooling engine properly",
            "coolant leak visible beneath engine compartment",
            "engine temperature rising due to cooling problem",
            "radiator hose split and coolant dripping",
            "cooling system pressure low and engine hot",
        ],
    }
    for label, notes in examples.items():
        for idx, note in enumerate(notes):
            texts.append(f"{note}; synthetic smoke-test example {idx}")
            labels.append(label)

    torch.cuda.reset_peak_memory_stats()
    model = GPUTextClassifier(max_epochs=10, learning_rate=0.05, seed=42)
    model.fit(texts, labels)
    predictions = model.predict(texts[:3])
    torch.cuda.synchronize()

    print("CUDA device:", torch.cuda.get_device_name(0))
    print("Training device:", model.training_device_)
    print("Prediction sample:", predictions.tolist())
    print("Peak allocated CUDA memory:", round(torch.cuda.max_memory_allocated() / 1024**2, 2), "MiB")
    if not str(model.training_device_).startswith("cuda"):
        raise SystemExit("FAIL: model did not train on CUDA")
    if len(predictions) != 3:
        raise SystemExit("FAIL: unexpected prediction count")
    print("DVIR GPU TRAINING SMOKE TEST: PASSED")


if __name__ == "__main__":
    main()
