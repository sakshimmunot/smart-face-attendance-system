import cv2
import numpy as np
import face_recognition
import os
import datetime
import csv
import smtplib
import threading
from queue import Queue
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
import sys
from PIL import Image

load_dotenv()

# ─────────────────────────────────────────────
#  CONFIG  (edit these if needed)
# ─────────────────────────────────────────────
KNOWN_FACES_DIR   = "known_faces"       # put student photos here (name.jpg)
UNKNOWN_FACES_DIR = "unknown_faces"     # unknown visitor captures saved here
ATTENDANCE_DIR    = "attendance"        # all attendance saved here

EMAIL_SENDER    = "sakshimmunot@gmail.com"
EMAIL_PASSWORD  = "eeqs goxc ypxt jihy"
EMAIL_RECEIVER  = "sakshimmunot@gmail.com"

FACE_TOLERANCE        = 0.50
ALERT_COOLDOWN_SEC    = 60
UNKNOWN_RECHECK_MIN   = 5        # reduced to 5 min (was 10)
PROCESS_EVERY_N       = 4
MIN_FACE_PX           = 40       # lowered so small faces still trigger alert
BLUR_THRESHOLD        = 20       # lowered — webcam frames are often soft
BRIGHTNESS_THRESHOLD  = 20       # lowered for indoor lighting


