#!/system/bin/sh
B=/data/data/cn.classfun.droidvm
D=/storage/emulated/0/DroidVM/UniXNU
U=$(stat -c %u $B)
rm -f /data/local/tmp/unixnu.sock
exec $B/usr/bin/crosvm run \
  --name unixnu --hypervisor gunyah --mem 5120 --cpus 4 \
  --protected-vm-pseudo-unprotected --no-balloon --disable-sandbox \
  --hugepages --prepare-lend-mthp \
  --socket /data/local/tmp/unixnu.sock \
  --block $D/droidvm-disk.img,lock=false,ro=true \
  --virtio-mmio-block $D/os.img,ro=true,lock=false \
  --virtio-mmio-block $D/systemos.img,ro=true,lock=false \
  --virtio-mmio-block $D/data-store.img,lock=false \
  --virtio-mmio-input \
  --gpu gfxstream,vulkan=false,gles=true --virtio-mmio-gpu \
  --simplefb width=1024,height=768 \
  --vnc-server host=127.0.0.1,port=12700,screen=simplefb,transport-cap=cpu \
  --dump-device-tree-blob /data/local/tmp/unixnu-fdt.dtb \
  --serial type=file,hardware=serial,num=1,earlycon,console,path=/data/local/tmp/unixnu-console.log \
  $D/edk2-virtio-gpu.fd
