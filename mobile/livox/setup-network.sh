#!/bin/sh
# Dedicated wired sensor subnet; never changes Wi-Fi, Tailscale, Docker or the default route.
set -eu
if ! nmcli -t -f NAME connection show | grep -qx 'wavesense-livox'; then
 sudo nmcli connection add type ethernet ifname eth0 con-name wavesense-livox ipv4.method manual ipv4.addresses 192.168.1.50/24 ipv4.never-default yes ipv6.method disabled connection.autoconnect yes connection.autoconnect-priority 100
fi
sudo nmcli connection modify wavesense-livox ipv4.never-default yes
if [ "$(cat /sys/class/net/eth0/carrier)" != 1 ]; then
 echo 'Sensor network profile ready; waiting for Ethernet carrier (LiDAR power/cable).'
 exit 0
fi
sudo nmcli connection up wavesense-livox
