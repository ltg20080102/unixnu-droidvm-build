# Local build + boot notes (2026-11-23)

Build of `crosvm` (virtio-mmio) from the DroidVM minimal manifest on WSL2, and the
first boot of the iPadOS payload on the phone. Everything here is *local*: the tree
lives in `/aosp` (ext4 on `D:\aosp.vhdx`), not in this repository.

## 1. Tree

```sh
repo init -u https://github.com/Droid-VM/crosvm-minimal-manifest -b droidvm -m crosvm-minimal.xml
repo sync -c -j4 --depth 1          # 96 projects
```

Git rewrites used (USTC does not queue, TUNA did):

```sh
git config --global url."https://mirrors.ustc.edu.cn/aosp/".insteadOf "https://android.googlesource.com/"
git config --global url."https://gh-proxy.com/https://github.com/".insteadOf "https://github.com/"
```

Seven projects the sync left behind (cloned by hand, `--depth 1`):
`toolchain/pgo-profiles`, `system/{server_configurable_flags,security,tools/aidl,tools/xsdc,unwinding}`,
`test/app_compat/csuite`.

## 2. Patches that the build needs

| file | why |
|---|---|
| `patches/boringssl-visibility-apex.patch` | `external/boringssl` must expose `libcrypto_static`/`libssl_static` to `//external/crosvm/third_party/libdatachannel` and list `com.android.virt` in `apex_available` |
| `patches/crosvm-media-off-and-shim.patch` | the fork's `media` feature wants `virtio-media` 0.0.7 (with `devices::camera`, `renew_dmabuf_for`), the tree has 0.0.6: drop the feature from the four crosvm Android.bp files |

`shim.bin` (14 648 bytes) is `droidvm-shim` built without the softfloat target:

```sh
cd external/crosvm/droidvm-shim
RUSTFLAGS="-C target-feature=-neon" \
  cargo build --release --target aarch64-unknown-none \
  -Z build-std=core,alloc        # not needed with the prebuilt rust: see .cargo/config.toml
```

The prebuilt Android Rust has no `aarch64-unknown-none-softfloat` std and no `rust-lld`,
so `droidvm-shim/.cargo/config.toml` adds a `[target.aarch64-unknown-none]` section that
links with the clang prebuilt's `ld.lld` and disables NEON. Copy the result to
`external/crosvm/hypervisor/src/gunyah/shim.bin` (soong `include_bytes!`s it).

## 3. Build

```sh
source build/envsetup.sh
lunch aosp_arm64 trunk_staging eng
GOGC=30 ALLOW_MISSING_DEPENDENCIES=true TARGET_BUILD_UNBUNDLED=true \
  m crosvm_device_only -j2
scripts/collect_crosvm.sh out/crosvm-pkg     # crosvm + 12 .so, 27 MB
```

`GOGC=30` is what keeps soong's analysis (single process) under 7.3 GB; without it the
7.6 GB WSL box OOM-kills `soong_build`. A 16 GB swapfile at `/aosp/swapfile` helps.

## 4. Phone deployment

* `usr/bin/crosvm` <- our build, `crosvm.stock` kept next to it
* `usr/lib/libvirglrenderer.so`, `libgfxstream_backend.so`, `libvncserver.so` <- ours
  (the app's copies are older and shadow `$ORIGIN`, which is how
  `cannot locate symbol "virgl_set_log_level"` happens)
* **firmware**: use `firmware/edk2-gunyah-20261113-run43452476331.fd` from this repo
  (sha256 `8855a78e…`), not the one the APK ships. The APK's `VirtioGpuDxe` writes
  `QueueNum = 0` on virtio-mmio, crosvm then says
  `split queue size 0 is not a power of 2`, and EDK2 stops before BdsDxe - so the
  payload never starts (see the header of `.github/workflows/build-edk2.yml`).
* images: `/data/media/0/DroidVM/UniXNU/{os.img,systemos.img,droidvm-disk.img,edk2-virtio-gpu.fd}`
* data store: 16 GiB sparse file with a 4096-byte header holding `DroidVMDataStore\001\000\000\000`:

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
* `--virtio-media`/`--virtio-mmio-media` are rejected (the build has the media feature off)
* VNC: `--vnc-server host=127.0.0.1,port=12700,screen=simplefb,transport-cap=cpu`,
  `adb forward tcp:12700 tcp:12700`, then any RFB client

Reached: `UEFI -> BOOTAA64.EFI -> UniXNU -> DarwinBoot -> XNU hand-off` (serial log +
screen). Still open: the kernel idles right after the hand-off (vCPU time barely moves),
screen stays on the DarwinBoot page.
