#!/bin/bash
# Replace Payloads/kernel.macho on the phone's UniXNU boot disk and leave everything else alone:
# the device tree (kernel.macho.adt) is generated from the VM's FDT at boot and the trust cache
# covers the user space, so neither depends on the kernel image.
#
#   deploy-kernel.sh kernel.macho [--start]
#
# ADB comes from bin/adb in this directory (a shim for the Windows adb.exe); the disk path is the
# one the DroidVM daemon boots (storage/emulated/0/DroidVM/UniXNU/droidvm-disk.img).
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$PWD/bin:$PATH"
KERNEL=${1:?usage: deploy-kernel.sh kernel.macho [--start]}
START=${2:-}
DISK=/storage/emulated/0/DroidVM/UniXNU/droidvm-disk.img
PHONE=$(sed -n 's/^PHONE=//p' rig.env)

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

echo "== pull $DISK"
adb -s "$PHONE" pull "$DISK" "$WORK/disk.img"
echo "== payload before"
mdir -i "$WORK/disk.img" ::/EFI/UniXNU/Payloads
echo "== put $KERNEL"
mcopy -o -i "$WORK/disk.img" "$KERNEL" ::/EFI/UniXNU/Payloads/kernel.macho
echo "== payload after"
mdir -i "$WORK/disk.img" ::/EFI/UniXNU/Payloads
echo "== push"
adb -s "$PHONE" push "$WORK/disk.img" /data/local/tmp/droidvm-disk.img
adb -s "$PHONE" shell su -c "cp -f /data/local/tmp/droidvm-disk.img $DISK && chown media_rw:media_rw $DISK && echo deployed"

if [ "$START" = "--start" ]; then
	echo "== restart the VM"
	adb -s "$PHONE" shell su -c 'pidof crosvm | xargs -r kill' || true
	sleep 3
	python3 vm.py start
fi
