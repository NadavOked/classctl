#!/bin/bash
# ClassCtl - install the agent as a systemd service. Run with sudo.
set -e
INSTALL_DIR="${1:-/opt/classctl}"

if [ "$EUID" -ne 0 ]; then echo "run with sudo"; exit 1; fi
if [ ! -f "$INSTALL_DIR/agent.json" ]; then
  echo "not found $INSTALL_DIR/agent.json — run python3 setup_wizard.py first"; exit 1
fi

install -m 644 classctl-agent.service /etc/systemd/system/classctl-agent.service
systemctl daemon-reload
systemctl enable --now classctl-agent.service
echo "[OK] agent running. Status: systemctl status classctl-agent"
