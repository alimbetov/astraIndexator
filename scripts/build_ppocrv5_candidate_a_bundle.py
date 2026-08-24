#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

CRITICAL_KAZAKH = set("ӘәҒғҚқҢңӨөҰұҮүҺһІі")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_tree(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


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
        if not directory.is_dir() or not any(directory.rglob("*.onnx")):
            raise SystemExit(f"ONNX model directory is invalid: {directory}")
    if not args.dictionary.is_file():
        raise SystemExit(f"Dictionary is missing: {args.dictionary}")

    dictionary_text = args.dictionary.read_text(encoding="utf-8")
    missing = sorted(CRITICAL_KAZAKH - set(dictionary_text))
    if missing:
        raise SystemExit("Dictionary misses required Kazakh characters: " + " ".join(missing))

    root = args.output
    if root.exists():
        shutil.rmtree(root)
    (root / "det").parent.mkdir(parents=True, exist_ok=True)
    copy_tree(args.det_dir, root / "det")
    copy_tree(args.rec_dir, root / "rec")
    shutil.copy2(args.dictionary, root / "rec" / "character_dict.txt")

    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative == "manifest.json":
            continue
        files.append({"path": relative, "sha256": sha256(path), "sizeBytes": path.stat().st_size})

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
        "files": files,
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"bundle={root}")
    print(f"manifestSha256={sha256(manifest_path)}")
    print(f"files={len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
