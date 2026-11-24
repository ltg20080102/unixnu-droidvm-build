# Local build + boot notes (2026-11-23/24)

Build of `crosvm` (virtio-mmio) from the DroidVM minimal manifest on WSL2, the deployment of
the iPadOS payload to the phone, and what the first boots showed. Everything here is *local*:
the tree lives in `/aosp` (ext4 on `D:\aosp.vhdx`), not in this repository.

State: the guest reaches `DARWINBOOT: hand-off to the kernel` and then the vCPUs go idle.
That is the one open problem; the notes below are what got there and what was ruled out.

## 1. Tree

```sh
repo init -u https://github.com/Droid-VM/crosvm-minimal-manifest -b droidvm -m crosvm-minimal.xml
repo sync -c -j4 --depth 1          # 96 projects
```

Git rewrites used (USTC does not queue; TUNA did, at ~2 MB/min):

```sh
git config --global url."https://mirrors.ustc.edu.cn/aosp/".insteadOf "https://android.googlesource.com/"
git config --global url."https://gh-proxy.com/https://github.com/".insteadOf "https://github.com/"
```

Seven projects the sync left behind (cloned by hand, `--depth 1`):
`toolchain/pgo-profiles`, `system/{server_configurable_flags,security,tools/aidl,tools/xsdc,unwinding}`,
`test/app_compat/csuite`.

Prebuilt gotchas: USTC ignores `--depth` on a re-fetch, so a retried sync re-downloads full
history (we saw `.repo` at 78 GB, 63 GB of it `tmp_pack_*` garbage from aborted retries). For
the two biggest prebuilts a blobless sparse clone is better:

```sh
git clone --filter=blob:none --sparse --depth 1 -b android16-qpr2-release \
  https://mirrors.ustc.edu.cn/aosp/platform/prebuilts/clang/host/linux-x86 prebuilts/clang/host/linux-x86
git -C prebuilts/clang/host/linux-x86 sparse-checkout set --cone clang-r563880c soong
```

`soong` must be in the sparse set: it holds the soong plugin that registers
`llvm_prebuilt_*`, and the root files (`Android.bp`) must be present too, otherwise soong
fails with "unrecognized module type" / "missing dependencies: libclang_rt.builtins".

## 2. Patches the build needs

| file | why |
|---|---|
| `patches/boringssl-visibility-apex.patch` | `external/boringssl` must expose `libcrypto_static`/`libssl_static` to `//external/crosvm/third_party/libdatachannel` and list `com.android.virt` in `apex_available` |
| `patches/crosvm-media-off-and-shim.patch` | the fork's `media` feature wants `virtio-media` 0.0.7+ (with `devices::camera`, `renew_dmabuf_for`); the tree has 0.0.6 and even crates.io 0.0.7 has no `camera`, so the feature is dropped from the four crosvm Android.bp files |

`shim.bin` (14 648 bytes) is `droidvm-shim` built without the softfloat target: the prebuilt
Android Rust has no `aarch64-unknown-none-softfloat` std and no `rust-lld`, so
`droidvm-shim/.cargo/config.toml` adds a `[target.aarch64-unknown-none]` section that links
with the clang prebuilt's `ld.lld` and sets `-C target-feature=-neon` (verified: 0 SIMD
instructions, `_shim_end = 0x7950` < 0x200000). Copy the result to
`external/crosvm/hypervisor/src/gunyah/shim.bin`.

## 3. Build

```sh
source build/envsetup.sh
lunch aosp_arm64 trunk_staging eng
GOGC=30 ALLOW_MISSING_DEPENDENCIES=true TARGET_BUILD_UNBUNDLED=true \
  m crosvm_device_only -j2
scripts/collect_crosvm.sh out/crosvm-pkg     # crosvm + 12 .so, 27 MB
```

`GOGC=30` keeps soong's single-process analysis under ~7.3 GB (without it the 7.6 GB WSL box
OOM-kills `soong_build`); a 16 GB swapfile at `/aosp/swapfile` helps.

## 4. Phone deployment

