"""framectl - control a SwitchBot AI Art Frame from a Raspberry Pi, without a SwitchBot Hub.

How a new image gets onto the frame:
  1. image -> official OpenAPI `uploadImage` (the cloud stores it in the frame's album)
  2. internal app API `images/list` -> find the new `imageIndex`
  3. Bluetooth `570F7A01 01 <idx>` (sync this index) + `570F7A00` (connect to Wi-Fi now)
     -> the frame wakes up and downloads the image itself
  4. Bluetooth `570F7A02 <idx>` -> show that image
Removing: Bluetooth `570F7A01 02 <idx>` + internal API `images/delete`.
"""
import asyncio, base64, hashlib, hmac, io, json, logging, random, time, urllib.error, urllib.request, uuid
from pathlib import Path

import aiohttp
from PIL import Image, ImageFilter

log = logging.getLogger("framectl")
HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
STATE_PATH = HERE / "state.json"
OPENAPI = "https://api.switch-bot.com"
BLE_SCAN_TRIES = 4
BLE_SCAN_TIMEOUT = 20


# ---------------- config / state ----------------

def load_config():
    return json.loads(CONFIG_PATH.read_text())


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    CONFIG_PATH.chmod(0o600)


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    # managed = album indexes that this program put on the frame (posters)
    return {"enabled": True, "managed": [], "current": None}


def save_state(st):
    STATE_PATH.write_text(json.dumps(st, indent=2))


# ---------------- image processing ----------------

