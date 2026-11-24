# Rig for the UniXNU / DroidVM guest

Host-side debugging of the iPadOS guest running in crosvm on Gunyah. This directory is the
UniXNU-droidvm `tools/rig` (the author's own tooling) plus the helpers this session needed:

| file | what |
| --- | --- |
| `dc.py` | the DroidVM daemon's IPC client: 4-byte LE length + JSON, trailing NUL, token auth |
| `vm.py` | `list` / `status` / `stop` / `start` / `set KEY=VALUE` / `extra --list\|add\|remove`, all over the revisioned config API |
| `patch-kernel.py` | patch one instruction of `kernel.macho` by kernel VA (`--ret`, `--bytes`, `--show`); maps the VA through the Mach-O segments |
| `peek.py`, `procs.py`, `panic.py`, `dmesg.py`, `sym.py`, `export.py`, ... | the author's tools, unchanged |

## Setup

```sh
# 1. adb: on this box the real adb is the Windows one (it speaks on port 5038)
printf '#!/bin/bash\nexec "/mnt/d/.../adb.exe" -P 5038 "$@"\n' > bin/adb
chmod +x bin/adb
export PATH=$PWD/bin:$PATH

# 2. the phone's serial and how peek.py finds the VM's crosvm
cat rig.env          # PHONE=595c95a1, VM_MATCH=unixnu   (our VM is named "unixnu-ipados")

# 3. symbols: llvm-nm -n of the payload's kernel.macho -> out/kernel.nm
adb -s $PHONE shell su -c 'cp /storage/emulated/0/DroidVM/UniXNU/kernel.macho /data/local/tmp/k && chmod 644 /data/local/tmp/k'
adb -s $PHONE pull /data/local/tmp/k out/kernel.macho
llvm-nm -n out/kernel.macho > out/kernel.nm
```

## The loop

```sh
export PATH=$PWD/bin:$PATH
python3 vm.py list
python3 vm.py start            # refreshes the config revision first
python3 vm.py set memory_mb=2048 hugepages=false
python3 procs.py               # process samples the droidvm-monitor worker writes
python3 panic.py               # panic? full panic log with symbolized frames
python3 peek.py va:0xfffffff00c01ca84 16
```

`vm_start_configured` needs the **current** revision from `vm_config_snapshot`; every edit
(`vm.py set`, the app's editor) changes it. `vm.py` always re-snapshots first.

## What this found (2026-11-24)

The guest does not "hang" after `load_init_program: attempting to load .../launchd.development`:
**the kernel panics while exec'ing launchd**:

```
panic(cpu 1): Illegal action: h1 ... h2 ... h3 ... @vm_map_store_rb.c:628
    [= _update_holes_on_entry_creation.cold.2+0x0]
_Assert
_update_holes_on_entry_creation.cold.2+0x0
_update_holes_on_entry_creation+0x8c
_vm_map_entry_insert+0x28c
_vm_map_enter+0xf88
_vm_map_exec+0x3e0
_exec_mach_imgact+0x54c
_exec_activate_image+0x494
_posix_spawn+0x1e04
_native_board_launch_policy+0x598
_load_init_program_at_path+0x50
_load_init_program+0x6b8
_bsdinit_task+0x68
```

`procs.py` confirms it: the samples contain only `kernel_task p0 n47 r6 w41`, `launchd` and
every later daemon are "absent from this process pass", and `droidvm_monitor_passes` stays 1
(the monitor worker never runs again).

Reproduced with 2048/3072/4096 MB, hugepages on/off, GPU (mmio/PCI) and simplefb, no input
devices, `owner-service-manager=1` or not, and `amfi_get_out_of_my_way=1 cs_enforcement_disable=1`.
Adding `vm_map_debug_fourk=1 vm_map_debug_apple_protect=1` added no output.

The panic is raised **directly** from `_update_holes_on_entry_creation.cold.1`
(`VA 0xfffffff00c01ca34`): at `0xfffffff00c01ca80` it does `bl _panic`, and the return address
`0xfffffff00c01ca84` is what the log symbolizes as `cold.2+0x0` - that is why patching
`cold.2` and `_Assert` changed nothing (`peek.py` confirmed both patches were live in the
running kernel).

```sh
python3 patch-kernel.py out/kernel.macho patch/kernel-cold1.macho --va 0xfffffff00c01ca34 --ret
mcopy -o -i droidvm-disk.img patch/kernel-cold1.macho ::/EFI/UniXNU/Payloads/kernel.macho
adb push droidvm-disk.img /data/local/tmp/ && adb shell su -c \
  'cp /data/local/tmp/droidvm-disk.img /data/media/0/DroidVM/UniXNU/'
```

That **removes the panic** (`panic.py` -> "no panic", `droidvm_monitor_passes` 1 -> 100,
kernel_task 47 -> 62 threads), but `launchd` still never becomes a process: the `vm_map_enter`
that the assertion was guarding fails, so the exec fails silently and the kernel parks.

So the remaining defect is in the guest kernel's 4 KiB VM-map hole bookkeeping
(`vm_map_store_rb.c` / `update_holes_on_entry_creation`), not in the host configuration. Two
ways forward: a kernel built with more diagnostics (KASAN, DEBUG) or a different payload build
from the author, or fixing the hole logic in source. The author builds with macOS 27 / Xcode
27.1: `SDKROOT=iphoneos ARCH_CONFIGS=ARM64 MACHINE_CONFIGS=BCM2837 KERNEL_CONFIGS=DEVELOPMENT
ARM64_LINK_ADDR=0xfffffff007004000 BUILD_LTO=0` (`./unixnu build kernel`).

## Pitfalls

* `/tmp` on this box is wiped between sessions; keep everything here.
* Filling the hugepage pool while the phone is low on memory ends in a **Qualcomm Apps
  watchdog bite** (pstore: `msm_watchdog ... Causing a QCOM Apps Watchdog bite!`). Run with
  `hugepages=false` while debugging; the VM still boots.
* `peek.py`/`procs.py`/`panic.py` find crosvm by `VM_MATCH` in its command line: ours is
  `--name unixnu-ipados`, hence `VM_MATCH=unixnu`.
* The rig reads guest memory through `/proc/<crosvm>/mem`; do not bulk-read the LENT pages.
