#!/usr/bin/env python3
"""
TelegramHash Cube Dostiyor farm
tg: 1873407633

409 Conflict / session_superseded:
  server allows ONE live session per user. second session/start kills the first.
  do NOT run two farms on same account. on 409: backoff, re-auth once, continue.

usage:
  python3 telegramhash_cube_farm.py
  python3 telegramhash_cube_farm.py 5
  python3 telegramhash_cube_farm.py 0
"""

import atexit
import json
import os
import random
import socket
import sys
import time
import uuid
from datetime import datetime

import requests

BASE_HOST = "app.telegramhash.com"
BASE = f"https://{BASE_HOST}"
BASE_IP = "194.87.55.22"
UID = 1873407633

INIT = os.environ.get("TH_INIT") or (
    'user={"id":1873407633,"first_name":"Mr.DoSToN","last_name":"| Tasky 🐾",'
    '"username":"DoSToN_SoDiQoV","language_code":"ru","allows_write_to_pm":true,'
    '"photo_url":"https:\\/\\/t.me\\/i\\/userpic\\/320\\/rLoknJGv2MRVPGGTh1gdWnFkbtB-eoE5xCUEWU4wgcA.svg"}'
    "&chat_instance=-7364615783948674929&chat_type=sender&auth_date=1793174803"
    "&signature=Rk5n4X1anbzzvz9crpOPDwSdZF4rd4vEghT-9GEjiIex9JEMMscAnTc-Y8DLp11yvIFoxBy6y5JlDdxRsqMXCw"
    "&hash=e380455703d01f68bc2ef8c00ecb26281a0ae19ee0c759a2feb09c3e27f9c34d"
)

GAMES = int(sys.argv[1]) if len(sys.argv) > 1 else 1
DELAY_BETWEEN_GAMES = 8.0
DELAY_BETWEEN_TAPS = 0.10
MIN_GAME_SECONDS = 16.0
WIN_PAUSE = 1.5
ALL_FACES = ("top", "bottom", "left", "right", "front", "back")
LOCK_PATH = (
    "/tmp/cube_farm.lock" if os.environ.get("GITHUB_ACTIONS")
    else os.path.join(os.path.dirname(os.path.abspath(__file__)) or ".", ".cube_farm.lock")
)


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def acquire_lock() -> None:
    """Single local instance. Stale lock (>2h) is overwritten."""
    if os.path.exists(LOCK_PATH):
        try:
            age = time.time() - os.path.getmtime(LOCK_PATH)
            with open(LOCK_PATH) as f:
                old = f.read().strip()
            if age < 7200:
                log(f"LOCK busy pid/info={old} age={int(age)}s — stop other farm or rm {LOCK_PATH}")
                sys.exit(2)
            log(f"stale lock age={int(age)}s, taking over")
        except Exception:
            pass
    with open(LOCK_PATH, "w") as f:
        f.write(f"{os.getpid()} {time.time()}")

    def _clear():
        try:
            if os.path.exists(LOCK_PATH):
                with open(LOCK_PATH) as f:
                    if str(os.getpid()) in f.read():
                        os.remove(LOCK_PATH)
        except Exception:
            pass

    atexit.register(_clear)


def resolve_base() -> str:
    try:
        socket.getaddrinfo(BASE_HOST, 443, type=socket.SOCK_STREAM)
        return BASE
    except OSError:
        log(f"DNS fail for {BASE_HOST}, using IP {BASE_IP}")
        return f"https://{BASE_IP}"


def normalize_cube(data):
    if not isinstance(data, dict):
        return None
    if data.get("ad_required"):
        return data
    if "cube" in data and isinstance(data["cube"], dict):
        data = data["cube"]
    if any(isinstance(k, str) and "," in k for k in data.keys()):
        return {k: v for k, v in data.items() if isinstance(k, str) and "," in k}
    return None


def build_ad_events(min_view_ms: int = 5000):
    show = uuid.uuid4().hex[:20]
    tx = str(uuid.uuid4())
    note_start = f"method=adShowTryStart showStart net=giga show={show}"
    note_end = (
        f"method=adShowed showEnd net=giga sec={max(5, min_view_ms // 1000)} "
        f"tx={tx} show={show} done=1"
    )
    note_reward = f"method=adReward show={show} done=1"
    t_end = max(min_view_ms + 200, 5200)
    events = [
        {"t": 40, "kind": "script", "host": "gigapub.tech", "path": "/sdk.js", "status": 200, "note": None},
        {"t": 120, "kind": "script", "host": "gigapub.tech", "path": "/show", "status": 200, "note": note_start},
        {"t": 250, "kind": "img", "host": "gigapub.tech", "path": "/pixel.gif", "status": 200, "note": None},
        {"t": t_end - 100, "kind": "script", "host": "gigapub.tech", "path": "/event", "status": 200, "note": note_end},
        {"t": t_end, "kind": "script", "host": "gigapub.tech", "path": "/reward", "status": 200, "note": note_reward},
    ]
    logs = [
        {"t": 100, "text": "init giga"},
        {"t": 130, "text": "adShowTryStart"},
        {"t": t_end - 150, "text": "adplaybackcompleted"},
        {"t": t_end - 80, "text": "reward received"},
        {"t": t_end - 40, "text": "showend"},
        {"t": t_end, "text": "done=1"},
    ]
    return events, logs, t_end


