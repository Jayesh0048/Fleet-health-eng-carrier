from dvir_learning.gpu_tfidf import GPUTextClassifier, resolve_training_device


def test_gpu_backend_cpu_fallback_smoke(monkeypatch):
    monkeypatch.setenv("DVIR_TRAIN_DEVICE", "cpu")
    texts = [
        "brake pedal soft air leak number one",
        "brake pedal soft air leak number two",
        "brakes weak air pressure low number three",
        "brakes weak air pressure low number four",
        "tire tread worn flat tire number one",
        "tire tread worn flat tire number two",
        "sidewall damaged pressure low tire number three",
        "sidewall damaged pressure low tire number four",
    ]
    labels = ["BRAKES"] * 4 + ["TIRES"] * 4
    model = GPUTextClassifier(max_epochs=2, seed=7).fit(texts, labels)
    pred = model.predict(texts[:2])
    assert len(pred) == 2
    assert model.training_device_ == "cpu"


def test_device_respects_explicit_cpu(monkeypatch):
    monkeypatch.setenv("DVIR_TRAIN_DEVICE", "cpu")
    assert resolve_training_device() == "cpu"
