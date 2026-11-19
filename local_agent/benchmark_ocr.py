"""Measure plate-reading accuracy on YOUR camera images.

Put photos in a folder, named after the correct plate:
    KA05MG9999.jpg, KA05MG9999_2.jpg, 22BH1234AA.png, ...
(anything after an underscore is ignored). Images can be full camera frames
or close-ups of the plate.

Run:
    .venv/bin/python benchmark_ocr.py path/to/folder
    .venv/bin/python benchmark_ocr.py path/to/folder --csv results.csv

Every installed reader is tested on its own, plus the combined setup the
agent actually uses (primary reader cross-checked by a second one).
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from pathlib import Path

import cv2

import plate_ocr
import plate_rules

EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def truth_from_name(p: Path) -> str:
    return plate_rules.clean(p.stem.split("_")[0])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--csv", help="write per-image results to this CSV file")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    images = sorted(p for p in Path(args.folder).iterdir() if p.suffix.lower() in EXTS)
    if not images:
        print("No images found. Name them after the plate, e.g. KA05MG9999.jpg")
        return 1

    finder = plate_ocr.PlateFinder()
    engines = {}
    for key, cls in plate_ocr._ENGINE_CLASSES.items():
        try:
            engines[key] = cls()
        except Exception as e:
            print(f"(skipping {key}: {e})")
    combined = plate_ocr.PlateReader()
    names = list(engines) + ["combined"]
    stats = {n: {"ok": 0, "ms": 0.0} for n in names}
    found = 0
    rows = []

    for img_path in images:
        truth = truth_from_name(img_path)
        img = cv2.imread(str(img_path))
        if img is None:
            continue
        boxes = finder.find(img)
        crop = finder.crop(img, boxes[0]) if boxes else img  # close-ups may not need finding
        found += bool(boxes)
        row = {"image": img_path.name, "truth": truth, "plate_found": bool(boxes)}
        for n in names:
            t = time.perf_counter()
            if n == "combined":
                r = combined.read(crop)
                got = r[0] if r else ""
            else:
                r = combined._safe(engines[n], crop)
                got = (plate_rules.correct(r.text)[0] or r.text) if r else ""
            stats[n]["ms"] += (time.perf_counter() - t) * 1000
            stats[n]["ok"] += got == truth
            row[n] = got
        rows.append(row)
        marks = "  ".join(f"{n}={'✓' if row[n] == truth else row[n] or '∅'}" for n in names)
        print(f"{img_path.name:<28} {truth:<11} {marks}")

    n = len(rows)
    print(f"\nPlate found by the finder in {found}/{n} images")
    print(f"{'reader':<16}{'accuracy':>10}{'avg ms':>9}")
    for name in names:
        s = stats[name]
        print(f"{name:<16}{s['ok'] / n:>9.1%}{s['ms'] / n:>9.0f}")
    print(f"\ncombined = {combined.description}")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"Per-image results written to {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
