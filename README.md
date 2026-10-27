# Apple TV poster on a SwitchBot AI Art Frame (no Hub needed)

When you start a film or series on your Apple TV, this shows its poster on a
[SwitchBot AI Art Frame](https://www.switch-bot.com/). When you stop watching, the poster
is removed and the frame goes back to one of your own pictures.

It runs on a Raspberry Pi (a Pi Zero 2 W is enough) and does **not** need a SwitchBot Hub:
the Pi talks to the frame over Bluetooth.

> **Unofficial project.** This is not made, supported or endorsed by SwitchBot, Apple or TMDB.
> It uses one SwitchBot API that is **not documented** (see [How it works](#how-it-works)), so a
> SwitchBot app or firmware update may break it at any time. Use at your own risk.

---

## Contents

1. [What it does](#what-it-does)
2. [How it works](#how-it-works)
3. [What you need](#what-you-need)
4. [Step-by-step installation](#step-by-step-installation)
5. [Configuration reference](#configuration-reference)
6. [Turning it on and off](#turning-it-on-and-off)
7. [Troubleshooting](#troubleshooting)
8. [Known limitations](#known-limitations)
9. [Protocol reference](#protocol-reference)
10. [Credits and sources](#credits-and-sources)
11. [License](#license)

---

## What it does

- Something starts playing in one of the big streaming apps on the Apple TV: Netflix, Disney+,
  HBO Max, Apple TV+, Prime Video, Pathé Thuis, SkyShowtime or Crunchyroll. The list can be
  changed.
- If it keeps playing for 10 seconds (so zapping doesn't trigger it), the Pi looks up the poster
  on [TMDB](https://www.themoviedb.org/), turns it into a portrait image and puts it on the
  frame. If TMDB has no match, the Apple TV's own artwork is used.
- **Pause** keeps the poster up.
- **Stop**, switching to another app, or turning the Apple TV off removes the poster from the
  frame and from the cloud. The frame then shows a random picture of your own that is already on it.
- A small local web API (`/on`, `/off`, `/status`) lets you switch the whole thing on and off,
  for example from a home-automation system.

Expect the poster to appear about **1.5 to 3 minutes** after you press play. The frame is a
battery-powered e-ink device that is asleep most of the time, so every step has to wait for it.

## How it works

SwitchBot's own answer is that you need a Hub to change images remotely. The official
cloud API can upload an image, but without a Hub that image only lands in the app's album
and never reaches the frame.

This project closes that gap with the Pi:

```
 Apple TV ──(pyatv, local network)──► Raspberry Pi ──(TMDB)──► poster
                                          │
     1. upload poster ───────────────────►│ SwitchBot OpenAPI  (official: uploadImage)
     2. find its album index ────────────►│ SwitchBot app API  (undocumented: images/list)
     3. "download index N" + "wake up" ──►│ Bluetooth          (via pySwitchbot)
            frame turns on Wi-Fi and downloads the image from the SwitchBot cloud itself
     4. "show index N" ──────────────────►│ Bluetooth
```

- **Bluetooth** commands to the frame are encrypted. [pySwitchbot](https://github.com/sblibs/pySwitchbot)
  handles that, using a key it fetches once with your SwitchBot account.
- **The album index** of a fresh upload is not returned by the official API. It comes from the
  same endpoint the SwitchBot app itself uses. That endpoint was found by inspecting the
  SwitchBot Android app (see [Credits](#credits-and-sources)).
- **Removing** a poster uses a Bluetooth delete command plus the app API's delete endpoint, so the
  10-image album doesn't fill up.

## What you need

**Hardware**
- A SwitchBot AI Art Frame. Tested with the **7.3" model** (800×480 panel, firmware 1.4). The
  13.3" and 31.5" models are untested and will need different image sizes.
- A Raspberry Pi with Bluetooth and Wi-Fi, within Bluetooth range of the frame (a few metres).
  Tested on a **Raspberry Pi Zero 2 W**. Any Pi 3/4/5 should work as well.
- An Apple TV on the same network as the Pi.
- Optional but recommended: power the frame from its USB cable. Every poster is a Wi-Fi download
  plus an e-ink refresh, which drains the battery much faster than SwitchBot's rated
  "about 2 years at one change per week".

**Accounts**
- A **SwitchBot account with an e-mail address and password**. If you signed up with *Sign in
  with Apple* or Google, see [step 3](#3-get-your-switchbot-credentials) first.
- A free **TMDB account** for an API key: themoviedb.org → Settings → API.

**Software**
- **Raspberry Pi OS Lite, 64-bit** (bookworm). The 64-bit OS matters: the install below relies on
  ready-made `aarch64` packages so nothing needs to be compiled on the Pi.
- Some comfort with a terminal and SSH.

## Step-by-step installation

The commands assume the user is `pi` and the project lives in `/home/pi/atvframe`. If your user
or folder is different, change the paths in `atvframe.service` too.

### 1. Prepare the Pi

Flash Raspberry Pi OS Lite (64-bit) with [Raspberry Pi Imager](https://www.raspberrypi.com/software/),
enable SSH and Wi-Fi in the Imager settings, boot it and log in over SSH. Then:

```bash
sudo apt update && sudo apt install -y git python3-venv bluetooth bluez
git clone https://github.com/OmegaProj3ct/appletv-switchbot-artframe.git ~/atvframe
cd ~/atvframe
python3 -m venv venv
```

### 2. Install the Python packages

On a Pi Zero, install in this exact order and **don't run anything else at the same time**.
A plain `pip install pyatv` tries to compile the `miniaudio` audio library. On a Pi Zero
(512 MB RAM) that can use up all memory and swap, and the Pi then stops responding until you
pull the power.

```bash
cd ~/atvframe

# a) SwitchBot / Bluetooth / images
./venv/bin/pip install --no-cache-dir -r requirements.txt

# b) a stand-in for 'miniaudio'. pyatv only needs it to stream audio TO the Apple TV,
#    which this project never does.
cp stubs/miniaudio.py venv/lib/python3.*/site-packages/

# c) pyatv without its dependencies, then the dependencies as ready-made packages only
./venv/bin/pip install --no-cache-dir --no-deps pyatv==0.18.0
./venv/bin/pip install --no-cache-dir --only-binary=:all: -r requirements-pyatv.txt
```

pip may print a red warning that `miniaudio` is missing. That is expected and harmless.

`srptools` is pinned to **1.0.1** on purpose. With srptools 2.x, pairing with the Apple TV
fails with `SRPClientSession.__init__() takes 2 positional arguments but 3 were given`.

Check that everything imports:

```bash
./venv/bin/python -c "import pyatv, switchbot, PIL; print('ok')"
```

### 3. Get your SwitchBot credentials

You need four things from the **SwitchBot app** on your phone:

| What | Where |
|---|---|
| **Token** and **secret** (official API) | Profile → Preferences → About → tap *App Version* 10 times → *Developer Options* → *Get Token* |
| The frame's **Bluetooth MAC address** | Open the frame → settings (gear) → device info. Looks like `B0:E9:FE:xx:xx:xx` |
| Your account **e-mail** | Profile |
| Your account **password** | See below |

**Signed up with Sign in with Apple or Google?** Then your account has no password yet, and the
Bluetooth key cannot be fetched. In the SwitchBot app, set a password for your account first.
Your login e-mail is then the address shown in your profile. With Apple's *Hide my e-mail* that is
an `@privaterelay.appleid.com` address. If you use two-factor authentication, turn it off while
fetching the key; the script cannot enter a code.

Then create your config:

```bash
cd ~/atvframe
cp config.example.json config.json
chmod 600 config.json
nano config.json      # fill in "mac", "token" and "secret"
```

### 4. Connect to the frame

**Close the SwitchBot app on your phone completely.** The frame accepts only one Bluetooth
connection at a time, and while the app is connected the Pi can't even see the frame.

```bash
cd ~/atvframe
./venv/bin/python setup_frame.py key           # asks for e-mail + password, stores the Bluetooth key
./venv/bin/python setup_frame.py find-device   # stores the frame's device_id
./venv/bin/python setup_frame.py info          # Bluetooth test
```

- `key` stores `key_id` and `encryption_key`. It then asks whether to store your e-mail and
  password in `config.json` as well. **Answer yes:** atvframe needs them at runtime to log in to
  the SwitchBot app API for the album list. The file is readable only by your user.
- `info` should print something like `battery: 100`, `firmware: 1.4` and `all_images_index: [...]`.
  If the frame isn't found, wait a minute and try again. The frame stops advertising over Bluetooth
  for a while after it has been on Wi-Fi.

### 5. Set the frame to show one picture (not a slideshow)

In the SwitchBot app, make sure the frame shows a **single fixed picture**, not a slideshow.
In slideshow mode the frame moves on to its next picture by itself, and the poster disappears
while you are still watching.

Also make sure at least one picture of your own is on the frame. That is what it falls back
to after a film. The frame and its cloud album hold at most **10 images**, and each poster
temporarily takes one place, so keep at most 9 of your own.

### 6. Test the whole chain

```bash
./venv/bin/python setup_frame.py test
```

This uploads a test image with a **red bar at the top** and puts it on the frame. It takes
1.5–3 minutes; the log shows each step. Then:

- **Red bar at the top:** orientation is correct.
- **Red bar at the bottom:** set `"rotate": 270` in `config.json` (the default is 90). The right
  value depends on which way round you hang the frame.

Answer `Y` to remove the test image afterwards.

### 7. Get a TMDB API key

On themoviedb.org: Settings → API → request a (free, personal) key. Put either the
**API Key** or the longer **API Read Access Token** in `config.json` as `"tmdb_key"`; both work.

Without a TMDB key, the Apple TV's own artwork is used. That is often a small, square image
that looks poor on a portrait frame.

### 8. Pair with the Apple TV

Find your Apple TV:

```bash
./venv/bin/atvremote scan
```

Copy one of the **Identifiers** of your Apple TV into `config.json` as `"atv_id"`. Then pair
twice, once per protocol. Each time a 4-digit PIN appears on the TV, which you type in the
terminal:

```bash
./venv/bin/atvremote --id <ATV_ID> --protocol airplay pair
./venv/bin/atvremote --id <ATV_ID> --protocol companion pair
```

The credentials are stored in `~/.pyatv.conf`.

**No PIN appears on the TV?** Wake the TV first. Then on the Apple TV go to *Settings → AirPlay
and HomeKit → Allow Access* and temporarily choose *Everyone on the same network*. Pair, then set
it back to what you had. Playback info still works afterwards.

Check that it works by starting something on the Apple TV and running:

```bash
./venv/bin/atvremote --id <ATV_ID> playing
./venv/bin/atvremote --id <ATV_ID> app
```

### 9. Run it

First run it in the foreground and watch the log:

```bash
cd ~/atvframe
./venv/bin/python atvframe.py
```

Start a film in, say, Netflix. After 10 seconds you should see lines like `TMDB poster: …`,
`uploading to cloud`, `cloud assigned index …` and `index … is on the frame`. Then stop the film
and look for `playback stopped … removing poster`. Press `Ctrl+C` to quit.

### 10. Start automatically at boot

```bash
sudo cp ~/atvframe/atvframe.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now atvframe
systemctl status atvframe          # should say "active (running)"
journalctl -u atvframe -f          # follow the log
```

The service restarts the program automatically 30 seconds after a crash.

## Configuration reference

All settings live in `config.json` (see `config.example.json`).

| Key | Meaning |
|---|---|
| `mac` | Bluetooth MAC address of the frame |
| `device_id` | The frame's cloud ID. Filled in by `setup_frame.py find-device`. It is the MAC address without colons |
| `key_id`, `encryption_key` | Bluetooth key. Filled in by `setup_frame.py key` |
| `token`, `secret` | Official SwitchBot API credentials |
| `sb_email`, `sb_password` | SwitchBot account login, used for the album list |
| `session_token`, `region` | Filled in automatically after logging in |
| `atv_id` | Apple TV identifier from `atvremote scan` |
| `tmdb_key` | TMDB API key (v3) or Read Access Token (v4) |
| `tmdb_language` | Language for TMDB searches, default `en-US`. Try `nl-NL` etc. for local titles |
| `rotate` | `90` or `270`, see step 6 |
| `portrait_w`, `portrait_h` | Poster size before rotation, default `480` × `800` (7.3" frame) |
| `delay` | Seconds of continuous playback before a poster is shown, default `10` |
| `reconnect_every` | Reconnect to the Apple TV every N seconds, default `60` (see Troubleshooting) |
| `http_port` | Port of the local on/off API, default `8765` |
| `services` | Optional extra apps by bundle ID, e.g. `{"com.example.app": "Example"}` |
| `service_names` | Optional list replacing the default app-name matches (`netflix`, `disney`, `hbo`, `apple tv`, `prime video`, `pathe`, `skyshowtime`, `crunchyroll`) |

Apps that are not recognised are logged once with their bundle ID, so you can add them to `services`.

## Turning it on and off

The program listens on `127.0.0.1:8765`, which is reachable only from the Pi itself:

```bash
curl 127.0.0.1:8765/status   # {"enabled": true, "now": "Netflix: …", "atv": "connected"}
curl 127.0.0.1:8765/off      # stop showing posters (also removes the current poster)
curl 127.0.0.1:8765/on
```

The on/off state survives restarts. Anything that can make an HTTP request on the Pi can use
this, for example Home Assistant or Homebridge with an HTTP-switch plugin.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `frame not found over Bluetooth` | Close the SwitchBot app on your phone. Move the Pi closer. After a Wi-Fi download the frame can be invisible over Bluetooth for a few minutes; the program retries by itself. |
| `info` returns nothing / commands rejected | Wrong `key_id`/`encryption_key`. Run `setup_frame.py key` again. |
| `Incorrect username or password` when fetching the key | Check that your account has a password (Apple/Google sign-in, see step 3). Log out and in again in the app. SwitchBot may block logins briefly after several failed attempts. |
| `image count limit reached: 10` | The album is full. Delete pictures in the SwitchBot app; keep at most 9 of your own. |
| `upload did not appear in the album list` | The app API changed or the login failed. Run `setup_frame.py album` to test. |
| Poster disappears by itself while you're still watching | The frame is in slideshow mode. See step 5. |
| Poster stays after you stop watching | Wait about a minute. A long-lived Apple TV connection sometimes keeps reporting "playing" after a stop, so the program reconnects every `reconnect_every` seconds. |
| No poster for a certain app | Check the log for `app … is not in the streaming list` and add its bundle ID to `services`. |
| Wrong poster (e.g. a different film with the same title) | TMDB search is title-based. Episodes are matched as TV series, and anything of 70 minutes or more as a film. Try `tmdb_language`. |
| No PIN on the TV while pairing | See step 8. |
| Pi Zero freezes during install | Something tried to compile. Reboot and follow step 2 exactly, one command at a time. |

## Known limitations

- **Slow.** 1.5–3 minutes until the poster appears; removing it takes about as long.
- **Battery.** Every poster costs a Wi-Fi session and an e-ink refresh. Use the USB cable if you watch a lot.
- **Undocumented API.** The album-list and delete endpoints are not public and may change.
- **Slideshow mode is not restored.** The program assumes the frame is set to a single picture.
- **Tested on one setup:** 7.3" frame, Pi Zero 2 W, one Apple TV, EU SwitchBot region.
  Automatic detection was confirmed for HBO Max. The other bundle IDs come from public sources;
  the app-name matching covers them as a fallback.
- Your SwitchBot password is stored in plain text in `config.json` (mode 600). That is needed to
  log in again when the session expires. Use a SwitchBot password you don't use anywhere else.

## Protocol reference

For anyone building on this. All Bluetooth commands are sent through pySwitchbot's
`SwitchbotArtFrame._send_command(hex)`. A reply starting with `01` means "accepted".
`<idx>` is the album index as one byte in hex.

| Command | Meaning | Status |
|---|---|---|
| `570F7A00` | Turn on Wi-Fi / start an update | verified |
| `570F7A0101<idx>` | Download cloud image `<idx>` to the frame (send `570F7A00` right after) | verified |
| `570F7A0102<idx>` | Delete image `<idx>` from the frame | verified |
| `570F7A02<idx>` | Show image `<idx>` | verified |
| `570F7A0300<idx><unix time, 8 hex digits>` | Static mode on image `<idx>` | verified by hand, not used in the code |
| `570F7B02` | Read display mode | partly understood |
| `5702` | Basic info (battery, firmware, image indexes), via `get_basic_info()` | verified |

Cloud endpoints:

| Endpoint | Auth | Body |
|---|---|---|
| `POST https://api.switch-bot.com/v1.1/devices/{deviceId}/commands` (official) | token + HMAC-SHA256 signature | `{"command":"uploadImage","parameter":{"imageBase64":"data:image/jpeg;base64,…"},"commandType":"command"}` |
| `POST https://wonderlabs.{region}.api.switchbot.net/device/artframe/api/v1/images/list` (undocumented) | header `authorization: <access_token>` from the account login | `{"deviceID":"…"}` → `data.images[].imageIndex` |
| `POST https://wonderlabs.{region}.api.switchbot.net/device/artframe/api/v1/images/delete` (undocumented) | same | `{"deviceID":"…","groupID":"","imageIndexList":[idx],"deleteAll":false}` |

Album indexes are not sequential (e.g. 4, 21, 143, 212), so the new index is found by comparing
the list before and after an upload.

## Credits and sources

This project stands on the work of others. Thank you!

**Libraries**
- [**pySwitchbot**](https://github.com/sblibs/pySwitchbot) (sblibs and contributors). Bluetooth
  communication with the Art Frame, the encryption, fetching the encryption key, and the
  SwitchBot account login. This is the library behind Home Assistant's SwitchBot integration.
- [**pyatv**](https://github.com/postlund/pyatv) by Pierre Ståhl and contributors. Everything
  Apple TV: discovery, pairing and now-playing info.
- [**Bleak**](https://github.com/hbldh/bleak). Bluetooth Low Energy on Linux.
- [**Pillow**](https://python-pillow.org/). Image processing.
- [**aiohttp**](https://github.com/aio-libs/aiohttp). HTTP client and the local on/off API.
- [**srptools**](https://github.com/idlesign/srptools). Used by pyatv for pairing.

**Research and documentation**
- [**Inside the SwitchBot AI Art Frame**](https://blog.iodev.org/blog/inside-the-switchbot-ai-art-frame/)
  and its [**follow-up**](https://blog.iodev.org/blog/switchbot-ai-art-frame-rbl-follow-up/) on
  blog.iodev.org. Teardown and Bluetooth protocol analysis of the frame. The wake command
  `570F7A00`, the image-operation command family `570F7A01…` and the pointer to
  `woArtFrame_service.js` in the SwitchBot app come from here. This project would not exist
  without it.
- [**SwitchBot API documentation**](https://github.com/OpenWonderLabs/SwitchBotAPI) and its
  [AI Art Frame page](https://github.com/OpenWonderLabs/SwitchBotAPI/blob/main/devices/others/ai-art-frame.md).
  `uploadImage` and request signing.
- SwitchBotAPI issues [#489](https://github.com/OpenWonderLabs/SwitchBotAPI/issues/489) and
  [#461](https://github.com/OpenWonderLabs/SwitchBotAPI/issues/461). They confirm that API uploads
  don't reach the frame without a Hub and that the frame holds at most 10 images.
- [SwitchBot support: Do I need a Hub to remotely change the images?](https://support.switch-bot.com/hc/en-us/articles/36590466144023-Do-I-Need-a-Hub-to-Remotely-Change-the-Images-on-SwitchBot-Al-Art-Frame).
  The official position this project works around.
- [SwitchBot support: AI Art Frame installation guide (IT)](https://support.switch-bot.com/hc/it/articles/37114340123671-Guida-all-Installazione-di-SwitchBot-AI-Art-Frame-con-Alexa).
  Minimum app version for the frame.
- [Home Assistant SwitchBot (Bluetooth) integration](https://www.home-assistant.io/integrations/switchbot/)
  and [`switchbot_cloud.upload_art_frame_image`](https://www.home-assistant.io/actions/switchbot_cloud.upload_art_frame_image/).
  Showed that local Bluetooth control of the frame is possible and how the encryption key is obtained.
- [pySwitchbot README](https://github.com/sblibs/pySwitchbot/). The note that accounts using
  Apple/Google sign-in need a password to fetch the encryption key.
- [laravel-switchbot-frame](https://packagist.org/packages/spaanproductions/laravel-switchbot-frame).
  Background on the Hub requirement of the cloud API.
- [Emby forum: Apple TV image resolution](https://emby.media/community/topic/143725-apple-tv-image-resolution).
  Background on why Apple TV artwork is often low resolution.
- [Notebookcheck hands-on review](https://www.notebookcheck.net/SwitchBot-AI-art-frame-hands-on-review-Bringing-the-world-of-art-home.1231417.0.html).
  Hardware background (Wi-Fi + Bluetooth, battery).

**Finding the album API**

The `images/list` and `images/delete` endpoints were found by inspecting the official
**SwitchBot Android app** (version 10.6). Its Art Frame screens are a separately downloaded
React Native plugin (`ArtFrame` v1.0.18.0), and the endpoint paths are in that plugin's
JavaScript bundle. Tools used: [jadx](https://github.com/skylot/jadx) (decompiler) and
[androguard](https://github.com/androguard/androguard). The app itself was downloaded from
[APKPure](https://apkpure.com/switchbot/com.theswitchbot.switchbot). You do **not** need to
repeat any of this to use the project.

**Data**
- Posters and metadata come from [**TMDB**](https://www.themoviedb.org/).
  This product uses the TMDB API but is not endorsed or certified by TMDB.

**Trademarks.** SwitchBot, Apple TV, Netflix, Disney+, HBO Max, Prime Video, Pathé Thuis, SkyShowtime and Crunchyroll are trademarks
of their respective owners. They are mentioned only to describe compatibility.

**Built with** help from [Claude](https://claude.ai) (Anthropic), which did the research, wrote
the code and this README, and debugged it on a real Pi Zero 2 W together with the author.

## License

[MIT](LICENSE). The libraries listed above have their own licenses.
