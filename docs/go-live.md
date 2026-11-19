# Shadow run and go-live

Manual gate logging stops only after the system has **proved itself on your
gate**, measured against the guard's log.

## 1. Shadow run (2 weeks)

The system runs fully automatically. The guard keeps the usual register and
also fills in a simple sheet each day, saved as CSV:

```csv
time,plate,direction
2026-12-02 10:04,KA05MG9999,in
2026-12-02 10:41,KA05MG9999,out
```

`time` is local time (`YYYY-MM-DD HH:MM`), and `direction` is `in` or `out`.
Entering it in a spreadsheet and saving it as CSV is fine.

Every morning, on the server (or anywhere with database access):

```bash
docker compose exec backend python maintenance.py shadow-compare --log /data/gate-2026-12-02.csv --csv /data/result-2026-12-02.csv
```

The report lists:

- **Precision:** of the records the system saved on its own, how many are
  right. Wrong plates and invented events lower it.
- **Recall:** of the cars in the guard's log, how many the system recorded
  correctly, automatically or through Plate Review.
- **Misread:** a car recorded under a plate one or two characters off.
- **Missed:** a car in the log with no record in the system.
- **Extra:** a record the log doesn't have. Check each one: it is either a
  guard omission or a false record.

Follow up every misread, miss and extra the same day:

| Finding | Usual cause | Fix |
|---|---|---|
| Missed at night | IR too weak, shutter too slow | Camera settings (docs/hardware.md) |
| Missed, cars too fast | No speed bump, low `DETECTION_FPS` | Speed bump; `DETECTION_FPS=8` |
| Misread, risky characters (0/D, 1/I, 8/B) | Plate too small or blurred | Zoom in; shutter |
| Extra entries of parked cars | ROI includes the parking area | Tighten `roi` |
| Many Plate Review items | Plate too small, or glare | Camera placement |

Also save the plate images from the review queue. **Plate Review → Export
training data** is the start of your labelled test set (see docs/accuracy.md).
Aim for 1,000+ images covering day, night and rain.

## 2. Pass criteria (all of them, 7 days in a row)

- [ ] Precision **≥ 99.5 %** every day.
- [ ] Review rate **≤ 3 %** of vehicles (dashboard digest: *Sent to review*).
- [ ] **No missed vehicle** that the system could have read. Covered or missing
      plates are expected, and appear in Plate Review as *No readable plate*.
- [ ] No lost detections: the outbox depth returns to 0 after every network
      drop.
- [ ] The alerts arrived when they should have (test once by unplugging a
      camera).
- [ ] A backup was taken every night, and one **test restore** succeeded:
      `python maintenance.py restore-backup --apply`.

If a day fails, fix the cause and the 7-day count starts again.

## 3. Go-live

1. Agree on one person (and a backup person) who clears **Plate Review** at
   least twice a day. It takes a few minutes, on a phone or PC.
2. Stop the manual register.
3. For the first month, run `shadow-compare` weekly on a 1-hour manual sample
   to confirm precision holds.
4. Keep `REVIEW_MODE=strict`.
