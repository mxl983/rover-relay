# Deployed models (checked into git)

Stable inference weights written only by full training scripts (not experiments):

| File | Part | Produced by |
|------|------|-------------|
| `part1_cat_detector.pt` | 1 — YOLO cat detector | `python train_cat.py` |
| `part2_posture_cnn.pth` | 2 — posture CNN | `python posture_network.py` |

`experiment_*.py` never overwrites these. Live demo defaults:

```bash
python detect_live.py --source 0
```
