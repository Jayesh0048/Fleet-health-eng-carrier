#!/usr/bin/env python3
import os
# Prevent CUDA memory fragmentation
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
os.environ["OMP_NUM_THREADS"] = "4"

import gc
import sys
from pathlib import Path
import torch
from ultralytics import YOLO

# ============================================================
# CONFIGURATION
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent

# Target YAML (prior dataset path)
DATA_YAML = Path("/home/techtruckers/Pictures/fleet_health_agent_starter/data.yaml")

# Fallback check if data_fixed.yaml is the active file
if not DATA_YAML.exists():
    fallback_yaml = Path("/home/techtruckers/Pictures/fleet_health_agent_starter/data_fixed.yaml")
    if fallback_yaml.exists():
        DATA_YAML = fallback_yaml

MODEL_WEIGHTS = "yolo11s.pt"
EPOCHS = 60                          # 50 Epochs Target
IMGSZ = 640
RUN_NAME = "accident_combined_s_60ep"
BATCH_SIZES = [32, 16, 8]           # Fallback batch ladder if VRAM spikes
WORKERS = 6


def verify_environment():
    if not torch.cuda.is_available():
        sys.exit(" Error: CUDA GPU not detected. Training requires GPU.")
    if not DATA_YAML.exists():
        sys.exit(f" Error: data.yaml not found at {DATA_YAML}")

    gpu_name = torch.cuda.get_device_name(0)
    vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
    print("\n" + "=" * 65)
    print(f" COMPUTE TARGET: {gpu_name} ({vram_gb:.1f} GB VRAM)")
    print(f" MODEL: {MODEL_WEIGHTS} | EPOCHS: {EPOCHS} | IMGSZ: {IMGSZ}")
    print(f" DATASET: {DATA_YAML}")
    print(f" RUN NAME: {RUN_NAME}")
    print("=" * 65 + "\n")


def train():
    verify_environment()
    last_ckpt = PROJECT_ROOT / "runs" / RUN_NAME / "weights" / "last.pt"

    for batch in BATCH_SIZES:
        model = None
        try:
            # Resume from last.pt if interrupted, otherwise start from pretrained weights
            weights_to_load = str(last_ckpt) if last_ckpt.exists() else MODEL_WEIGHTS
            print(f">>> Launching training loop: batch={batch}, base={Path(weights_to_load).name}")

            model = YOLO(weights_to_load)
            model.train(
                data=str(DATA_YAML),
                epochs=EPOCHS,
                imgsz=IMGSZ,
                batch=batch,
                workers=WORKERS,
                device=0,
                project=str(PROJECT_ROOT / "runs"),
                name=RUN_NAME,
                exist_ok=True,
                patience=25,               # Early stopping tolerance for 75 epochs
                amp=True,                  # FP16 mixed precision
                plots=True,
                optimizer="AdamW",
                lr0=0.001,
                lrf=0.01,
                box=8.0,
                cls=2.0,
                dfl=2.0,
                # --- CALIBRATED AUGMENTATION PROFILE ---
                # Reduced extreme jitter to eliminate cloud/sky hallucinations
                hsv_v=0.4,                 # Controlled brightness variation
                hsv_s=0.5,                 # Moderate saturation shifts
                hsv_h=0.015,
                erasing=0.15,              # Occlusion handling without creating abstract shapes
                bgr=0.05,
                mosaic=1.0,
                mixup=0.10,
                close_mosaic=15,           # Disable mosaic for last 15 epochs for crisp boundaries
            )
            return

        except RuntimeError as e:
            if "out of memory" not in str(e).lower():
                raise e
            print(f" GPU Out of Memory with batch={batch}. Dropping batch size...")
            del model
            gc.collect()
            torch.cuda.empty_cache()

    sys.exit(" Error: Unable to train even at minimum batch size.")


def evaluate():
    best_weights = PROJECT_ROOT / "runs" / RUN_NAME / "weights" / "best.pt"
    if not best_weights.exists():
        sys.exit(f" Weights not found at {best_weights}")

    print(f"\n>>> Running Final Evaluation on Test Split: {best_weights.name}")
    model = YOLO(str(best_weights))
    metrics = model.val(data=str(DATA_YAML), split="test", device=0, batch=16, imgsz=IMGSZ)
    print("-" * 65)
    print(f" Final Test Results -> mAP50: {metrics.box.map50:.3f} | mAP50-95: {metrics.box.map:.3f}")
    print("-" * 65)


if __name__ == "__main__":
    train()
    evaluate()