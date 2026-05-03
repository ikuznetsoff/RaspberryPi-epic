#!/bin/bash
# install.sh -- one-shot setup for RaspberryPi-epic on a fresh Pi OS Lite
# Trixie install (Pi Zero W + Hyperpixel 2.1 Round Touch).
#
# What this does:
#   1. Installs apt packages we need
#   2. Installs Pimoroni's Hyperpixel 2.1 Round driver
#   3. Replaces python3-rpi-lgpio shim with the real RPi.GPIO from PyPI
#      (the shim can't claim DPI-locked pins, so panel SPI init fails)
#   4. Patches /boot/firmware/config.txt for the Pimoroni legacy DPI path
#   5. Creates the project venv and installs Python deps
#   6. Installs an epic.service systemd unit and disables the conflicting
#      tty1 getty + old desktop-autostart entry
#
# Idempotent: safe to re-run.
#
# Run as the regular user (NOT root) -- script uses sudo where needed.
# Expects the repo cloned to ~/pi/code/epic.

set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/pi/code/epic}"
HP_DIR="$HOME/github/hyperpixel2r"
CONFIG_TXT="/boot/firmware/config.txt"
SERVICE_FILE="/etc/systemd/system/epic.service"

if [ "$EUID" -eq 0 ]; then
    echo "Run as a regular user (with sudo available), not as root." >&2
    exit 1
fi
if [ ! -d "$REPO_DIR" ]; then
    echo "Repo not found at $REPO_DIR -- clone it there first." >&2
    exit 1
fi
if [ ! -f "$CONFIG_TXT" ]; then
    echo "$CONFIG_TXT missing -- not running on Pi OS Bookworm/Trixie?" >&2
    exit 1
fi

echo "==> [1/6] Install apt packages"
sudo apt update
sudo apt install -y --no-install-recommends \
    git python3-venv python3-pip python3-dev gcc \
    fbset i2c-tools evtest

echo "==> [2/6] Install Hyperpixel 2.1 Round driver"
mkdir -p "$(dirname "$HP_DIR")"
if [ ! -d "$HP_DIR/.git" ]; then
    git clone https://github.com/pimoroni/hyperpixel2r "$HP_DIR"
else
    (cd "$HP_DIR" && git pull --ff-only) || true
fi
(cd "$HP_DIR" && sudo ./install.sh)

echo "==> [3/6] Replace RPi.GPIO shim with /dev/mem version"
# Pimoroni's panel SPI init script uses RPi.GPIO. On Bookworm/Trixie the
# default python3-rpi-lgpio shim refuses pins claimed by the DPI overlay
# ("GPIO not allocated"). The original RPi.GPIO from PyPI uses /dev/mem
# directly and bypasses that gate.
sudo apt remove -y python3-rpi-lgpio 2>/dev/null || true
sudo pip3 install --break-system-packages --force-reinstall RPi.GPIO

echo "==> [4/6] Patch $CONFIG_TXT"
# Disable any KMS overlays that fight the Pimoroni DPI path
sudo sed -i 's|^dtoverlay=vc4-kms-v3d$|#dtoverlay=vc4-kms-v3d|' "$CONFIG_TXT"
sudo sed -i 's|^dtoverlay=vc4-kms-dpi-hyperpixel2r$|#dtoverlay=vc4-kms-dpi-hyperpixel2r|' "$CONFIG_TXT"

# Append entries the Pimoroni legacy DPI path needs (idempotent)
add_if_missing() {
    if ! grep -qxF "$1" "$CONFIG_TXT"; then
        echo "$1" | sudo tee -a "$CONFIG_TXT" > /dev/null
        echo "    + $1"
    fi
}
add_if_missing "dtoverlay=hyperpixel2r"
add_if_missing "enable_dpi_lcd=1"
add_if_missing "dpi_group=2"
add_if_missing "dpi_mode=87"
add_if_missing "dpi_output_format=0x7f216"
add_if_missing "dpi_timings=480 0 10 16 55 480 0 15 60 15 0 0 0 60 0 19200000 6"
add_if_missing "disable_fw_kms_setup=1"
add_if_missing "dtparam=i2c_arm=on"
add_if_missing "framebuffer_width=480"
add_if_missing "framebuffer_height=480"

echo "==> [5/6] Set up project venv + Python deps"
cd "$REPO_DIR"
if [ ! -d venv ]; then
    python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

echo "==> [6/6] Install systemd service + disable autostart conflicts"
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=DSCOVR EPIC Image Viewer
After=hyperpixel2r-init.service network-online.target
Wants=network-online.target
Requires=hyperpixel2r-init.service
Conflicts=getty@tty1.service

[Service]
Type=simple
User=root
WorkingDirectory=$REPO_DIR
Environment=EPIC_FBDEV=/dev/fb0
Environment=EPIC_NO_TOUCH=1
ExecStart=$REPO_DIR/venv/bin/python -u $REPO_DIR/epic.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Stop the console from grabbing the framebuffer
sudo systemctl disable getty@tty1.service 2>/dev/null || true
sudo systemctl mask getty@tty1.service 2>/dev/null || true

# Stale desktop-session autostart (if a previous install put it there)
rm -f "$HOME/.config/autostart/epic.desktop"

sudo systemctl daemon-reload
sudo systemctl enable epic.service

echo
echo "Done. Reboot to bring everything up:"
echo "    sudo reboot"
echo
echo "After reboot:"
echo "    sudo systemctl status epic.service"
echo "    sudo journalctl -u epic.service -f"
echo "    sudo pkill -USR1 -f epic.py    # toggle weather overlay over SSH"
