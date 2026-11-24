# Local build + boot notes (updated 2026-11-24)

Build of `crosvm` (virtio-mmio) from the DroidVM minimal manifest on WSL2, deployment of the
iPadOS payload to the phone, and the debugging rounds that followed. Everything here is local:
the tree lives in `/aosp` (ext4 on `D:\aosp.vhdx`), not in this repository. The vhdx is not
mounted by default after a WSL restart (`wsl --mount --vhd D:\aosp.vhdx --bare`, then mount the
whole-disk ext4 device on `/aosp`).

State: **the guest kernel boots.** Its XNU message buffer (read from guest RAM) shows APFS
mounts of `SystemCryptex` and `OwnerData` (the data store), `init`, and
`load_init_program: attempting to load /usr/appleinternal/sbin/launchd.development`. Nothing
after that: userspace does not progress, the display never gets a frame, and crosvm's CPU time
stays flat. That is the open problem.

## 1. Tree

```sh
repo init -u https://github.com/Droid-VM/crosvm-minimal-manifest -b droidvm -m crosvm-minimal.xml
repo sync -c -j4 --depth 1          # 96 projects
```

Git rewrites (USTC does not queue; TUNA did, at ~2 MB/min):

```sh
git config --global url."https://mirrors.ustc.edu.cn/aosp/".insteadOf "https://android.googlesource.com/"
git config --global url."https://gh-proxy.com/https://github.com/".insteadOf "https://github.com/"
```

Seven projects `repo sync` left behind, cloned by hand with `--depth 1`:
`toolchain/pgo-profiles`, `system/{server_configurable_flags,security,tools/aidl,tools/xsdc,unwinding}`,
`test/app_compat/csuite`.

Prebuilt notes: USTC ignores `--depth` on a re-fetch, so a retried sync re-downloads full
history (we saw `.repo` at 78 GB, 63 GB of it `tmp_pack_*` from aborted retries). For the two
biggest prebuilts a blobless sparse clone is better:

```sh
git clone --filter=blob:none --sparse --depth 1 -b android16-qpr2-release \
  https://mirrors.ustc.edu.cn/aosp/platform/prebuilts/clang/host/linux-x86 prebuilts/clang/host/linux-x86
git -C prebuilts/clang/host/linux-x86 sparse-checkout set --cone clang-r563880c soong
```

`soong` must be in the sparse set (it registers `llvm_prebuilt_*`), and the root `Android.bp`
must be checked out too, or soong reports "unrecognized module type" /
"missing dependencies: libclang_rt.builtins".

## 2. Patches the build needs

| file | why |
|---|---|
| `patches/boringssl-visibility-apex.patch` | `external/boringssl` must expose `libcrypto_static`/`libssl_static` to `//external/crosvm/third_party/libdatachannel` and list `com.android.virt` in `apex_available` |
| `patches/crosvm-media-off-and-shim.patch` | the fork's `media` feature wants `virtio-media` 0.0.7+ (with `devices::camera`, `renew_dmabuf_for`); the tree has 0.0.6, and even crates.io 0.0.7 has no `camera`, so the feature is dropped from the four crosvm Android.bp files |

`shim.bin` (14 648 bytes) is `droidvm-shim` built without the softfloat target (the prebuilt
Android Rust has no `aarch64-unknown-none-softfloat` std and no `rust-lld`); the local
`droidvm-shim/.cargo/config.toml` addition links with the clang prebuilt's `ld.lld` and disables
NEON. Verified 0 SIMD instructions and `_shim_end = 0x7950`. Copy it to
`external/crosvm/hypervisor/src/gunyah/shim.bin`.

## 3. Build

```sh
source build/envsetup.sh
lunch aosp_arm64 trunk_staging eng
GOGC=30 ALLOW_MISSING_DEPENDENCIES=true TARGET_BUILD_UNBUNDLED=true \
  m crosvm_device_only -j2
scripts/collect_crosvm.sh out/crosvm-pkg     # crosvm + 12 .so, 27 MB
```

`GOGC=30` keeps soong's single-process analysis under ~7.3 GB; without it the 7.6 GB WSL box
OOM-kills `soong_build`. A 16 GB swapfile at `/aosp/swapfile` helps.

## 4. Phone deployment

