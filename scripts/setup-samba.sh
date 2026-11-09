#!/usr/bin/env bash
# setup-samba.sh — Crea una carpeta compartida de solo lectura para la tele.
#
# Uso:  ./setup-samba.sh              (share "webb" en /srv/webb, usuario actual)
#       SHARE=webb DIR=/srv/webb SMB_USER=gilbert ./setup-samba.sh
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
  echo "Agregado [$SHARE] a /etc/samba/smb.conf"
else
  echo "[$SHARE] ya existía en smb.conf, no lo toco"
fi

echo "Elegí la contraseña de Samba para '$SMB_USER' (es la que va en la tele):"
sudo smbpasswd -a "$SMB_USER"

sudo testparm -s 2>/dev/null | grep -A3 "\[$SHARE\]"
sudo systemctl restart smbd

if command -v ufw >/dev/null && sudo ufw status | grep -q active; then
  sudo ufw allow samba
fi

echo "✅ Samba listo. Probá con: smbclient //localhost/$SHARE -U $SMB_USER -c 'ls'"
