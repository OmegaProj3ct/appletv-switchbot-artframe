#!/usr/bin/env python3
"""setup_frame - one-time setup and test commands for the SwitchBot AI Art Frame.

Usage (inside the virtualenv, in the project folder):
  python setup_frame.py key          fetch the Bluetooth encryption key via your SwitchBot account
  python setup_frame.py find-device  look up the frame's deviceId via the official API
  python setup_frame.py info         test Bluetooth: read basic info from the frame
  python setup_frame.py album        list the images in the frame's album (cloud)
  python setup_frame.py test         put a test image on the frame (full chain, takes minutes)
  python setup_frame.py show-random  show a random image of your own on the frame
"""
import asyncio, getpass, json, logging, sys, time

import framectl


def need(cfg, *keys):
    missing = [k for k in keys if not cfg.get(k)]
    if missing:
        sys.exit(f"Missing in config.json: {', '.join(missing)}")


async def cmd_key(cfg):
    import aiohttp
    from switchbot.devices.art_frame import SwitchbotArtFrame
    need(cfg, "mac")
    print("Log in with your SwitchBot account e-mail and password.")
    print("(Sign in with Apple/Google does not work here - see the README.)")
    email = input("E-mail: ").strip()
    pw = getpass.getpass("Password: ")
    async with aiohttp.ClientSession() as s:
        res = await SwitchbotArtFrame.async_retrieve_encryption_key(s, cfg["mac"], email, pw)
    cfg["key_id"], cfg["encryption_key"] = res["key_id"], res["encryption_key"]
    print(f"OK: key_id {res['key_id']} and encryption_key saved.")
    print("\natvframe also needs these credentials at runtime to read the frame's album")
    print("(the album list is only available through the SwitchBot app API).")
    if input("Store e-mail and password in config.json (readable only by you)? [y/N] ").strip().lower() == "y":
        cfg["sb_email"], cfg["sb_password"] = email, pw
    framectl.save_config(cfg)


def cmd_find_device(cfg):
    need(cfg, "token", "secret")
    res = framectl.openapi(cfg, "GET", "/v1.1/devices")
    frames = [d for d in (res.get("body") or {}).get("deviceList", []) if d.get("deviceType") == "AI Art Frame"]
    if not frames:
        print(json.dumps(res, indent=2))
        sys.exit("No 'AI Art Frame' found in your device list.")
    for f in frames:
        print(json.dumps(f, indent=2))
    cfg["device_id"] = frames[0]["deviceId"]
    framectl.save_config(cfg)
    print(f"device_id {cfg['device_id']} saved.")


async def cmd_info(cfg):
    need(cfg, "mac", "key_id", "encryption_key")
    inf = await framectl.Frame(cfg).info()
    if not inf:
        sys.exit("No answer. Wrong key, or the SwitchBot app on your phone is still connected?")
    for k, v in inf.items():
        print(f"{k}: {v}")


async def cmd_album(cfg):
    need(cfg, "device_id", "sb_email", "sb_password")
    c = framectl.Cloud(cfg)
    try:
        album = await c.album()
    finally:
        await c.close()
    for idx, img in sorted(album.items()):
        print(idx, {k: v for k, v in img.items() if k != "imageIndex"})


async def cmd_test(cfg):
    from PIL import Image, ImageDraw, ImageFont
    need(cfg, "mac", "key_id", "encryption_key", "token", "secret", "device_id", "sb_email", "sb_password")
    img = Image.new("RGB", (480, 800), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 480, 120], fill="#d00000")          # red bar = top of the poster
    try:
        font = ImageFont.load_default(size=60)
    except TypeError:
        font = ImageFont.load_default()
    d.text((240, 400), "TOP ^", fill="black", font=font, anchor="mm")
    d.text((240, 520), time.strftime("%H:%M"), fill="black", font=font, anchor="mm")
    fc = framectl.FrameController(cfg)
    try:
        idx = await fc.push_image(img)
        print(f"Test image is on the frame as index {idx}. The red bar should be at the top.")
        print("If it is at the bottom, change 'rotate' in config.json (90 <-> 270).")
        if input("Remove the test image again? [Y/n] ").strip().lower() != "n":
            await fc.remove_image(idx)
            await fc.show_random_own()
    finally:
        await fc.close()


async def cmd_show_random(cfg):
    fc = framectl.FrameController(cfg)
    try:
        await fc.show_random_own()
    finally:
        await fc.close()


COMMANDS = {
    "key": cmd_key, "find-device": cmd_find_device, "info": cmd_info,
    "album": cmd_album, "test": cmd_test, "show-random": cmd_show_random,
}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        print(__doc__); sys.exit(1)
    fn = COMMANDS[sys.argv[1]]
    cfg = framectl.load_config()
    if asyncio.iscoroutinefunction(fn):
        asyncio.run(fn(cfg))
    else:
        fn(cfg)
