# Plate-reading accuracy: measure, improve, protect

No plate reader is right 100% of the time. The goal is that **every record the
system saves on its own is correct**, and the few reads it isn't sure about go
to **Plate Review** for a quick human check. Two numbers track this:

| Metric | Meaning | Target |
|---|---|---|
| **Precision** | Share of automatically saved reads that are correct | ≥ 99.5 % |
| **Review rate** | Share of vehicles sent to Plate Review | ≤ 3 % |

A lower review rate is never worth a lower precision.

## How the system protects precision

1. **Several reads per car.** Every plate in view is tracked across frames and
   read several times. The reads are voted on character by character.
2. **Two readers.** fast-plate-ocr reads each plate. The Indian-plate model
   (Awiros) is consulted when the first reader is unsure. Agreement raises
   confidence; disagreement lowers it.
3. **Format check.** The read must be a valid Indian plate (state code, RTO
   number, series, 4 digits, or the BH series).
4. **Look-alike fixes cost confidence.** O→0 or I→1 swaps lower confidence a
   little. Risky swaps (A→4, T→7, D→0, 3→B and similar) lower it a lot.
5. **Context.** Reads of known vehicles (inside now, on the *Known plates*
   list, or with a named owner) are trusted more. A read one character away
   from a car that is inside is checked by a person.
6. **Only "high" confidence is saved automatically for new vehicles**
   (`REVIEW_MODE=strict`). Everything else goes to Plate Review.

## Collect a labelled test set (Phase 7 shadow run)

Every decision in Plate Review is saved as a labelled example: the plate image
plus the confirmed text, or "not a plate". Corrections, where the reader was
wrong, are the most valuable.

You also need examples of plates the system was sure about. During the shadow
run, save about 1,000 gate images with the correct plate as the file name:

```
KA05MG9999_day.jpg   MH12AB1234_night.jpg   22BH1234AA_rain_2.jpg   NOTAPLATE_glare.jpg
```

A word after the plate that names a condition (`day`, `night`, `rain`, `dusk`,
`dawn`, `glare`, `fog`, `dirty`, `angle`, `tworow`, `fast`) groups the results
by condition.

**Plate Review → Export training data** (admin) downloads a ZIP already in this
format. Keep the test set **outside the repository**: it contains real
customers' plates.

## Measure

```bash
make benchmark DATA=~/plate-sets/gate-2026-12              # report
cd local_agent && .venv/bin/python benchmark_ocr.py ~/plate-sets/gate-2026-12 \
    --save-baseline ~/plate-sets/baseline.json             # record the current level
make benchmark DATA=~/plate-sets/gate-2026-12 BASELINE=~/plate-sets/baseline.json
```

The last command exits with an error if precision dropped by more than 0.5
points or accuracy by more than 1 point. Run it before changing any setting or
model.

## Tune from data, not guesses

Change one setting at a time and re-run the benchmark (and watch the review
rate on the dashboard for a few days):

| Setting (agent `.env`) | Effect |
|---|---|
| `VOTE_MIN_READS` (3) | More reads per car → higher precision, slower decisions |
| `OCR_SURE` (0.90) | Above this the second reader is skipped. Raise it to cross-check more reads |
| `PLATE_DETECTOR_CONF` (0.35) | Plate-box threshold. Lower finds more plates and more false boxes |
| `DETECTION_FPS` (5) | More frames per car → more reads |
| `--auto-conf` (benchmark only) | Where you draw the auto-save line when measuring |

## Retrain monthly

1. Export training data (Plate Review → Export training data), and add it to
   the training set kept outside the repository.
2. Fine-tune the fast-plate-ocr model on that set. The project
   (github.com/ankandrew/fast-plate-ocr) documents the dataset format and the
   training command. Follow its current documentation, since the tooling
   changes between versions.
3. Benchmark the new model against the **fixed** test set and the saved baseline
   (`FASTPLATE_MODEL` / model path in the agent `.env`).
4. **Promote it only if it is not worse** (the baseline check passes) and
   precision is at least as high. Otherwise keep the current model.
5. Save the new baseline after promoting.

## Vehicles without a standard plate

- **Trade or temporary plates.** Add them in **Settings → Known plates** with
  kind *Trade / temporary*. Exact reads are then accepted even though they don't
  follow the standard format. In Plate Review, tick *Non-standard plate* to save
  one exactly as typed.
- **No plate at all** (new, unregistered cars). With the optional vehicle
  detector (`VEHICLE_GATE=true`), a vehicle that stays in view without a
  readable plate is sent to Plate Review as *No readable plate*, with its photo.
