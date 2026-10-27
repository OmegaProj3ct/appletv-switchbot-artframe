#!/usr/bin/env python3
"""atvframe - show a poster of what your Apple TV is playing on a SwitchBot AI Art Frame.

Behaviour:
- When a film/series plays in one of the streaming apps for 'delay' seconds without
  interruption, put its poster (TMDB, falling back to Apple TV artwork) on the frame.
- Pause keeps the poster. Stop (or Apple TV off/unreachable): remove the poster
  (frame + cloud) and show a random image of your own that is already on the frame.
- Small local HTTP API on 127.0.0.1:8765: /status, /on, /off.
"""
import asyncio, io, json, logging, time, unicodedata
from aiohttp import web
from PIL import Image

import pyatv
from pyatv.const import DeviceState, Protocol
from pyatv.interface import Playing

import framectl

log = logging.getLogger("atvframe")

# Streaming apps are recognised by bundle ID (exact) or by app name (contains, accent-insensitive).
# Unknown apps are logged once; extend via config.json ("services" / "service_names").
DEFAULT_SERVICES = {
    "com.netflix.Netflix": "Netflix",
    "com.disney.disneyplus": "Disney+",
    "com.wbd.hbomax": "HBO Max",
    "com.wbd.stream": "HBO Max",
    "com.apple.TVWatchList": "Apple TV+",
    "com.amazon.aiv.AIVApp": "Prime Video",
}
DEFAULT_SERVICE_NAMES = ["netflix", "disney", "hbo", "apple tv", "prime video", "pathe",
                         "skyshowtime", "crunchyroll"]



TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/w780"


async def tmdb_poster(cfg, name, total_time=None, series_name=None):
    """Find a portrait poster on TMDB. Returns (bytes, description) or (None, reason)."""
    key = (cfg.get("tmdb_key") or "").strip()
    name = series_name or name   # for an episode, search for the series name
    if not key or not name:
        return None, "no tmdb_key or title"
    import aiohttp
    headers, params = {"accept": "application/json"}, {"query": name, "include_adult": "false",
                                                       "language": cfg.get("tmdb_language", "en-US")}
    if len(key) > 40:            # Read Access Token (v4)
        headers["Authorization"] = "Bearer " + key
    else:                        # API Key (v3)
        params["api_key"] = key
    # movie or TV? a series name or a short runtime means an episode
    if series_name:
        want = "tv"
    elif total_time:
        want = "movie" if total_time >= 70 * 60 else "tv"
    else:
        want = None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.get(TMDB_API + "/search/multi", params=params, headers=headers) as r:
            if r.status != 200:
                return None, f"TMDB search HTTP {r.status}"
            data = await r.json()
        cands = [x for x in data.get("results", []) if x.get("media_type") in ("movie", "tv") and x.get("poster_path")]
        if not cands:
            return None, "no TMDB result"
        n = name.strip().lower()

        def score(x):
            t = (x.get("title") or x.get("name") or "").strip().lower()
            sc = x.get("popularity") or 0
            if t == n:
                sc += 1000
            if want and x["media_type"] == want:
                sc += 500
            return sc
        best = max(cands, key=score)
        async with s.get(TMDB_IMG + best["poster_path"]) as r:
            if r.status != 200:
                return None, f"TMDB poster HTTP {r.status}"
            img = await r.read()
        label = f'{best.get("title") or best.get("name")} ({best["media_type"]}, {(best.get("release_date") or best.get("first_air_date") or "")[:4]})'
        return img, label


