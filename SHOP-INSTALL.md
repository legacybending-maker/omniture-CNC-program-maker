# Installing omniturn-cam on the shop laptop

These steps assume a Linux laptop (Ubuntu, Debian, Mint or similar) with an
internet connection for the install. After it's installed, it runs without
internet.

## 1. One-time system packages

Open a terminal and run:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
python3 --version
```

The last line must print **Python 3.11** or newer.

## 2. Put the program in your home folder

Install a **release** (a tested, numbered version), not the latest
development code. The releases are listed at
https://github.com/legacybending-maker/omniture-CNC-program-maker/releases
with what changed in each one.

**Option A: from GitHub (recommended).** Updates are one command later.

```bash
cd ~
git clone https://github.com/legacybending-maker/omniture-CNC-program-maker omniturn-cam
cd ~/omniturn-cam
git checkout "$(git describe --tags --abbrev=0 origin/main)"   # the newest release
git describe --tags                                            # shows which one you're on
```

**Option B: from a release zip.** Download "Source code (zip)" from the
release page and unzip it into your home folder, renaming the folder to
`omniturn-cam`.

## 3. Install

```bash
cd ~/omniturn-cam
python3 -m venv .venv
.venv/bin/pip install -e ".[all]"
```

The STEP-file library is a large download (a few hundred MB), so this takes
a few minutes.

## 4. Start it

```bash
~/omniturn-cam/.venv/bin/omniturn-cam-gui
```

To try it on the manual's example part:

```bash
~/omniturn-cam/.venv/bin/omniturn-cam-gui ~/omniturn-cam/examples/manual_g75.toml
```

Optional desktop launcher:

```bash
cat > ~/.local/share/applications/omniturn-cam.desktop <<EOF
[Desktop Entry]
Name=OmniTurn CAM
Exec=$HOME/omniturn-cam/.venv/bin/omniturn-cam-gui
Type=Application
Terminal=false
Categories=Development;Engineering;
EOF
```

## 5. First-run settings

The first time it runs, it creates two files you should look over:

- `~/.config/omniturn-cam/machine.toml`: the machine name for the program
  header, max rpm (4000), collet capacity, and X/Z travel. **Check the travel
  numbers against the machine.**
- `~/.config/omniturn-cam/tools.toml`: the tool library. Edit it in the
  program with **F8**.

## 6. Getting a program to the machine

1. **F6 Program** → **Save to USB stick**. This writes `NAME.cnc` (8-character
   name, DOS line endings) to the stick.
2. On the OmniTurn, copy the file from the stick into `C:\PROGRAMS` as you
   normally would.
3. **Prove it out:** run it on the F4 Verify screen, then single-block it
   with the feed override turned down. This software is new and comes with
   no warranty.

## Updating

Read the release notes first, especially **G-code output**: they say which
programs come out different. Then:

```bash
cd ~/omniturn-cam
git fetch --tags
git checkout vX.Y.Z                      # the release you want
.venv/bin/pip install -e ".[all]"
```

Before cutting metal with a new version, re-post a job you've run before and
compare: `diff OLD.cnc NEW.cnc`. Anything different should be explained in the
release notes; prove it out again (F4 Verify, single block).

To go back to the version you had: `git checkout vOLD` and the `pip install`
line again.

If you installed from a zip, unzip the new release over the old folder and run
the `pip install` line again.

## If something goes wrong

- `omniturn-cam-gui: command not found`: use the full path shown in step 4.
- **"STEP import needs the OpenCascade bindings"**: run
  `~/omniturn-cam/.venv/bin/pip install cadquery-ocp`.
- **The window doesn't open** (on some minimal installs): run
  `sudo apt install -y libxcb-cursor0 libgl1 libegl1`.
- **The rules check (F5) shows a red error**: the message names the problem
  and the manual page. The program won't save code that breaks the
  OmniTurn's formatting rules.
