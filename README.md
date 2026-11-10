# 🔭 tv-webb-screensaver

Turn an **Android TV** screensaver into a window to the universe: **NASA's Astronomy Picture of the Day (APOD)** plus images from the **James Webb** and **Hubble** space telescopes, refreshed automatically every day.

Instead of the same old photographer shots (Google's *Backdrop*), the TV shows:

- **NASA APOD**: today's picture comes up **often**, with its explanation (optionally translated and summarized) in a band below the image that never covers it.
- **ESA/Webb** (*Picture of the Month*) and **ESA/Hubble** (*Picture of the Week*), plus nebulae, galaxies and stars: a **random selection that changes every day**, each with a one-or-two-line note on what the telescope is looking at and why it's interesting.
- **Optional art mode:** famous public-domain paintings (Van Gogh, Vermeer, Hokusai, Velázquez…), with a **painting of the day**.
- **Optional animals mode:** Wikimedia Commons *featured pictures* of animals, named in your language, with an **animal of the day**.
- The TV can **take turns** between space, art and animals (e.g. 20 minutes each), or follow hour ranges.

Also:

- **No distortion:** every image is fit to the TV's exact resolution, and the borders are filled with a blurred copy of the same image.
- **Captions:** title, date, constellation and distance (paintings: title, artist, year and museum; animals: name, scientific name, photographer and license), in English or Spanish.
- **Real photos only:** charts, spectra, and versions with compasses or labels are discarded.

```
┌──────────────────────┐    daily cron     ┌───────────────────┐
│ NASA APOD · ESA/Webb │ ───────────────▶  │  Home server      │
│ ESA/Hubble           │   webb_sync.py    │  /srv/webb (pool) │
│ Wikidata · Commons   │   + hourly switch │  (Samba share)    │
└──────────────────────┘                   │                   │
                                           └─────────┬─────────┘
                                                     │ SMB
                                           ┌─────────▼─────────┐
                                           │  Android TV       │
                                           │  Aerial Views     │
                                           │  (screensaver)    │
                                           └───────────────────┘
```

Tested on a **Skyworth Android TV** with an Ubuntu mini PC as the server.

---

## Requirements

- An **Android TV / Google TV** (Skyworth, Sony, TCL, Chromecast, Nvidia Shield, etc.)
- A **Linux server** on the same network that is always on (mini PC, Raspberry Pi, NAS…)
- The **[Aerial Views](https://github.com/theothernt/AerialViews)** app (free, open source, on the Play Store)
- `adb` on the server (or on any computer on the network)

## Layout

```
scripts/
├── webb_sync.py        # fetches APOD/Webb/Hubble/paintings, filters, fits, adds text, builds the daily pools
├── setup-samba.sh      # creates the shared folder for the TV
└── tv-screensaver.sh   # connects over adb and switches the screensaver
art/
├── paintings.txt       # the painting list for art mode: "Title | Artist" per line (edit freely)
└── paintings.lock.json # each line resolved to a Wikidata item + Commons file (generated, committed)
.env.example            # copy to .env for your local settings (git-ignored)
```

---

## Setup

### 1. Find the TV's IP address

From the server:

```bash
ip a                              # see your network range, e.g. 192.168.1.0/24
sudo nmap -sn 192.168.1.0/24
```

Look for the TV maker in the list. Heads-up: **Skyworth shows up as `Shenzhen Chuangwei-RGB Electronics`** (Chuangwei is its Chinese name).

You can also find it on the TV under **Settings → Network & Internet**.

> 💡 Give the TV a **fixed IP** in your router (DHCP reservation). If its IP changes, the adb connection breaks.

### 2. Enable adb on the TV

1. **Settings → Device Preferences → About** → press **Build** 7 times.
2. **Device Preferences → Developer options** → turn on **USB debugging** (on many TVs this also enables network adb on port 5555).
3. On the server:

```bash
sudo apt install -y adb
export TV=192.168.1.65            # your TV's IP
./scripts/tv-screensaver.sh connect
```

A prompt appears on the TV: check **"Always allow from this computer"** and accept. `adb devices` must show `device`, not `unauthorized`.

### 3. Create the shared folder

```bash
./scripts/setup-samba.sh
```

It asks for a Samba password: that's the one you'll enter on the TV. By default it shares `/srv/webb` as `webb`, with your user.

### 4. Fetch and prepare the images

```bash
sudo apt install -y python3-pil fonts-dejavu-core
adb shell wm size                       # the TV's real resolution, e.g. 1920x1080
cp .env.example .env                    # and set WEBB_SIZE to what wm size returned
./scripts/webb_sync.py
```

The first run takes a while: it reads every image page and processes them all. After that it only handles new ones.

#### What the TV shows (the daily *pool*)

The shared folder does **not** hold everything. On every run it is rebuilt with:

| What | How many | Variable |
|---|---|---|
| Latest APOD, **repeated** so it shows up often | 8 copies | `POOL_APOD_WEIGHT` |
| APODs from previous days | 6 | `POOL_APOD_RECENT` |
| **Random** Webb + Hubble (changes daily) | 40 | `POOL_RANDOM` |

With the defaults, roughly **1 in 7 images is today's APOD**. The copies are *hardlinks*, so they take no extra space. The full library lives in `~/.cache/webb/rendered`.

> Turn on **shuffle** in Aerial Views: otherwise all APOD copies play back to back.

#### Art mode: famous paintings

Besides space, the script can build a second pool with **famous public-domain paintings**, and the TV folder shows one pool or the other depending on the hour.

- **The list** is [`art/paintings.txt`](art/paintings.txt), one painting per line: `Title | Artist`, with the English title. It ships with ~110 well-known works, all by artists who died long ago (the youngest died in 1944), so they're in the public domain. Add or remove lines freely.
- **Resolution:** each line is looked up on **Wikidata**. It must be a painting (or a print, so Hokusai qualifies) whose creator matches the artist word by word (`Van Gogh` matches `Vincent van Gogh`). The result (Wikidata item + Wikimedia Commons file) is saved to `art/paintings.lock.json`, so a painting never changes on its own. Commit that file.
- **Ambiguous titles** (several versions of *Sunflowers*, *The Scream*…) are **skipped and reported**, never guessed. Pin the version you want with a third field: `The Scream | Edvard Munch | Q18891156`. If one match is clearly the best known (5× the Wikipedia articles of the next), it's taken and the output says so.
- **Download:** from Wikimedia Commons at `ART_FETCH_WIDTH` (1920 px by default), with an identifiable User-Agent and pauses between requests.
- **Caption:** the painting is shown whole, with the text in a band **below** it (never on top): title, then `artist · year · museum`. Title, artist name and museum come from Wikidata in `WEBB_LANG` (*La noche estrellada · Vincent van Gogh · 1889 · Museo de Arte Moderno*).
- **Painting of the day:** one painting per day, chosen deterministically, with **no repeats until the whole list has been shown**. Like the APOD, it changes when the daily run happens.

The art pool, rebuilt every day:

| What | How many | Variable |
|---|---|---|
| Painting of the day, **repeated** | 6 copies | `POOL_ART_WEIGHT` |
| **Random** paintings (changes daily) | 30 | `POOL_ART_RANDOM` |

#### Animals mode: featured wildlife photos

A third pool with animal photos from **[Wikimedia Commons featured pictures](https://commons.wikimedia.org/wiki/Category:Featured_pictures_of_animals)**: about 5,000 photos chosen by community vote as the best on Commons, under free licenses (CC BY-SA, CC0, public domain), and most of them linked to their species on Wikidata.

- **Which photos:** every day 12 new ones are drawn at random (`ANIMALS_PER_DAY`; 48 on the first run) and the library keeps the latest 240 (`ANIMALS_MAX`), so it keeps changing. Only landscape JPEGs at least 1920 px wide, **linked to an animal taxon on Wikidata** (no species, no photo: names are never guessed from file names). Museum specimens, skeletons and illustrations are left out, and so is anything whose taxonomy doesn't lead to the animal kingdom (a flower tagged on the photo, say).
- **Name:** the species' common name in `WEBB_LANG` from Wikidata (*Búho real*, *Elefante africano*). Many insects and rare species have none in Spanish: then the nearest well-known group up the taxonomy names it (*Mariposa o polilla*, *Ciervo*, *Libélula*; the list is `ANIMAL_GROUPS` in the script).
- **Caption:** the name, then `scientific name · Photo: author · license`. The CC BY-SA license requires crediting the author, so it's always shown.
- **Animal of the day:** like the painting of the day, repeated in the pool (`POOL_ANIMAL_WEIGHT`, 6) next to a random daily sample (`POOL_ANIMAL_RANDOM`, 30).

#### Which pool the TV shows

`SCHEDULE` decides:

| `SCHEDULE` | Meaning |
|---|---|
| `space` (default) | Only space, as before. Nothing else is downloaded. |
| `art` / `animals` | Only that pool. |
| `space,art,animals` | **Take turns**, `SCHEDULE_MINUTES` each (default 60), counting from midnight. With 20: space at :00, art at :20, animals at :40, every hour. |
| `alternate` | Same as `space,art`. |
| `space:8-19,art:20-7` | Hour ranges (inclusive, may wrap past midnight). Every hour must be covered exactly once. |

All pools are kept in `~/.cache/webb/pools/` (hardlinks). Switching is a separate, instant step with no network: `webb_sync.py --pool-only`, run from cron at every turn (see below). To try a schedule without waiting: `./scripts/webb_sync.py --pool-only --schedule space,art,animals --schedule-minutes 20 --time 21:45`.

**Fixed file names.** The TV folder always holds `slot-01.jpg` … `slot-48.jpg` (`TV_SLOTS`), and a switch only changes which picture each name points to. Aerial Views reads the folder list once, when the screensaver starts, then loops over it and reads every file again each time it's shown (it keeps no image cache). With fixed names, a TV that is already running the screensaver picks up the new pool on the next picture, instead of stumbling on files that no longer exist.

#### Configuration

Everything is set through environment variables, a `.env` file at the repo root, or the equivalent flag (see `--help`). Real environment variables win over `.env`; inside `.env`, if a variable appears twice, the **last** line wins.

| Variable | Values | Default |
|---|---|---|
| `WEBB_SOURCES` | any combination of `webb,hubble,apod` (the space pool) | all three |
| `WEBB_DEST` | folder the TV reads | `/srv/webb` |
| `WEBB_CACHE` | originals, library and state | `~/.cache/webb` |
| `WEBB_SIZE` | TV resolution | `1920x1080` |
| `WEBB_LANG` | `en` / `es` | `en` |
| `WEBB_CAPTION` | `1` with text / `0` image only | `1` |
| `WEBB_TRANSLATE` | `none` / `claude` / `ollama` / `argos` | `none` |
| `WEBB_FONT` | path to a `.ttf` | DejaVu Sans |
| `WEBB_TEXT_WIDTH` | share of the screen width used by text, centered | `0.60` |
| `OLLAMA_URL` / `OLLAMA_MODEL` | local Ollama server and model, for `WEBB_TRANSLATE=ollama` | `http://127.0.0.1:11434` / `llama3.1:8b` |
| `LLM_MAX_CALLS` | model requests per run; the rest wait for later runs | `20` with `ollama`, no limit with `claude` |
| `OLLAMA_BUSY_UNITS` | systemd units (comma-separated); while one is active, the run doesn't use Ollama | none |
| `APOD_API_KEY` | free key from [api.nasa.gov](https://api.nasa.gov) | `DEMO_KEY` |
| `APOD_DAYS` | how many days of APOD to fetch per run | `14` |
| `APOD_TEXT` | `summary` / `full` / `none` | `summary` |
| `SCHEDULE` | `space` / `art` / `animals` / `space,art,animals` / `alternate` / `space:8-19,art:20-7` | `space` |
| `ART_LIST` | path to the painting list | `art/paintings.txt` |
| `POOL_ART_WEIGHT` | copies of the painting of the day | `6` |
| `POOL_ART_RANDOM` | random paintings in the art pool | `30` |
| `ART_FETCH_WIDTH` | width to download paintings and animal photos at, in px | `1920` |
| `SCHEDULE_MINUTES` | minutes per turn when pools take turns | `60` |
| `TV_SLOTS` | pictures in the TV folder (`slot-01.jpg`…) | `48` |
| `ANIMALS_PER_DAY` | new animal photos per day | `12` |
| `ANIMALS_MAX` | animal photos kept in the library (oldest go first) | `240` |
| `POOL_ANIMAL_WEIGHT` | copies of the animal of the day | `6` |
| `POOL_ANIMAL_RANDOM` | random animal photos in the pool | `30` |
| `ANIMAL_CATEGORY` | Commons category to draw from (e.g. `Featured pictures of birds`) | `Featured pictures of animals` |

`DEMO_KEY` is enough for one run a day, but getting your own key is better (free, arrives instantly by email).

#### Text, language and translation

- **Webb and Hubble** get a short caption **centered** at the bottom: title, a one-or-two-line **description** (what the telescope is pointed at and why it's interesting: what astronomers want to see, a finding or a curiosity), and date, constellation, distance and credit. With `WEBB_TRANSLATE=claude` or `ollama`, the model writes it from ESA's press text. Without it (or if the API key fails) it's built from ESA's own data, in the chosen language: what the object is, where it is and how long its light took to reach us, e.g. *Nebulosa M16, dentro de la Vía Láctea. La luz que vemos salió de ahí hace unos 6.500 años.* (the distance then drops out of the data line, since it's already said). Constellation names are translated too (*Ursa Major* → *Osa Mayor*).
- **APOD** shows the whole image on top and a **centered band** below with title, date, credit and the explanation: the text never covers the image. The font size adjusts itself so everything fits.
- All text stays in the **middle 60%** of the screen (`WEBB_TEXT_WIDTH`), so it doesn't collide with the clock and other overlays Aerial Views draws near the edges.
- Dates and distances are written in the chosen language with no external dependencies. Titles and explanations come in English from the sources; to translate or condense them:
  - `WEBB_TRANSLATE=claude` + `ANTHROPIC_API_KEY=...` (optional `CLAUDE_MODEL`): translates when `WEBB_LANG=es`, and **condenses the APOD explanation to 2–3 sentences** (in any language), which is what reads comfortably from the couch. Each text is processed only once and cached; it costs cents per month. **Recommended.**
  - `WEBB_TRANSLATE=ollama`: the same as `claude` (translation, APOD summary and the Webb/Hubble description) with a **local model** through [Ollama](https://ollama.com): free and private, but slow on a CPU. Pick the model with `OLLAMA_MODEL`. If the server also runs other work, use a model that is already loaded (so nothing gets evicted from RAM), cap the requests per run with `LLM_MAX_CALLS` (the backlog is worked off over several days; today's APOD goes first), and list in `OLLAMA_BUSY_UNITS` the systemd units during which the script must leave Ollama alone.
  - `WEBB_TRANSLATE=argos`: offline with [Argos Translate](https://github.com/argosopentech/argos-translate) (`pip install argostranslate` + the en→es package). Translates but doesn't summarize, so the APOD text stays long.
  - Without it, APOD shows the full explanation with a smaller font.
  - If translation fails (a wrong `ANTHROPIC_API_KEY`, Ollama down, the per-run cap reached), the run says so once and the images are shown untranslated (Webb/Hubble with the description built from ESA's data). They're retried on later runs, and re-rendered once their text is translated.

If you change language, resolution or text settings, the script **re-renders everything from the cache** without downloading again.

#### Inspect an image

```bash
./scripts/webb_sync.py --debug potm2610a     # Webb metadata and whether it's accepted
```

#### Run it daily

APOD is published once a day, so the heavy run is **daily** (8:31 AM here): it downloads, renders and rebuilds the pools. If `SCHEDULE` mixes pools, add an entry that only switches the TV folder (instant, no network) at every turn: `*/20` for 20-minute turns, `0` (hourly) for hour ranges. Keys and language live in `.env`:

```bash
crontab -l 2>/dev/null | grep -v webb_sync | crontab -
(crontab -l 2>/dev/null
 echo "31 8 * * * $PWD/scripts/webb_sync.py >/dev/null 2>&1"
 echo "*/20 * * * * $PWD/scripts/webb_sync.py --pool-only >/dev/null 2>&1") | crontab -
```

The two never step on each other: they share a lock around the pool folders.

The run history is in `/srv/webb/.sync.log`.

### 5. Configure Aerial Views on the TV

1. Install **Aerial Views** from the Play Store.
2. In the app, turn off the Apple videos and the other providers.
3. Enable the **Samba / SMB** source with:
   - Host: the server's IP (e.g. `192.168.1.72`)
   - Share: `webb`
   - Samba user and password
4. Make sure it includes **photos**, not just videos, and turn on **shuffle**.
5. If there's a photo scaling option, pick **fit** (not crop or stretch): the images already come at the exact size.

### 6. Set it as the screensaver

Android TV hides the option to pick another screensaver, so it's done over adb:

```bash
./scripts/tv-screensaver.sh status    # 👈 write down what it returns (your original)
./scripts/tv-screensaver.sh aerial
./scripts/tv-screensaver.sh test      # starts it right away to check
```

If a nebula shows up: done 🌌

---

## Going back to the original screensaver

```bash
./scripts/tv-screensaver.sh restore
```

The original value **varies by TV**. On the Skyworth it's `com.google.android.backdrop/.Backdrop`; on others it's `com.google.android.apps.tv.dreamx/.service.Backdrop`. Use whatever `status` returned before you changed anything:

```bash
BACKDROP=<what-you-wrote-down> ./scripts/tv-screensaver.sh restore
```

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Connection refused` | adb isn't listening. Turn on **USB debugging** and restart the TV. If **Wireless debugging** exists, use `adb pair` with the code the TV shows, then `adb tcpip 5555`. |
| `failed to authenticate` / `unauthorized` | The prompt on the TV hasn't been accepted. Press Home (so the screensaver isn't running) and reconnect. |
| The authorization prompt **doesn't show up** | On the TV: *Revoke USB debugging authorizations* + toggle USB debugging off/on. On the server: `adb kill-server && rm -f ~/.android/adbkey*` and reconnect. If it still fails, unplug the TV for 10 s. |
| Authorization keeps getting lost | "Always allow" wasn't checked, or you ran `sudo adb` (root uses a different key). Always run adb without sudo. |
| Distorted images | `WEBB_SIZE` doesn't match the TV: check `adb shell wm size`. Also check the photo scaling in Aerial Views. |
| APOD hardly ever shows up | Turn on shuffle in Aerial Views or raise `POOL_APOD_WEIGHT`. |
| A source brings nothing | Check the output or `.sync.log`: if a category page moved, adjust it in `ESO_SITES`. |
| An image with text slipped through | Run `--debug <id>` to see its type and title. If it makes sense, add the word to `TEXT_HINTS` in `webb_sync.py`. |
| Text overlaps the clock or other Aerial Views overlays | Lower `WEBB_TEXT_WIDTH` (e.g. `0.5`), or move those overlays in the Aerial Views settings. |
| Everything is in English although `.env` says `WEBB_LANG=es` | Before this fix, if `.env` had the same variable twice (e.g. the `.env.example` block plus your own lines), the **first** one won. Now the last one does; still, keep each variable once. Check with `grep -n WEBB_LANG .env`. |
| Titles and APOD text stay in English with `WEBB_LANG=es` | Translation needs `WEBB_TRANSLATE=claude` and a valid `ANTHROPIC_API_KEY`. The run prints `translation disabled for this run … 401` if the key is wrong. Paintings don't need it (their Spanish names come from Wikidata). |
| `art: skipped … ambiguous` | Several Wikidata items fit. Pick the right one (the message lists them) and pin it: `Title | Artist | Q…`. |
| `art: skipped … no Wikidata item found` | Try the title as English Wikipedia writes it, or check the artist's spelling on Wikidata (e.g. *Brueghel*). Or pin the item. |
| The TV doesn't switch pools | Check the `--pool-only` cron (`crontab -l`) runs at every turn, and `/srv/webb/.sync.log` (it logs every switch). |
| After updating from a version without fixed names, the screensaver stalls or skips | Once: it still holds the old file list. Exit the screensaver and start it again. |
| An animal shows a broad name (*Mariposa o polilla*) | That species has no common name in your language on Wikidata; you can add one there (label or *taxon common name*). |
| Black screen in Aerial Views | SMB support is picky about paths: use a simple share, no subfolders, with user and password. |

---

## How `webb_sync.py` works

1. **APOD:** queries `api.nasa.gov/planetary/apod` for the last `APOD_DAYS` days. Skips videos and images narrower than 1000 px.
2. **Webb and Hubble:** both run on the same ESA platform. It reads *Picture of the Month* / *Picture of the Week* and some categories (set in `ESO_SITES`), and for each new image reads the title, **Type**, date, constellation and distance. Only `Observation` images whose title doesn't suggest text on top (*annotated*, *compass*, *spectrum*, *illustration*…) are kept.
3. Downloads the original (Webb/Hubble: `wallpaper_uhd` in 4K or `large`; APOD: `hdurl`) to `~/.cache/webb/raw`.
4. Renders the TV version to `~/.cache/webb/rendered`: whole image without distortion, blurred background, and the centered caption or text band.
5. **Animals** (if `SCHEDULE` uses it): draws random featured pictures from Commons, keeps the ones linked to an animal taxon, names them from Wikidata and downloads them.
6. **Art** (if `SCHEDULE` uses it): resolves new lines of `art/paintings.txt` on Wikidata into the lock file, reads title/artist/year/museum in `WEBB_LANG`, and downloads the painting from Wikimedia Commons.
7. Rebuilds the daily pools in `~/.cache/webb/pools/` and mirrors the one for the current turn into the TV folder, under fixed names. Files are written atomically, so the TV never sees a half-written one. `--pool-only` does just this last mirroring step.

---

## Next step: artificial life on the TV 🧬

An Android screensaver is just a `DreamService`. The next idea is a minimal Kotlin app with a `WebView` pointing to a simulation served from the home server (e.g. `http://gilbert.local:8080`), alternating with the space images by time of day: the universe during the day, artificial-life dots at night.

---

## Credits

- Paintings: public-domain reproductions from **[Wikimedia Commons](https://commons.wikimedia.org)**, metadata from **[Wikidata](https://www.wikidata.org)** (CC0).
- Animals: Wikimedia Commons featured pictures by their authors, each under its own free license (shown on screen).
- Images: **ESA/Webb** and **ESA/Hubble** ([usage terms](https://esawebb.org/copyright/)), and **NASA APOD** (each picture has its own author and many carry their own copyright; the credit is shown under each picture). This project is meant for personal use at home: check the rights of each image before redistributing.
- Screensaver: [Aerial Views](https://github.com/theothernt/AerialViews) by Neil Turner.

## License

The scripts in this repo are released under the [MIT](LICENSE) license.