class App:
    def __init__(self):
        self.cfg = framectl.load_config()
        self.fc = framectl.FrameController(self.cfg)
        self.services = {**DEFAULT_SERVICES, **self.cfg.get("services", {})}
        self.service_names = [n.lower() for n in self.cfg.get("service_names", DEFAULT_SERVICE_NAMES)]
        self.app_names = {}          # bundle ID -> app name as reported by the Apple TV
        self.delay = int(self.cfg.get("delay", 10))
        self.atv = None
        self.connected_at = 0.0
        self.reconnect_every = int(self.cfg.get("reconnect_every", 60))
        self.playing_key = None      # (app, title) currently shown on the frame
        self.candidate = None        # (key, since) candidate still inside the delay
        self.last_seen = {}          # key -> last time seen playing
        self.busy = asyncio.Lock()
        self.unknown_logged = set()
        self.status = {"enabled": self.fc.state.get("enabled", True), "now": None, "atv": "disconnected"}

    # ---------- Apple TV ----------
    async def connect(self):
        loop = asyncio.get_running_loop()
        # credentials come from ~/.pyatv.conf (created by 'atvremote wizard' / 'atvremote pair')
        from pyatv.storage.file_storage import FileStorage
        storage = FileStorage.default_storage(loop)
        await storage.load()
        confs = await pyatv.scan(loop, identifier=self.cfg["atv_id"], timeout=8, storage=storage)
        if not confs:
            raise RuntimeError("Apple TV not found on the network")
        conf = confs[0]
        for proto, cred in self.cfg.get("atv_credentials", {}).items():
            conf.set_credentials(Protocol[proto], cred)
        self.atv = await pyatv.connect(conf, loop, storage=storage)
        self.status["atv"] = "connected"
        if not self.connected_at:
            log.info("connected to Apple TV %s", conf.name)
        self.connected_at = time.time()

    async def poll(self):
        """Read what is playing: (key, state, playing) or None if off/unreachable."""
        try:
            playing: Playing = await asyncio.wait_for(self.atv.metadata.playing(), 10)
            app = self.atv.metadata.app
            app_id = app.identifier if app else None
        except Exception as e:
            log.warning("Apple TV unreachable: %s", e)
            try:
                self.atv.close()
            except Exception:
                pass
            self.atv = None; self.status["atv"] = "disconnected"
            return None
        app_name = (app.name if app else "") or ""
        if app_id:
            self.app_names[app_id] = app_name
        is_service = self.is_service(app_id, app_name)
        if app_id and not is_service and app_id not in self.unknown_logged:
            self.unknown_logged.add(app_id)
            log.info("app %s (%s) is not in the streaming list; ignored", app_id, app_name)
        name = playing.title or playing.series_name or playing.artist or playing.content_identifier or ""
        key = (app_id, name) if is_service else None
        return key, playing.device_state, playing

    def is_service(self, app_id, app_name):
        if not app_id:
            return False
        if app_id in self.services:
            return True
        n = unicodedata.normalize("NFKD", app_name).encode("ascii", "ignore").decode().lower()
        return any(s in n for s in self.service_names)

    def service_label(self, app_id):
        return self.services.get(app_id) or self.app_names.get(app_id) or app_id

    # ---------- frame actions ----------
    async def show_poster(self, key, playing):
        async with self.busy:
            if not self.status["enabled"]:
                return
            data = None
            try:
                data, why = await tmdb_poster(self.cfg, key[1], getattr(playing, "total_time", None),
                                              getattr(playing, "series_name", None))
                if data:
                    log.info("TMDB poster: %s", why)
                else:
                    log.info("no TMDB poster (%s); using Apple TV artwork", why)
            except Exception as e:
                log.warning("TMDB failed: %s; using Apple TV artwork", e)
            if not data:
                try:
                    art = await self.atv.metadata.artwork(width=1200, height=None)
                    data = art.bytes if art else None
                except Exception as e:
                    log.warning("fetching artwork failed: %s", e)
            if not data:
                log.warning("no artwork for %s; removing old poster, showing own image", key)
                self.playing_key = key; self.status["now"] = None
                if self.fc.state["managed"]:
                    try:
                        await self.fc.cleanup_managed(); await self.fc.show_random_own()
                    except Exception as e:
                        log.error("cleanup failed: %s", e)
                return
            img = Image.open(io.BytesIO(data))
            log.info("poster for %s / %s (%dx%d)", self.service_label(key[0]), key[1], *img.size)
            old = list(self.fc.state["managed"])
            try:
                idx = await self.fc.push_image(img)
            except Exception as e:
                # do not keep retrying the same title
                log.error("placing poster failed: %s", e); self.playing_key = key; return
            self.playing_key = key
            self.status["now"] = f"{self.service_label(key[0])}: {key[1]}"
            for o in old:  # remove old poster(s) once the new one is up
                if o != idx:
                    await self.fc.remove_image(o)

    def has_poster(self):
        return self.playing_key is not None or bool(self.fc.state["managed"])

    async def clear_poster(self, reason):
        async with self.busy:
            if not self.fc.state["managed"] and self.playing_key is None:
                return
            log.info("playback stopped (%s): removing poster", reason)
            self.playing_key = None; self.status["now"] = None
            if not self.fc.state["managed"]:
                return
            try:
                await self.fc.cleanup_managed()
                await self.fc.show_random_own()
            except Exception as e:
                log.error("cleanup failed: %s", e)

    # ---------- main loop ----------
    async def run_loop(self):
        while True:
            try:
                # A long-lived pyatv connection sometimes keeps reporting "Playing" after
                # playback stopped. Reconnecting every minute fixes that.
                if self.atv is not None and time.time() - self.connected_at > self.reconnect_every:
                    try:
                        self.atv.close()
                    except Exception:
                        pass
                    self.atv = None
                if self.atv is None:
                    await self.connect()
                res = await self.poll()
                now = time.time()
                if res is None:
                    self.candidate = None
                    if self.has_poster():
                        await self.clear_poster("Apple TV off/unreachable")
                    await asyncio.sleep(15); continue
                key, state, playing = res
                if state == DeviceState.Playing and key is not None:
                    # playing in a streaming app
                    if key == self.playing_key:
                        self.candidate = None
                    elif self.candidate and self.candidate[0] == key:
                        if now - self.candidate[1] >= self.delay:
                            self.candidate = None
                            await self.show_poster(key, playing)
                    else:
                        self.candidate = (key, now)
                elif state in (DeviceState.Paused, DeviceState.Loading, DeviceState.Seeking):
                    # pause/loading/seeking: keep the poster, restart the delay
                    self.candidate = None
                else:
                    # stopped/idle, or something other than a streaming app is playing
                    self.candidate = None
                    if self.has_poster():
                        await self.clear_poster(f"state={state.name if state else state}, app={key}")
            except Exception as e:
                log.error("loop error: %s", e); self.atv = None; self.status["atv"] = "disconnected"
                self.candidate = None
                if self.has_poster():
                    try:
                        await self.clear_poster("Apple TV unreachable")
                    except Exception as e2:
                        log.error("cleanup failed: %s", e2)
                await asyncio.sleep(20)
            await asyncio.sleep(3)

    # ---------- local HTTP API (on/off) ----------
    async def http_status(self, _):
        return web.json_response(self.status)

    async def http_on(self, _):
        self.status["enabled"] = True; self.fc.state["enabled"] = True; framectl.save_state(self.fc.state)
        log.info("enabled via HTTP API")
        return web.json_response(self.status)

    async def http_off(self, _):
        self.status["enabled"] = False; self.fc.state["enabled"] = False; framectl.save_state(self.fc.state)
        log.info("disabled via HTTP API")
        asyncio.create_task(self.clear_poster("disabled"))
        return web.json_response(self.status)

    async def serve(self):
        app = web.Application()
        app.add_routes([web.get("/status", self.http_status), web.get("/on", self.http_on), web.get("/off", self.http_off),
                        web.post("/on", self.http_on), web.post("/off", self.http_off)])
        runner = web.AppRunner(app, access_log=None); await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", int(self.cfg.get("http_port", 8765))).start()
        log.info("HTTP API on 127.0.0.1:%s", self.cfg.get("http_port", 8765))


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("pyatv").setLevel(logging.WARNING)
    app = App()
    await app.serve()
    await app.run_loop()


if __name__ == "__main__":
    asyncio.run(main())