class SessionSuperseded(Exception):
    pass


class RateLimited(Exception):
    def __init__(self, wait: float = 30.0):
        self.wait = wait
        super().__init__(f"rate/conflict wait {wait}s")


class CubeFarm:
    def __init__(self):
        self.base = resolve_base()
        self.s = requests.Session()
        self.s.headers.update(
            {
                "Host": BASE_HOST,
                "Origin": BASE,
                "Referer": BASE + "/",
                "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        if self.base.startswith(f"https://{BASE_IP}"):
            self.s.verify = False
            try:
                import urllib3

                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass
        self.tok = None
        self._conflict_streak = 0

    def _raise_http(self, r: requests.Response) -> None:
        if r.status_code == 409:
            try:
                detail = r.json()
            except Exception:
                detail = r.text
            err = ""
            if isinstance(detail, dict):
                d = detail.get("detail") or detail
                if isinstance(d, dict):
                    err = str(d.get("error") or d.get("reason") or "")
                else:
                    err = str(d)
            if "superseded" in err.lower() or "session" in err.lower():
                raise SessionSuperseded(str(detail))
            raise RateLimited(45.0)
        r.raise_for_status()

    def session_start(self) -> None:
        last = None
        for attempt in range(5):
            try:
                self.base = resolve_base()
                if self.base.startswith(f"https://{BASE_IP}"):
                    self.s.verify = False
                r = self.s.post(
                    f"{self.base}/session/start",
                    json={"initData": INIT, "device_id": str(uuid.uuid4())},
                    timeout=25,
                )
                self._raise_http(r)
                data = r.json()
                self.tok = data["session_token"]
                self.s.headers["Authorization"] = f"Bearer {self.tok}"
                self.s.headers["X-Session-Token"] = self.tok
                self.s.headers["Host"] = BASE_HOST
                self._conflict_streak = 0
                log(f"session ok via {self.base} expires={data.get('expires_at')}")
                return
            except SessionSuperseded as e:
                last = e
                log(f"session superseded attempt {attempt+1}")
                time.sleep(3 + attempt)
            except Exception as e:
                last = e
                log(f"session attempt {attempt+1} fail: {e}")
                time.sleep(2 + attempt)
        raise last

    def get_cube(self, new: bool = False):
        path = f"/get_cube_new/{UID}" if new else f"/get_cube/{UID}"
        r = self.s.get(self.base + path, timeout=20)
        self._raise_http(r)
        return normalize_cube(r.json())

    def attest_ad(self, ad_session: str, min_view_ms: int = 5000) -> dict:
        events, logs, t_end = build_ad_events(min_view_ms)
        body = {
            "ad_session": ad_session,
            "dur_ms": t_end + 300,
            "result": "resolved",
            "events": events,
            "logs": logs,
            "mode": "main",
        }
        r = self.s.post(f"{self.base}/api/cube/ad/attest/{UID}", json=body, timeout=20)
        self._raise_http(r)
        data = r.json()
        if not data.get("ok") and not data.get("idempotent"):
            body_g = {
                "ad_session": ad_session,
                "dur_ms": max(min_view_ms + 500, 5500),
                "result": "resolved",
                "events": [],
                "logs": [],
                "mode": "main",
            }
            r2 = self.s.post(f"{self.base}/api/cube/ad/attest/{UID}", json=body_g, timeout=20)
            if r2.status_code != 409:
                try:
                    self._raise_http(r2)
                    g = r2.json()
                    if g.get("ok"):
                        return g
                except Exception:
                    pass
        return data

    def ensure_cube(self) -> dict:
        state = None
        try:
            state = self.get_cube(new=True)
        except RateLimited:
            raise
        except SessionSuperseded:
            raise
        if state is None:
            state = self.get_cube(False)
        if isinstance(state, dict) and state.get("ad_required"):
            log(f"ad_required provider={state.get('provider')}")
            res = self.attest_ad(state["ad_session"], state.get("min_view_ms", 5000))
            log(f"attest {res}")
            if not res.get("ok") and not res.get("idempotent"):
                # grace_limit — wait and retry once
                if res.get("reason") == "grace_limit":
                    log("grace_limit — sleep 60s")
                    time.sleep(60)
                    res = self.attest_ad(state["ad_session"], state.get("min_view_ms", 5000))
                    log(f"attest retry {res}")
                if not res.get("ok") and not res.get("idempotent"):
                    raise RuntimeError(f"ad blocked: {res}")
            time.sleep(0.5)
            state = self.get_cube(new=True)
            if state is None or (isinstance(state, dict) and state.get("ad_required")):
                state = self.get_cube(False)
        if not state or (isinstance(state, dict) and state.get("ad_required")):
            raise RuntimeError(f"no cube state: {state}")
        return state

    def remove(self, pos: list, face: str) -> dict:
        r = self.s.post(
            f"{self.base}/check_removal/{UID}",
            json={"position": pos, "face": face},
            timeout=15,
        )
        self._raise_http(r)
        return r.json()

    def check_win(self) -> dict:
        r = self.s.post(f"{self.base}/check_win/{UID}", timeout=15)
        self._raise_http(r)
        return r.json()

    def try_remove_cell(self, pos: list, faces) -> dict:
        ordered = []
        for f in list(faces or []):
            if f not in ordered:
                ordered.append(f)
        for f in ALL_FACES:
            if f not in ordered:
                ordered.append(f)
        last = {"success": False}
        for face in ordered:
            try:
                res = self.remove(pos, face)
            except SessionSuperseded:
                raise
            except RateLimited:
                raise
            except Exception as e:
                # soft errors only
                if "409" in str(e):
                    raise RateLimited(30.0)
                continue
            time.sleep(DELAY_BETWEEN_TAPS + random.uniform(0, 0.04))
            last = res
            if res.get("success"):
                return res
        return last

    def clear_cube(self, state: dict) -> bool:
        t0 = time.time()
        idle_rounds = 0
        for _round in range(30):
            if not state:
                return True
            progress = 0
            keys = list(state.keys())
            random.shuffle(keys)
            for key in keys:
                faces = state.get(key) or []
                try:
                    pos = [int(x) for x in key.split(",")]
                except ValueError:
                    continue
                res = self.try_remove_cell(pos, faces)
                if res.get("success"):
                    progress += 1
                    if res.get("last"):
                        log(f"last cell {pos}")
                        return True
            time.sleep(0.2)
            state = self.get_cube(False)
            if state is None:
                return True
            if isinstance(state, dict) and state.get("ad_required"):
                log("unexpected ad mid-clear")
                break
            if not state:
                return True
            if progress == 0:
                idle_rounds += 1
                log(f"stuck cells={len(state)} retry={idle_rounds}")
                if idle_rounds >= 5:
                    log(f"give up: {list(state.keys())}")
                    break
            else:
                idle_rounds = 0
            if time.time() - t0 > 90:
                log("clear timeout")
                break
        return False

    def play_one(self) -> dict:
        t0 = time.time()
        state = self.ensure_cube()
        log(f"cube cells={len(state)}")
        last = self.clear_cube(state)
        elapsed = time.time() - t0
        wait = max(0.0, MIN_GAME_SECONDS - elapsed) + WIN_PAUSE
        if wait > 0:
            time.sleep(wait)
        win = self.check_win()
        if not win.get("success"):
            time.sleep(4)
            win = self.check_win()
        log(f"win last={last} -> {win}")
        return win


def main() -> None:
    acquire_lock()
    farm = CubeFarm()
    farm.session_start()
    i = 0
    while GAMES == 0 or i < GAMES:
        i += 1
        log(f"=== game {i} ===")
        try:
            farm.play_one()
            farm._conflict_streak = 0
        except SessionSuperseded as e:
            farm._conflict_streak += 1
            log(f"session_superseded (another client?) streak={farm._conflict_streak}: {e}")
            # someone else opened a session — back off hard
            wait = min(120, 15 * farm._conflict_streak)
            log(f"cooldown {wait}s — kill other farm/browser tab on this account")
            time.sleep(wait)
            try:
                farm.session_start()
            except Exception as e2:
                log(f"reauth fail {e2}")
                time.sleep(10)
        except RateLimited as e:
            farm._conflict_streak += 1
            wait = e.wait * min(farm._conflict_streak, 4)
            log(f"409/rate — sleep {wait:.0f}s")
            time.sleep(wait)
            try:
                farm.session_start()
            except Exception as e2:
                log(f"reauth fail {e2}")
        except Exception as e:
            log(f"game err {e}")
            if "409" in str(e) or "superseded" in str(e).lower():
                time.sleep(30)
            try:
                farm.session_start()
            except Exception as e2:
                log(f"reauth fail {e2}")
                time.sleep(5)
            time.sleep(3)
        if GAMES == 0 or i < GAMES:
            time.sleep(DELAY_BETWEEN_GAMES + random.uniform(0, 2))


if __name__ == "__main__":
    main()