* `usr/bin/crosvm` <- our build, `crosvm.stock` kept next to it (ours has both mmio and pci, so
  the app's own VMs still start)
* `usr/lib/libvirglrenderer.so`, `libgfxstream_backend.so`, `libvncserver.so` <- ours; the app's
  copies are older and shadow `$ORIGIN`, which is how
  `cannot locate symbol "virgl_set_log_level"` happens
* **firmware**: `firmware/edk2-gunyah-20261113-run43452476331.fd` from this repo
  (sha256 `8855a78e…`). The APK's `VirtioGpuDxe` writes `QueueNum = 0` on virtio-mmio, crosvm
  answers `split queue size 0 is not a power of 2`, and EDK2 stops before BdsDxe - see the
  header of `.github/workflows/build-edk2.yml`. With this file the mmio GPU works.
* images: `/data/media/0/DroidVM/UniXNU/{os.img,systemos.img,droidvm-disk.img,edk2-virtio-gpu.fd}`
* data store: 16 GiB sparse + 4096-byte header `DroidVMDataStore\001\000\000\000`:

```sh
truncate -s 16G data-store.img
printf 'DroidVMDataStore\001\000\000\000' | dd of=data-store.img bs=4096 conv=notrunc
```

* kernel modules: `unixnu-droidvm-host` (gunyah_host_share, udmabuf, gh_unmovable, nproc_guard)
  and `gh-hugepage-reserve`. KernelSU disables both on install (a metamodule is present), so
  remove `disable` and keep `state=1`. Pool sizing that matters: `pool_want` pages of 2 MiB;
  a VM needs `mem_mb/2` pages, so 3072 MB -> 1536 pages; 5120 MB -> 2560. `pool_size_max` was
  4413 pages on this phone.

## 5. Boot command

`start-unixnu.sh` is the current command line. Notes that cost time:

* `--block …/droidvm-disk.img,lock=false,ro=true` - the key is `ro`, not `readonly`
* every disk on `/storage/emulated/0` needs `lock=false` (FUSE has no flock)
* `--hypervisor gunyah` is required by `--protected-vm-pseudo-unprotected`
* `--virtio-mmio-gpu` only works with the firmware from §4
* `--virtio-media`/`--virtio-mmio-media` are rejected (this build has the media feature off)
* `--virtio-mmio-edge-irq` makes the mmio devices edge-triggered; the help says Gunyah loses
  level irqfd interrupts to a vCPU in WFI
* VNC: `--vnc-server host=127.0.0.1,port=12700,screen=simplefb|gpu-0,transport-cap=cpu`,
  `adb forward tcp:12700 tcp:12700`, then any RFB client. The server sends the desktop name in
  ServerInit: read the 4-byte name length + name after the pixel format, or the stream desyncs.

Device layout crosvm assigns from the `--virtio-mmio-*` order (this matters when comparing
with the ADT the payload was built against):

```
a000000 block os.img        a001000 block systemos.img   a002000 block data-store.img
a003000 input (tablet)      a004000 input (keyboard)
a005000 gpu                 a006000 snd                  (a007000 media - not in this build)
```

GPU arguments the author's daemon generates (`DroidVM-Daemon/vm/crosvm_command.cpp`):

```
--gpu virglrenderer,context-types=drm,vulkan=false,egl=true,gles=true,udmabuf=true,pci-bar-size=4294967296
--pre-alloc drm-host-mb=8,media-host-mb=64      # board.toml: the drm pool is what /arm-io/virtio-gpu's drm-host-pool points at
--prepare-lend-mthp-mode single   --no-usb   --swiotlb 0 (pseudo-unprotected)
```

## 6. What the debugging rounds established

**The kernel boots.** Reading XNU's `msgbuf` out of guest RAM (see §7) showed:

```
apfs_log_op_with_proc: disk4s1 mounting volume DawnUpdate21H461...SystemCryptex
droidvm data store: 3145825-block window on a 4194304-block disk, read-write, flushed
apfs_log_op_with_proc: disk1s1 mounting volume OwnerData
load_init_program: attempting to load /usr/appleinternal/sbin/launchd.development
droidvm rtc: calendar set to ...
droidvm launchd overrides (watchdogd,identityservicesd,CommCenter,fairplayd.A2,accessoryupdaterd): 0
Couldn't build index for org.droidvm.ownervisor: Not eligible for acceleration
```

Then silence, no vCPU activity, no GPU frames. Variants tried, all ending the same way:

| variant | result |
|---|---|
| `--mem 5120` | idle |
| `--mem 3072` (droidvm#87 says 3072 works for Windows) | idle |
| GPU on mmio, gfxstream | with the old firmware: UEFI stalls; with the fixed firmware: boots, then idle |
| GPU on mmio, virglrenderer/drm | boots, then idle |
| GPU on PCI | boots, then idle (no mmio GPU node for the kernel) |
| no GPU at all | boots, then idle; screen turns black instead of the loader frame |
| author device order + snd | idle |
| `owner-service-manager=1` added to boot args | idle |
| boot args without `armcpu-diagnostics`/`droidvm-monitor` | idle |

So the GPU is not the blocker, and neither is the device set. The guest stalls in early
userspace, after launchd is loaded.

`ownervisor` (Droid-VM/ownervisor) is the guest's owner service manager; its README says the
kernel starts it at boot with `owner-service-manager=1`, and the CI seeds its binaries into the
Data store (`add-ownervisor-data.py`). The msgbuf line about its index may be benign.

## 7. Reading guest memory (tooling and pitfalls)

A DroidVM guest has **no kernel serial console**; the author's rig reads guest RAM instead.

| tool | what it does |
|---|---|
| `scripts/patch-early-probe.py` (device repo) | patches `kernel.macho`: the EL1 SP0/SP1 vectors branch to a catcher over `_pmap_coredump_test`, which records ESR/ELR/FAR/SPSR/x30/x29/SP at the first exception and parks the CPU. Needs `aarch64-linux-gnu-as/objcopy/nm`; on this box LLVM shims work (`llvm-mc -triple=aarch64 -filetype=obj`, `llvm-objcopy`, `llvm-nm`) |
| `tools/rig/memgrab.c` | reads a span of the crosvm `crosvm_guest` memfd coherently (`dc civac`); build with the NDK `aarch64-linux-android*-clang -static` |
| `tools/rig/peek.py` | host side: symbol/PA/VA -> memfd offset through `/proc/PID/maps`; `KERNEL_VA=0xFFFFFFF00427C000`, `KERNEL_PA=0x84A7C000` from DarwinBoot's hand-off line |
| `tools/rig/dmesg.py` | prints XNU's msgbuf through `peek` |
| `tools/rig/export.py` | guest file mailbox: `ls/tree/cat/get/ps/kill/mem/put/stackshot/page/ioreg`. Writes a request into `droidvm_export_box` in guest memory; the guest's `droidvm_export_poll` worker answers. Our requests landed (`request` counter advanced) but `done` never did, so the guest's monitor worker is not running |
| `tools/rig/swap-adt.sh` | regenerates the payload's ADT from `package/crosvm-gunyah.dtb` with `make-droidvm-adt.py` |

Pitfalls learned the hard way:

* **Do not mass-read the guest memfd of a running VM.** A 3 GB `pread` scan of the LEND'ed
  pages ended in `gh_vcpu_mgr` VCPU STATE dumps and a Qualcomm Apps watchdog bite (pstore:
  `msm_watchdog … Causing a QCOM Apps watchdog bite!`), i.e. a phone reboot. Read small windows.
* Symbol addresses are only valid when the kernel's load layout matches the hand-off line. One
  boot read `msgbufp` fine; a later boot returned garbage at the same address, so a scan for
  the msgbuf magic (`0x6d736762`) is the robust way to recover the slide.
* `--hugepages` needs the pool from `gh_hugepage_reserve`; without the module drop
  `--hugepages` and `--prepare-lend-mthp-mode`.
* Keep the pool small when not booting a VM (`pool_want=1024` = 2 GiB); at 3072 pages the host
  had only ~250 MB free.

## 8. The payload build (CI) is fine

`build droidvm release` run 45020390060 (2026-11-22) produced the current payload. Its log
(the repo's `logs/run-45020390060.log`, fetched with `fetch-run-logs.yml`) shows every step:

```
build-droidvm-ownervisor.py            ok
add-ownervisor-data.py                 ok   (data-2g-skip-ov.dbr -> data-12g-skip-ov.dbr)
make-owner-trust-bundle.py             ok
unixnu build payload --owner-trust-cache .../owner-trust.bundle --data-ramdisk .../data-12g-skip-ov.dbr
```

`check turnip-host: FAIL deferred-normal` is expected on CI (it cannot run the host-side Turnip
check) and is not a failure. The trust cache in the ESP (`kernel.macho.trustcache`) hashes to
`bb620660…`, matching `release.json`'s `trust_sha256`, and contains the plugin/daemon/turnip
CDHashes (`f0c41a9f…`, `e55e0bad…`, `7f6a09f0…`). So the payload's signatures are consistent.

## 9. The C++ daemon (DroidVM-Daemon)

The app 0.0.6 Java daemon ignores `extra_options` (importing the author's `vms.json` makes it
start crosvm with PCI-only arguments - verified). The C++ daemon in `Droid-VM/DroidVM-Daemon`
does support it (`vm/crosvm_command.cpp:655` appends `extra_options` verbatim) and also provides
the host-side services the app normally runs (native display, app_console, input, network,
hugepage checks). Built standalone with the NDK:

```sh
# app source (dev branch has the resources; master does not)
git clone --depth 1 -b dev https://github.com/Droid-VM/DroidVM app
git clone --depth 1 https://github.com/Droid-VM/DroidVM-Daemon daemon
# jsoncpp at the app's pinned submodule commit, not 1.9.6
git -C app ls-tree HEAD app/src/main/cpp/jsoncpp      # 3455302847cf1e4671f1d8f5fa953fd46a7b1404
curl -L https://github.com/open-source-parsers/jsoncpp/archive/<commit>.tar.gz | tar xz
cmake -G Ninja -B daemon/build \
  -DCMAKE_TOOLCHAIN_FILE=$NDK/build/cmake/android.toolchain.cmake \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-33 -DCMAKE_BUILD_TYPE=Release \
  -DDROIDVM_JSONCPP_DIR=<jsoncpp> \
  -DDROIDVM_QCOM_XML=app/app/src/main/res/xml/qcom.xml \
  -DDROIDVM_VM_DATA_DIR=<dir with vm-defaults.json+gunyah.json from dev> \
  -DDROIDVM_VERSION_NAME=0.0.6 -DDROIDVM_VERSION_CODE=300 \
  -DDROIDVM_BUILD_PLATFORM_PROBE=OFF \
  -DCMAKE_CXX_FLAGS=-DJSONCPP_HAS_STRING_VIEW=1
ninja -C daemon/build droidvmd        # 68 MB
```

Deploy and run:

```sh
adb push droidvmd /data/local/tmp/ && adb shell su -c '
  am force-stop cn.classfun.droidvm
  /data/local/tmp/droidvmd --data-dir /data/data/cn.classfun.droidvm \
      --apk "$(pm path cn.classfun.droidvm | sed s/package://)" --force'
# -> droidvmd ready pid=… port=…; run/droidvmd-{port,token,pid}.txt published
```

Wire protocol (from `transport/`): 4-byte little-endian length + JSON,
`{"type":"request","command":"auth","token":"<32 hex>","request_id":"1"}` then
`{"type":"request","command":"ping",…}`; responses are `{"type":"response",…}`.
**Open:** our Python client connects but gets no response to `auth`/`ping`; the daemon log shows
nothing, so the handshake (peer uid check? an app login frame? Binder instead of TCP?) still
needs to be matched against the app's `DaemonClient`.

## 10. Issue tracker survey

| issue | what it says |
|---|---|
| [macOS-DroidVM#1](https://github.com/Droid-VM/macOS-DroidVM/issues/1) | macOS 27.2 on SM8750: DarwinBoot completes, kernel reads GPA `0xf000` then hangs silently; closed as "merged into private handling", no public fix |
| [droidvm#87](https://github.com/Droid-VM/droidvm/issues/87) | Windows guest at 4096 MB: black screen + crosvm idle; 3072 MB boots; a simplefb height not divisible by 8 hangs the same way |
| [droidvm#70](https://github.com/Droid-VM/droidvm/issues/70) | Gunyah/QEMU never populates `/chosen` initrd props (Linux guest) |
| [droidvm#12](https://github.com/Droid-VM/droidvm/issues/12), [#15](https://github.com/Droid-VM/droidvm/issues/15) | lent-memory host-access errors; the agent disables `prepare_lend_mthp` by default |
| [droidvm#77](https://github.com/Droid-VM/droidvm/issues/77) | UEFI guests cannot boot from CD/ISO (`edk2-gunyah` reports no bootable option) |
| crosvm#7/#8/#11/#12 | block queue size configurable, standalone framebuffers excluded from RAM, virtio-gpu panic/display-switch hangs, rutabaga resource reset |

Nobody has published a fix for "iOS/macOS kernel hangs after the hand-off on Gunyah".
