#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

CRITICAL_KAZAKH = set("ӘәҒғҚқҢңӨөҰұҮүҺһІі")
REQUIRED_RUNTIME_FILES = ("inference.onnx", "inference.yml")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_runtime_files(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_RUNTIME_FILES:
        src = source / name
        if not src.is_file():
            raise SystemExit(f"Required runtime file is missing: {src}")
        shutil.copy2(src, target / name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build AstraIndexator PP-OCRv5 Candidate A ONNX bundle")
    parser.add_argument("--det-dir", type=Path, required=True, help="Official PP-OCRv5 mobile detector ONNX directory")
    parser.add_argument("--rec-dir", type=Path, required=True, help="Official Cyrillic PP-OCRv5 mobile recognizer ONNX directory")
    parser.add_argument("--dictionary", type=Path, required=True, help="Cyrillic recognition character dictionary")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-revision", required=True)
    parser.add_argument("--engine-version", default="3.5")
    args = parser.parse_args()

    for directory in (args.det_dir, args.rec_dir):
        if not directory.is_dir():
            raise SystemExit(f"Model directory is invalid: {directory}")
        for name in REQUIRED_RUNTIME_FILES:
            if not (directory / name).is_file():
                raise SystemExit(f"Required runtime file is missing: {directory / name}")
    if not args.dictionary.is_file():
        raise SystemExit(f"Dictionary is missing: {args.dictionary}")

    dictionary_text = args.dictionary.read_text(encoding="utf-8")
    missing = sorted(CRITICAL_KAZAKH - set(dictionary_text))
    if missing:
        raise SystemExit("Dictionary misses required Kazakh characters: " + " ".join(missing))

    root = args.output
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    copy_runtime_files(args.det_dir, root / "det")
    copy_runtime_files(args.rec_dir, root / "rec")
    shutil.copy2(args.dictionary, root / "rec" / "character_dict.txt")

    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative == "manifest.json":
            continue
        files.append({"path": relative, "sha256": sha256(path), "sizeBytes": path.stat().st_size})

    tenge_present = "₸" in dictionary_text
    number_sign_present = "№" in dictionary_text
    manifest = {
        "schemaVersion": "astra-indexator-ocr-model-v1",
        "modelKind": "OCR",
        "modelId": "ppocrv5-mobile-det-cyrillic-mobile-rec-onnx-fp32",
        "engine": "paddleocr",
        "engineVersion": args.engine_version,
        "inferenceEngine": "onnxruntime",
        "executionProvider": "CPUExecutionProvider",
        "precision": "fp32",
        "artifactRevision": args.artifact_revision,
        "languages": ["kk", "ru", "en"],
        "textDetectionModelDir": "det",
        "textRecognitionModelDir": "rec",
        "candidate": "TZ-15A-CANDIDATE-A",
        "qualificationStatus": "CANDIDATE",
        "upstream": {
            "detector": "PaddlePaddle/PP-OCRv5_mobile_det_onnx",
            "recognizer": "PaddlePaddle/cyrillic_PP-OCRv5_mobile_rec_onnx"
        },
        "criticalTokenCoverage": {
            "kazakhCriticalAlphabet": True,
            "numberSign": number_sign_present,
            "tengeSymbol": tenge_present
        },
        "knownLimitations": ([] if tenge_present else ["Recognizer vocabulary does not contain U+20B8 KAZAKHSTANI TENGE SIGN (₸)."]),
        "files": files,
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"bundle={root}")
    print(f"manifestSha256={sha256(manifest_path)}")
    print(f"files={len(files)}")
    print(f"tengeSymbol={'PRESENT' if tenge_present else 'MISSING'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