* `usr/bin/crosvm` <- our build, `crosvm.stock` kept next to it (ours has both mmio and pci,
  so the app's own VMs still start)
* `usr/lib/libvirglrenderer.so`, `libgfxstream_backend.so`, `libvncserver.so` <- ours; the
  app's copies are older and shadow `$ORIGIN`, which is how
  `cannot locate symbol "virgl_set_log_level"` happens
* **firmware**: `firmware/edk2-gunyah-20261113-run43452476331.fd` from this repo
  (sha256 `8855a78e…`). The APK's `VirtioGpuDxe` writes `QueueNum = 0` on virtio-mmio,
  crosvm answers `split queue size 0 is not a power of 2`, and EDK2 stops before BdsDxe -
  see the header of `.github/workflows/build-edk2.yml`. With this file the mmio GPU works.
* images: `/data/media/0/DroidVM/UniXNU/{os.img,systemos.img,droidvm-disk.img,edk2-virtio-gpu.fd}`
* data store: 16 GiB sparse + 4096-byte header `DroidVMDataStore\001\000\000\000`:

```sh
truncate -s 16G data-store.img
printf 'DroidVMDataStore\001\000\000\000' | dd of=data-store.img bs=4096 conv=notrunc
```

## 5. Boot

`start-unixnu.sh` is the command line that reaches the kernel hand-off. Notes:

* `--block …/droidvm-disk.img,lock=false,ro=true` - the key is `ro`, not `readonly`
* every disk on `/storage/emulated/0` needs `lock=false` (FUSE has no flock)
* `--hypervisor gunyah` is required by `--protected-vm-pseudo-unprotected`
* `--virtio-mmio-gpu` only works with the firmware from §4
* `--virtio-media`/`--virtio-mmio-media` are rejected (this build has the media feature off)
* VNC: `--vnc-server host=127.0.0.1,port=12700,screen=simplefb,transport-cap=cpu`,
  `adb forward tcp:12700 tcp:12700`, then any RFB client. The server sends the desktop name
  in ServerInit, so a client must read the 4-byte name length + name after the pixel format.
* hugepage reserve: `gh_hugepage_reserve` with `pool_want=3072` (6 GiB, `pool_size_max=4413`);
  `settings.prop` holds it across reboots. Without the module, drop `--hugepages` and
  `--prepare-lend-mthp-mode`.

Device layout crosvm assigns from the `--virtio-mmio-*` order (matches the author's template):

```
a000000 block os.img        a001000 block systemos.img   a002000 block data-store.img
a003000 input (tablet)      a004000 input (keyboard)
a005000 gpu                 a006000 snd                  (a007000 media - not in this build)
```

GPU args from the author's daemon (`DroidVM-Daemon/vm/crosvm_command.cpp`):

```
--gpu virglrenderer,context-types=drm,vulkan=false,egl=true,gles=true,udmabuf=true,pci-bar-size=4294967296
--prepare-lend-mthp-mode single   --no-usb   --swiotlb 0 (pseudo-unprotected)
```

## 6. Open problem: the kernel idles after the hand-off

Serial log ends with:

```
DARWINBOOT: inputs validated; exiting boot services
DARWINBOOT: hand-off to the kernel
```

and then nothing: the screen keeps the DarwinBoot page, crosvm's `utime` barely moves, no
panic, no vCPU fault. Tried and ruled out:

| variant | result |
|---|---|
| `--mem 5120` | idle |
| `--mem 3072` (droidvm#87 says 3072 works for Windows) | idle, one short CPU burst then idle |
| GPU on mmio (gfxstream) | with the old firmware: UEFI stalls at the TianoCore splash (`split queue size 0`); with the fixed firmware: hand-off, then idle |
| GPU on PCI | UEFI boots, kernel sees no GPU (DarwinBoot only patches `virtio,mmio` nodes) |
| no GPU at all | hand-off, then idle |
| author's device order, + snd, virglrenderer/drm args | hand-off, then idle |
| boot args minus `armcpu-diagnostics=1 droidvm-monitor=1000` | hand-off, then idle |

There is **no kernel serial console** in this guest (the author's `patch-early-probe.py` says
so), so the next step is their probe: patch `kernel.macho` with the EL1 exception catcher,
boot, then read the first ESR/ELR out of guest memory:

* `scripts/patch-early-probe.py` (device repo, `wip/hackintosh`) - probe slots over
  `_pmap_coredump_test`, records ESR/ELR/FAR/SPSR/x30/x29/SP at the first exception
* `tools/rig/memgrab.c` - reads crosvm's `crosvm_guest` memfd coherently (`dc civac`), needs
  an aarch64 Android build
* `tools/rig/peek.py` - host side; `KERNEL_VA=0xFFFFFFF00427C000`, `KERNEL_PA=0x84A7C000`
  (matches our `DARWINBOOT: ARM64 va=… pa=…` line)

Also worth trying: `--file-backed-mapping addr=0xf000,size=0x1000` (macOS-DroidVM#1 tried it
without effect), and `--prepare-lend-mthp-mode disabled` (droidvm#15: the app's agent defaults
`prepare_lend_mthp` to false).

## 7. What the issue trackers say

| issue | what it says |
|---|---|
| [macOS-DroidVM#1](https://github.com/Droid-VM/macOS-DroidVM/issues/1) | macOS 27.2 on SM8750: DarwinBoot completes, kernel reads GPA `0xf000` then hangs silently; maintainer closed it as "merged into private handling" - no public fix |
| [droidvm#87](https://github.com/Droid-VM/droidvm/issues/87) | Windows guest at 4096 MB: black screen + crosvm idle; 3072 MB boots; a simplefb height not divisible by 8 hangs the same way |
| [droidvm#70](https://github.com/Droid-VM/droidvm/issues/70) | Gunyah/QEMU never populates `/chosen` initrd props (Linux guest) |
| [droidvm#12](https://github.com/Droid-VM/droidvm/issues/12), [#15](https://github.com/Droid-VM/droidvm/issues/15) | lent-memory host-access errors; the agent disables `prepare_lend_mthp` by default |
| [droidvm#77](https://github.com/Droid-VM/droidvm/issues/77) | UEFI guests cannot boot from CD/ISO (`edk2-gunyah` reports no bootable option) |
| crosvm#7/#8/#11/#12 | block queue size configurable, standalone framebuffers excluded from RAM, virtio-gpu panic/display-switch hangs, rutabaga resource reset |

Nobody has published a fix for "iOS/macOS kernel hangs after the hand-off on Gunyah".
