"""Measure plate-reading accuracy on YOUR camera images.

Put photos in a folder, named after the correct plate:
    KA05MG9999.jpg, KA05MG9999_night.jpg, KA05MG9999_rain_2.jpg, 22BH1234AA.png
A word after the plate that is a known condition (day, night, rain, …) groups
results by condition; anything else after an underscore is ignored. Images
named NOTAPLATE_… must produce no read. The ZIP exported from the dashboard
(Plate Review → Export training data) is already in this format.

Run:
    .venv/bin/python benchmark_ocr.py path/to/folder
    .venv/bin/python benchmark_ocr.py path/to/folder --csv results.csv
    .venv/bin/python benchmark_ocr.py path/to/folder --save-baseline baseline.json
    .venv/bin/python benchmark_ocr.py path/to/folder --baseline baseline.json   # exit 2 if worse

Metrics for the combined reader (what the agent actually uses):
    accuracy      share of images read exactly right
    auto-saved    reads confident enough to be saved without a person (conf ≥ --auto-conf)
    precision     share of auto-saved reads that are right — the number that matters
                  most when nobody checks the gate
    review rate   share that would go to Plate Review instead
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

import plate_rules

EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
NOT_A_PLATE = "NOTAPLATE"
CONDITIONS = {"day", "night", "rain", "dusk", "dawn", "glare", "fog", "dirty", "angle", "tworow", "fast"}
# A new run fails the baseline check when it is worse by more than this (percentage points).
MAX_PRECISION_DROP = 0.5
MAX_ACCURACY_DROP = 1.0


def truth_from_name(p: Path) -> str:
    return plate_rules.clean(p.stem.split("_")[0])


def condition_from_name(p: Path) -> str:
    for token in p.stem.split("_")[1:]:
        if token.lower() in CONDITIONS:
            return token.lower()
    return "unlabelled"


def summarize(rows: List[Dict], auto_conf: float) -> Dict:
    """rows: {truth, got, conf, condition}. Returns overall + per-condition metrics (percentages)."""
    def block(rs: List[Dict]) -> Dict:
        n = len(rs)
        if not n:
            return {"images": 0}
        correct = [r for r in rs if (r["got"] or NOT_A_PLATE) == r["truth"]]
        auto = [r for r in rs if r["got"] and r["conf"] >= auto_conf]
        auto_ok = [r for r in auto if r["got"] == r["truth"]]
        return {
            "images": n,
            "accuracy": round(100.0 * len(correct) / n, 2),
            "auto_saved": len(auto),
            "precision": round(100.0 * len(auto_ok) / len(auto), 2) if auto else None,
            "review_rate": round(100.0 * (n - len(auto)) / n, 2),
            "wrong_auto": sorted({f"{r['truth']}→{r['got']}" for r in auto if r["got"] != r["truth"]}),
        }
    by_cond = defaultdict(list)
    for r in rows:
        by_cond[r["condition"]].append(r)
    return {"auto_conf": auto_conf, "overall": block(rows),
            "by_condition": {c: block(rs) for c, rs in sorted(by_cond.items())}}


def compare(current: Dict, baseline: Dict) -> List[str]:
    """Reasons the current run is worse than the baseline (empty list = OK)."""
    problems = []
    cur, base = current["overall"], baseline["overall"]
    if base.get("precision") is not None and cur.get("precision") is not None \
            and cur["precision"] < base["precision"] - MAX_PRECISION_DROP:
        problems.append(f"precision {cur['precision']}% < baseline {base['precision']}%")
    if cur.get("accuracy", 0) < base.get("accuracy", 0) - MAX_ACCURACY_DROP:
        problems.append(f"accuracy {cur['accuracy']}% < baseline {base['accuracy']}%")
    return problems


def run(images: List[Path], finder, engines: Dict, combined) -> List[Dict]:
    import cv2
    names = list(engines) + ["combined"]
    rows = []
    for img_path in images:
        truth = truth_from_name(img_path)
        img = cv2.imread(str(img_path))
        if img is None:
            continue
        boxes = finder.find(img) if finder else []
        crop = finder.crop(img, boxes[0]) if boxes else img  # close-ups may not need finding
        row = {"image": img_path.name, "truth": truth, "condition": condition_from_name(img_path),
               "plate_found": bool(boxes)}
        for n in names:
            t = time.perf_counter()
            if n == "combined":
                r = combined.read(crop)
                got, conf = (r[0], float(r[1])) if r else ("", 0.0)
                row["got"], row["conf"] = got, conf
            else:
                r = combined._safe(engines[n], crop)
                got = (plate_rules.correct(r.text)[0] or r.text) if r else ""
            row[n] = got
            row[f"{n}_ms"] = round((time.perf_counter() - t) * 1000, 1)
        rows.append(row)
        marks = "  ".join(f"{n}={'✓' if (row[n] or NOT_A_PLATE) == truth else row[n] or '∅'}" for n in names)
        print(f"{img_path.name:<32} {truth:<11} {marks}  conf={row['conf']:.2f}")
    return rows


def _print_report(rows: List[Dict], names: List[str], summary: Dict) -> None:
    n = len(rows)
    print(f"\nPlate found by the finder in {sum(r['plate_found'] for r in rows)}/{n} images")
    print(f"{'reader':<16}{'accuracy':>10}{'avg ms':>9}")
    for name in names:
        ok = sum((r[name] or NOT_A_PLATE) == r["truth"] for r in rows)
        ms = sum(r[f"{name}_ms"] for r in rows) / n
        print(f"{name:<16}{ok / n:>9.1%}{ms:>9.0f}")
    o = summary["overall"]
    print(f"\ncombined, auto-save at conf ≥ {summary['auto_conf']}:")
    print(f"  accuracy {o['accuracy']}%   precision {o['precision']}%   review rate {o['review_rate']}%"
          f"   ({o['auto_saved']} of {o['images']} auto-saved)")
    for cond, b in summary["by_condition"].items():
        print(f"  {cond:<12} images {b['images']:>4}   accuracy {b['accuracy']}%   precision {b['precision']}%"
              f"   review {b['review_rate']}%")
    if o["wrong_auto"]:
        print("  wrong but auto-saved:", ", ".join(o["wrong_auto"][:20]))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--csv", help="write per-image results to this CSV file")
    ap.add_argument("--auto-conf", type=float, default=0.8, help="confidence at which a read counts as auto-saved")
    ap.add_argument("--save-baseline", help="write this run's metrics as the new baseline JSON")
    ap.add_argument("--baseline", help="compare with a saved baseline; exit 2 if worse")
    ap.add_argument("--only-combined", action="store_true", help="skip testing each reader on its own (faster)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    folder = Path(args.folder)
    images = sorted(p for p in folder.rglob("*") if p.suffix.lower() in EXTS)
    if not images:
        print("No images found. Name them after the plate, e.g. KA05MG9999.jpg")
        return 1

    import plate_ocr
    finder = plate_ocr.PlateFinder()
    engines = {}
    if not args.only_combined:
        for key, cls in plate_ocr._ENGINE_CLASSES.items():
            try:
                engines[key] = cls()
            except Exception as e:
                print(f"(skipping {key}: {e})")
    combined = plate_ocr.PlateReader()
    rows = run(images, finder, engines, combined)
    if not rows:
        print("No readable images.")
        return 1
    summary = summarize(rows, args.auto_conf)
    _print_report(rows, list(engines) + ["combined"], summary)
    print(f"\ncombined = {combined.description}")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"Per-image results written to {args.csv}")
    if args.save_baseline:
        Path(args.save_baseline).write_text(json.dumps(summary, indent=2))
        print(f"Baseline saved to {args.save_baseline}")
    if args.baseline:
        problems = compare(summary, json.loads(Path(args.baseline).read_text()))
        if problems:
            print("\nWORSE THAN BASELINE: " + "; ".join(problems))
            return 2
        print("\nNot worse than the baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
