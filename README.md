# 🎓 Smart Face Attendance System

Real-time student attendance using face recognition.  
Unknown visitors trigger an **email alert** to `sakshimmunot@gmail.com`.

---

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Add student photos
Place one clear, front-facing photo per student in the `known_faces/` folder.  
**Name the file after the student:**
```
known_faces/
  john_doe.jpg       →  marks as "John Doe"
  priya_sharma.jpg   →  marks as "Priya Sharma"
  sumit.jpg          →  marks as "Sumit"
```
- Supported formats: `.jpg`, `.jpeg`, `.png`
- One face per photo
- Good lighting, no sunglasses

### 3. Configure email (`.env`)
The `.env` file is pre-filled.  
If alerts stop working, regenerate the Gmail App Password at:  
https://myaccount.google.com/apppasswords

### 4. Run
```bash
python face_attendance.py
```
Press **Q** to quit.

---

## How It Works

| Scenario | What Happens |
|---|---|
| Student from `known_faces/` appears | Green box + name; **marked Present in CSV** |
| Same student appears again | Ignored (attendance already marked) |
| Unknown person appears | Red box; face saved; **alert email sent** to `sakshimmunot@gmail.com` |

---

## Output Files

| Path | Contents |
|---|---|
| `attendance/attendance_YYYY-MM-DD.csv` | Daily attendance log |
| `unknown_faces/unknown_YYYYMMDD_HHMMSS.jpg` | Captured unknown face photos |

### CSV format
```
Name,Date,Time,Status
John Doe,2025-04-10,09:03:45,Present
Priya Sharma,2025-04-10,09:05:12,Present
```

---

## Settings (top of `face_attendance.py`)

| Variable | Default | Meaning |
|---|---|---|
| `FACE_TOLERANCE` | `0.50` | Lower = stricter matching |
| `ALERT_COOLDOWN_SEC` | `60` | Seconds between alert emails |
| `UNKNOWN_RECHECK_MIN` | `10` | Minutes before same unknown re-alerts |
| `MIN_FACE_PX` | `80` | Ignore faces smaller than this |
| `PROCESS_EVERY_N` | `2` | Process every Nth frame (speed) |

---

## Troubleshooting

**Camera not opening** — check it's not used by another app; try changing `VideoCapture(0)` to `VideoCapture(1)`.

**Face not recognised** — lower `FACE_TOLERANCE` to `0.45`; ensure the photo has good lighting.

**Email not sending** — re-generate Gmail App Password; make sure 2-Step Verification is ON.

**High CPU usage** — increase `PROCESS_EVERY_N` to `3` or `4`.