def make_portrait(img, cfg):
    """Make a portrait image (default 480x800) and rotate it to the panel orientation."""
    pw, ph = int(cfg.get("portrait_w", 480)), int(cfg.get("portrait_h", 800))
    img = img.convert("RGB")
    w, h = img.size
    if h >= w * 1.1:
        # portrait source: scale and crop to fill
        scale = max(pw / w, ph / h)
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
        left, top = (img.width - pw) // 2, (img.height - ph) // 2
        out = img.crop((left, top, left + pw, top + ph))
    else:
        # landscape/square source: fit it on a blurred, darkened background
        bg = img.resize((pw, ph), Image.LANCZOS).filter(ImageFilter.GaussianBlur(24))
        bg = Image.eval(bg, lambda v: int(v * 0.45))
        scale = pw / w
        fg = img.resize((pw, round(h * scale)), Image.LANCZOS)
        bg.paste(fg, (0, (ph - fg.height) // 2))
        out = bg
    # The panel itself is landscape (800x480). Rotate so the poster is upright
    # when the frame hangs in portrait. Use 90 or 270 depending on how you hang it.
    rot = int(cfg.get("rotate", 90))
    if rot:
        out = out.rotate(rot, expand=True)
    return out


def to_jpeg(img, quality=88):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


# ---------------- official OpenAPI (v1.1) ----------------

def openapi(cfg, method, path, body=None):
    t = str(int(time.time() * 1000)); nonce = str(uuid.uuid4())
    sign = base64.b64encode(hmac.new(cfg["secret"].encode(), (cfg["token"] + t + nonce).encode(), hashlib.sha256).digest()).decode()
    headers = {"Authorization": cfg["token"], "sign": sign, "t": t, "nonce": nonce, "Content-Type": "application/json; charset=utf8"}
    req = urllib.request.Request(OPENAPI + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"statusCode": e.code, "message": e.read().decode(errors="replace")}


def openapi_upload(cfg, jpeg_bytes):
    b64 = "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode()
    res = openapi(cfg, "POST", f"/v1.1/devices/{cfg['device_id']}/commands",
                  {"command": "uploadImage", "parameter": {"imageBase64": b64}, "commandType": "command"})
    if res.get("statusCode") != 100:
        raise RuntimeError(f"uploadImage failed: {res}")
    return res


# ---------------- internal app API (album list / delete) ----------------
# Not documented by SwitchBot. Found in the SwitchBot Android app; may change without notice.

class Cloud:
    def __init__(self, cfg):
        self.cfg = cfg
        self.session = None
        self.token = cfg.get("session_token")
        self.region = cfg.get("region", "eu")
        self.base = "api.switchbot.net"

    async def _s(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def login(self):
        # Re-uses pySwitchbot's account login (the same one it uses to fetch the BLE key).
        # These are private pySwitchbot helpers; tested with PySwitchbot 3.1.0.
        from switchbot.devices.device import SwitchbotBaseDevice as B, _extract_region
        s = await self._s()
        auth = await B._get_auth_result(s, self.cfg["sb_email"], self.cfg["sb_password"])
        self.token = auth["access_token"]
        info = await B._async_get_user_info(s, {"authorization": self.token})
        self.region = _extract_region(info)
        self.cfg["session_token"] = self.token; self.cfg["region"] = self.region
        save_config(self.cfg)
        log.info("SwitchBot cloud: logged in (region %s)", self.region)

    async def _post(self, path, body, retry=True):
        if not self.token:
            await self.login()
        s = await self._s()
        url = f"https://wonderlabs.{self.region}.{self.base}{path}"
        async with s.post(url, json=body, headers={"authorization": self.token}) as r:
            txt = await r.text()
            if r.status in (401, 403) and retry:
                await self.login(); return await self._post(path, body, retry=False)
            try:
                data = json.loads(txt)
            except ValueError:
                raise RuntimeError(f"cloud {path}: HTTP {r.status}: {txt[:200]}")
            if data.get("resultCode") not in (100, 200) and retry and "token" in txt.lower():
                await self.login(); return await self._post(path, body, retry=False)
            return data

    async def album(self):
        d = await self._post("/device/artframe/api/v1/images/list", {"deviceID": self.cfg["device_id"]})
        imgs = (d.get("data") or {}).get("images") or []
        return {int(i["imageIndex"]): i for i in imgs}

    async def delete(self, indexes):
        return await self._post("/device/artframe/api/v1/images/delete",
                                {"deviceID": self.cfg["device_id"], "groupID": "", "imageIndexList": [int(i) for i in indexes], "deleteAll": False})


# ---------------- Bluetooth ----------------

class Frame:
    def __init__(self, cfg):
        self.cfg = cfg
        self.dev = None

    async def _connect(self):
        from bleak import BleakScanner
        from switchbot.devices.art_frame import SwitchbotArtFrame
        for i in range(BLE_SCAN_TRIES):
            d = await BleakScanner.find_device_by_address(self.cfg["mac"], timeout=BLE_SCAN_TIMEOUT)
            if d:
                self.dev = SwitchbotArtFrame(d, key_id=self.cfg["key_id"], encryption_key=self.cfg["encryption_key"])
                return self.dev
            log.warning("frame not found over Bluetooth (try %d/%d)", i + 1, BLE_SCAN_TRIES)
            await asyncio.sleep(5)
        raise RuntimeError("frame not found over Bluetooth")

    async def _close(self):
        if self.dev:
            try:
                await self.dev._execute_forced_disconnect()
            except Exception:
                pass
            self.dev = None

    async def run(self, *cmds, info=True):
        """Connect, send hex commands, return (info, [responses])."""
        last_err = None
        for attempt in range(3):
            try:
                dev = await self._connect()
                try:
                    inf = await dev.get_basic_info() if info else None
                    out = []
                    for c in cmds:
                        r = await dev._send_command(c)
                        out.append(r.hex() if r else None)
                        if not r or r[0] != 1:
                            raise RuntimeError(f"frame rejected {c}: {r.hex() if r else None}")
                    return inf, out
                finally:
                    await self._close()
            except Exception as e:  # BLE to a sleepy battery device is flaky: retry
                last_err = e; log.warning("BLE attempt %d failed: %s", attempt + 1, e)
                await asyncio.sleep(3)
        raise RuntimeError(f"BLE failed: {last_err}")

    async def info(self):
        inf, _ = await self.run()
        return inf

    async def sync(self, idx):
        return await self.run(f"570F7A0101{idx:02X}", "570F7A00", info=False)

    async def show(self, idx):
        return await self.run(f"570F7A02{idx:02X}", info=False)

    async def delete(self, idx):
        return await self.run(f"570F7A0102{idx:02X}", info=False)


# ---------------- high-level chain ----------------

class FrameController:
    def __init__(self, cfg=None):
        self.cfg = cfg or load_config()
        self.cloud = Cloud(self.cfg)
        self.frame = Frame(self.cfg)
        self.state = load_state()

    async def close(self):
        await self.cloud.close()

    async def push_image(self, img, show=True, timeout=180):
        """Put a PIL image on the frame. Returns its album index."""
        jpeg = to_jpeg(make_portrait(img, self.cfg))
        before = await self.cloud.album()
        log.info("uploading to cloud (%d KB)", len(jpeg) // 1024)
        openapi_upload(self.cfg, jpeg)
        idx = None
        for _ in range(24):
            await asyncio.sleep(5)
            now = await self.cloud.album()
            new = [i for i in now if i not in before]
            if new:
                idx = new[0]; break
        if idx is None:
            raise RuntimeError("upload did not appear in the album list")
        log.info("cloud assigned index %d; sync + wake over BLE", idx)
        self.state["managed"] = sorted(set(self.state["managed"]) | {idx}); save_state(self.state)
        await self.frame.sync(idx)
        t0 = time.time()
        while time.time() - t0 < timeout:
            await asyncio.sleep(15)
            try:
                inf = await self.frame.info()
            except Exception as e:
                log.warning("info failed: %s", e); continue
            if idx in inf["all_images_index"]:
                log.info("index %d is on the frame after %.0f s", idx, time.time() - t0)
                break
        else:
            raise RuntimeError(f"index {idx} did not appear on the frame")
        if show:
            await self.frame.show(idx)
            self.state["current"] = idx; save_state(self.state)
        return idx

    async def remove_image(self, idx):
        log.info("removing index %d (frame + cloud)", idx)
        try:
            await self.frame.delete(idx)
        except Exception as e:
            log.warning("BLE delete failed: %s", e)
        try:
            await self.cloud.delete([idx])
        except Exception as e:
            log.warning("cloud delete failed: %s", e)
        self.state["managed"] = [i for i in self.state["managed"] if i != idx]
        if self.state.get("current") == idx:
            self.state["current"] = None
        save_state(self.state)

    async def cleanup_managed(self):
        for idx in list(self.state["managed"]):
            await self.remove_image(idx)

    async def show_random_own(self):
        """Show a random image that was NOT put there by this program."""
        inf = await self.frame.info()
        choices = [i for i in inf["all_images_index"] if i not in self.state["managed"]]
        if not choices:
            log.warning("no own images on the frame to show"); return None
        idx = random.choice(choices)
        await self.frame.show(idx)
        log.info("showing random own image: %d", idx)
        return idx
