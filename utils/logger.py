# utils/logger.py
import datetime

def log(msg: str, error: bool = False):
    print(f"[LOG {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")
    if error:
        with open("errors.log", "a") as f:
            f.write(f"[ERROR {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
