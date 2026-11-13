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

**Option A: from the zip you were sent.** Unzip it into your home folder.
You'll get a folder called `omniturn-cam`.

```bash
cd ~
unzip ~/Downloads/omniturn-cam-*.zip
cd ~/omniturn-cam
```

**Option B: from GitHub.** This way you can pull updates later with `git pull`.

```bash
cd ~
git clone https://github.com/legacybending-maker/omniture-CNC-program-maker omniturn-cam
cd ~/omniturn-cam
```

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

If you installed from GitHub (option B):

```bash
cd ~/omniturn-cam && git pull && .venv/bin/pip install -e ".[all]"
```

If you installed from a zip, unzip the new one over the old folder and run
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
