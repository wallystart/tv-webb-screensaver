#!/usr/bin/env bash
# setup-samba.sh — Creates a read-only shared folder for the TV.
#
# Usage:  ./setup-samba.sh              (share "webb" at /srv/webb, current user)
#         SHARE=webb DIR=/srv/webb SMB_USER=gilbert ./setup-samba.sh
set -euo pipefail

SHARE="${SHARE:-webb}"
DIR="${DIR:-/srv/webb}"
SMB_USER="${SMB_USER:-$USER}"

sudo apt install -y samba smbclient
sudo mkdir -p "$DIR"
sudo chown "$SMB_USER": "$DIR"

if ! grep -q "^\[$SHARE\]" /etc/samba/smb.conf; then
  sudo tee -a /etc/samba/smb.conf > /dev/null <<EOF

[$SHARE]
   path = $DIR
   read only = yes
   valid users = $SMB_USER
EOF
  echo "Added [$SHARE] to /etc/samba/smb.conf"
else
  echo "[$SHARE] already exists in smb.conf, leaving it alone"
fi

echo "Choose the Samba password for '$SMB_USER' (this is what goes on the TV):"
sudo smbpasswd -a "$SMB_USER"

sudo testparm -s 2>/dev/null | grep -A3 "\[$SHARE\]"
sudo systemctl restart smbd

if command -v ufw >/dev/null && sudo ufw status | grep -q active; then
  sudo ufw allow samba
fi

echo "✅ Samba ready. Test it with: smbclient //localhost/$SHARE -U $SMB_USER -c 'ls'"