class AttendanceSystem:
    def __init__(self):
        self._make_dirs()
        print("=" * 55)
        print("  🎓  Smart Attendance System  —  starting up")
        print("=" * 55)

        # Known faces
        self.known_encodings: list = []
        self.known_names:     list = []
        self._load_known_faces()

        # Attendance state  {name: datetime first seen today}
        self.present_today: dict = {}
        self.attendance_csv = self._today_csv_path()

        # Unknown face tracking  {encoding_index: last_alert_time}
        self.unknown_encodings:   list = []
        self.unknown_alert_times: list = []
        self.last_global_alert = datetime.datetime.now() - datetime.timedelta(hours=1)

        # Camera
        self.cap = self._init_camera()

        # Thread queues
        self.proc_q   = Queue(maxsize=2)
        self.result_q = Queue(maxsize=2)
        self.frame_n  = 0
        self._proc_thread = threading.Thread(target=self._proc_loop, daemon=True)
        self._proc_thread.start()

        print(f"\nReady. Loaded {len(self.known_names)} students: {', '.join(self.known_names) or 'none'}")
        print(f"Attendance file: {self.attendance_csv}")
        print(f"Alerts will go to: {EMAIL_RECEIVER}")

        # Write CSV with all students marked Absent at start
        self._write_full_csv()

        # Test email on startup so you know immediately if it works
        self._test_email()

        print("\nPress Q to quit.\n")

    # ── directory setup ──────────────────────────────────────
    def _make_dirs(self):
        for d in [KNOWN_FACES_DIR, UNKNOWN_FACES_DIR, ATTENDANCE_DIR]:
            os.makedirs(d, exist_ok=True)

    # ── single fixed CSV path (one file forever) ─────────────
    def _today_csv_path(self) -> str:
        return os.path.join(ATTENDANCE_DIR, "attendance_all.csv")

    # ── camera init ───────────────────────────────────────────
    def _init_camera(self):
        print("📷  Initialising camera…")
        cap = cv2.VideoCapture(0)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 30)
        if not cap.isOpened():
            sys.exit("❌  Cannot open camera.")
        ret, _ = cap.read()
        if not ret:
            sys.exit("❌  Cannot read from camera.")
        print("✅  Camera ready.")
        return cap

    # ── load student face images ──────────────────────────────
    def _load_known_faces(self):
        print(f"📂  Loading student faces from '{KNOWN_FACES_DIR}/' …")
        for fname in os.listdir(KNOWN_FACES_DIR):
            if not fname.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            name = os.path.splitext(fname)[0].replace("_", " ").title()
            path = os.path.join(KNOWN_FACES_DIR, fname)
            try:
                img  = face_recognition.load_image_file(path)
                encs = face_recognition.face_encodings(img)
                if encs:
                    self.known_encodings.append(encs[0])
                    self.known_names.append(name)
                    print(f"   ✔  {name}")
                else:
                    print(f"   ⚠  No face found in {fname} — skipped")
            except Exception as e:
                print(f"   ✗  Error loading {fname}: {e}")

    # ── write today's session to the single master CSV ────────
    def _write_full_csv(self):
        """Rewrites only today's rows in the master CSV, preserving all past records."""
        date_str = datetime.date.today().strftime("%d-%m-%Y")

        # Read existing rows from past sessions
        past_rows = []
        if os.path.exists(self.attendance_csv):
            with open(self.attendance_csv, "r", newline="") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                for row in reader:
                    if row and row[1] != date_str:   # keep rows NOT from today
                        past_rows.append(row)

        # Build today's rows
        today_rows = []
        for name in sorted(self.known_names):
            if name in self.present_today:
                t = self.present_today[name]
                today_rows.append([name, date_str, t.strftime("%H:%M:%S"), "Present"])
            else:
                today_rows.append([name, date_str, "-", "Absent"])

        # Write everything back: past + today
        with open(self.attendance_csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Name", "Date", "Time", "Status"])
            for row in past_rows:
                w.writerow(row)
            for row in today_rows:
                w.writerow(row)

    # ── mark attendance in CSV ────────────────────────────────
    def _mark_attendance(self, name: str):
        if name in self.present_today:
            return  # already marked
        now = datetime.datetime.now()
        self.present_today[name] = now
        print(f"ATTENDANCE MARKED  -->  {name}  at {now.strftime('%H:%M:%S')}")
        self._write_full_csv()  # rewrite full sheet so absent rows stay

    # ── image quality check ───────────────────────────────────
    def _good_quality(self, face_bgr) -> bool:
        if face_bgr is None or face_bgr.size == 0:
            return False
        h, w = face_bgr.shape[:2]
        if h < MIN_FACE_PX or w < MIN_FACE_PX:
            return False
        gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
        if cv2.Laplacian(gray, cv2.CV_64F).var() < BLUR_THRESHOLD:
            return False
        if np.mean(gray) < BRIGHTNESS_THRESHOLD:
            return False
        return True

    # ── save unknown face image ───────────────────────────────
    def _save_unknown(self, frame, loc) -> str | None:
        top, right, bottom, left = loc
        face_bgr = frame[top:bottom, left:right]
        if face_bgr is None or face_bgr.size == 0:
            print("SAVE SKIP: empty face crop")
            return None
        h, w = face_bgr.shape[:2]
        if h < MIN_FACE_PX or w < MIN_FACE_PX:
            print(f"SAVE SKIP: face too small ({w}x{h}px, need {MIN_FACE_PX}px)")
            return None
        gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
        blur = cv2.Laplacian(gray, cv2.CV_64F).var()
        brightness = np.mean(gray)
        if blur < BLUR_THRESHOLD:
            print(f"SAVE SKIP: too blurry (score={blur:.1f}, need {BLUR_THRESHOLD})")
            return None
        if brightness < BRIGHTNESS_THRESHOLD:
            print(f"SAVE SKIP: too dark (brightness={brightness:.1f}, need {BRIGHTNESS_THRESHOLD})")
            return None
        ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(UNKNOWN_FACES_DIR, f"unknown_{ts}.jpg")
        rgb  = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2RGB)
        Image.fromarray(rgb).save(path)
        print(f"SAVED unknown face: {path}")
        return path

    # ── unknown face cooldown check ───────────────────────────
    def _should_alert_unknown(self, encoding) -> bool:
        now = datetime.datetime.now()

        # Check if this face was seen before
        if self.unknown_encodings:
            matches = face_recognition.compare_faces(
                self.unknown_encodings, encoding, tolerance=FACE_TOLERANCE)
            if True in matches:
                idx = matches.index(True)
                elapsed_min = (now - self.unknown_alert_times[idx]).total_seconds() / 60
                if elapsed_min < UNKNOWN_RECHECK_MIN:
                    return False   # same face, still in cooldown → show "Tracking"
                # cooldown passed for this face — allow re-alert
                self.unknown_alert_times[idx] = now
                return True

        # Brand new unknown face — register and alert immediately
        self.unknown_encodings.append(encoding)
        self.unknown_alert_times.append(now)
        if len(self.unknown_encodings) > 30:
            self.unknown_encodings.pop(0)
            self.unknown_alert_times.pop(0)
        return True

    # ── startup email test ────────────────────────────────────
    def _test_email(self):
        print("\n--- Testing email connection ---")
        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as s:
                s.ehlo()
                s.starttls()
                s.ehlo()
                s.login(EMAIL_SENDER, EMAIL_PASSWORD)
            print("EMAIL OK - login successful, alerts will work!")
        except smtplib.SMTPAuthenticationError:
            print("EMAIL FAILED - Wrong password or Gmail App Password not set up correctly.")
            print("  Fix: Go to myaccount.google.com/apppasswords and regenerate the password.")
        except smtplib.SMTPConnectError:
            print("EMAIL FAILED - Cannot connect to Gmail. Check your internet connection.")
        except Exception as e:
            print(f"EMAIL FAILED - {type(e).__name__}: {e}")
        print("--------------------------------\n")

    # ── send email alert ──────────────────────────────────────
    def _send_alert(self, image_path: str | None):
        try:
            now = datetime.datetime.now()
            msg = MIMEMultipart()
            msg["From"]    = EMAIL_SENDER
            msg["To"]      = EMAIL_RECEIVER
            msg["Subject"] = "Unknown Person Detected in Classroom!"

            html = f"""
            <html><body style="font-family:Arial,sans-serif;color:#222;">
              <h2 style="color:#c0392b;">SECURITY ALERT - Unknown Person</h2>
              <p>An <strong>unrecognised person</strong> was detected in the classroom.</p>
              <table style="border-collapse:collapse;">
                <tr><td style="padding:4px 12px 4px 0;color:#555;">Date and Time</td>
                    <td><strong>{now.strftime('%d %b %Y  %H:%M:%S')}</strong></td></tr>
                <tr><td style="padding:4px 12px 4px 0;color:#555;">Action needed</td>
                    <td>Please verify the visitor's identity.</td></tr>
              </table>
              <p style="margin-top:16px;">A photo is attached for reference.</p>
              <hr style="margin-top:24px;">
              <p style="font-size:11px;color:#999;">Smart Attendance System - automated alert</p>
            </body></html>"""
            msg.attach(MIMEText(html, "html"))

            if image_path and os.path.exists(image_path):
                with open(image_path, "rb") as f:
                    img = MIMEImage(f.read())
                    img.add_header("Content-Disposition", "attachment",
                                   filename=os.path.basename(image_path))
                    msg.attach(img)

            print(f"Sending alert email to {EMAIL_RECEIVER} ...")
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as s:
                s.ehlo()
                s.starttls()
                s.ehlo()
                s.login(EMAIL_SENDER, EMAIL_PASSWORD)
                s.send_message(msg)

            self.last_global_alert = datetime.datetime.now()
            print(f"ALERT EMAIL SENT successfully to {EMAIL_RECEIVER}")
        except smtplib.SMTPAuthenticationError:
            print("EMAIL FAILED - Authentication error. Check your Gmail App Password.")
        except Exception as e:
            print(f"EMAIL FAILED - {type(e).__name__}: {e}")

    # ── frame processing (background thread) ─────────────────
    def _proc_loop(self):
        while True:
            item = self.proc_q.get()
            if item is None:
                break
            frame = item
            result = self._process_frame(frame)
            if not self.result_q.full():
                self.result_q.put(result)

    def _process_frame(self, frame):
        small = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
        rgb   = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

        locations = face_recognition.face_locations(rgb, model="hog")  # hog = fast, cnn = accurate but slow
        encodings = face_recognition.face_encodings(rgb, locations)

        for enc, loc in zip(encodings, locations):
            name   = "Unknown"
            color  = (0, 0, 220)   # red for unknown
            status = ""

            # Match against known students
            if self.known_encodings:
                distances = face_recognition.face_distance(self.known_encodings, enc)
                best      = int(np.argmin(distances))
                if distances[best] <= FACE_TOLERANCE:
                    name  = self.known_names[best]
                    color = (34, 180, 34)   # green for known

            # Scale location back to full frame
            top, right, bottom, left = [x * 4 for x in loc]

            if name != "Unknown":
                self._mark_attendance(name)
                status = "Present"
            else:
                # Try to save and alert
                if self._should_alert_unknown(enc):
                    img_path = self._save_unknown(frame, (top, right, bottom, left))
                    if img_path:
                        status = "ALERT SENT"
                        threading.Thread(
                            target=self._send_alert, args=(img_path,), daemon=True
                        ).start()
                    else:
                        status = "Unknown"
                else:
                    status = "Tracking"

            # Draw bounding box
            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)

            # Name label bar
            label_y = bottom + 30
            cv2.rectangle(frame, (left, bottom), (right, label_y), color, cv2.FILLED)
            cv2.putText(frame, name,
                        (left + 6, label_y - 8),
                        cv2.FONT_HERSHEY_DUPLEX, 0.65,
                        (255, 255, 255), 1)

            # Status label bar
            if status:
                stat_y = label_y + 22
                cv2.rectangle(frame, (left, label_y), (right, stat_y),
                              (50, 50, 50), cv2.FILLED)
                cv2.putText(frame, status,
                            (left + 6, stat_y - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                            (200, 255, 200), 1)

        # Overlay: present count + time
        self._draw_overlay(frame)
        return frame

    def _draw_overlay(self, frame):
        h, w = frame.shape[:2]
        now  = datetime.datetime.now().strftime("%d %b %Y  |  %H:%M:%S")

        # Dark semi-transparent bar at top
        bar = frame.copy()
        cv2.rectangle(bar, (0, 0), (w, 44), (20, 20, 20), cv2.FILLED)
        cv2.addWeighted(bar, 0.6, frame, 0.4, 0, frame)

        cv2.putText(frame, f"Smart Attendance  |  Present: {len(self.present_today)}/{len(self.known_names)}",
                    (10, 28), cv2.FONT_HERSHEY_DUPLEX, 0.65, (255, 220, 80), 1)
        cv2.putText(frame, now,
                    (w - 260, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 180, 180), 1)

    # ── main loop ─────────────────────────────────────────────
    def run(self):
        last_frame = None
        while True:
            ret, frame = self.cap.read()
            if not ret:
                print("❌  Frame grab failed — exiting.")
                break

            self.frame_n += 1
            if self.frame_n % PROCESS_EVERY_N == 0:
                if not self.proc_q.full():
                    self.proc_q.put(frame.copy())

            display = self.result_q.get() if not self.result_q.empty() else frame
            last_frame = display
            cv2.imshow("Smart Attendance System", display)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        self._shutdown()

    def _shutdown(self):
        self.proc_q.put(None)
        self._proc_thread.join(timeout=3)
        self.cap.release()
        cv2.destroyAllWindows()
        self._write_full_csv()  # final save with all absent/present
        self._print_summary()

    def _print_summary(self):
        print("\n" + "=" * 55)
        print(f"  SESSION SUMMARY  —  {datetime.date.today()}")
        print("=" * 55)
        if self.present_today:
            for name, t in sorted(self.present_today.items(), key=lambda x: x[1]):
                print(f"  ✔  {name:<25}  {t.strftime('%H:%M:%S')}")
        else:
            print("  No students marked present.")
        print(f"\n  Absentees ({len(self.known_names) - len(self.present_today)}):")
        for name in self.known_names:
            if name not in self.present_today:
                print(f"  ✗  {name}")
        print(f"\n  CSV saved → {self.attendance_csv}")
        print("=" * 55)


if __name__ == "__main__":
    AttendanceSystem().run()
