#!/usr/bin/env python3
"""Patch one instruction of a kernel.macho (Mach-O arm64) by kernel virtual address.

  patch-kernel.py IN OUT --va 0xfffffff00c01ca84 --ret
  patch-kernel.py IN OUT --va 0xfffffff00bff9440 --bytes c0035fd6
  patch-kernel.py IN OUT --va ... --show        # only print what is there

The VA is mapped through the Mach-O LC_SEGMENT_64 load commands, so the file
offset is exact (the kernel's __TEXT_EXEC is not at file offset 0).  A `ret`
is 0xD65F03C0 little endian, i.e. c0 03 5f d6.
"""
import argparse
import struct
import sys

RET = bytes.fromhex("c0035fd6")


def file_offset(data, va):
    if struct.unpack("<I", data[:4])[0] != 0xFEEDFACF:
        raise SystemExit("not a 64-bit Mach-O")
    count = struct.unpack("<I", data[16:20])[0]
    offset = 32
    for _ in range(count):
        cmd, size = struct.unpack("<II", data[offset:offset + 8])
        if cmd == 0x19:  # LC_SEGMENT_64
            name = data[offset + 8:offset + 24].rstrip(b"\0").decode()
            vmaddr, vmsize, fileoff, filesize = struct.unpack("<QQQQ", data[offset + 24:offset + 56])
            if vmaddr <= va < vmaddr + vmsize and filesize:
                return va - vmaddr + fileoff, name
        offset += size
    raise SystemExit(f"VA {va:#x} is not in a file-backed segment")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source")
    parser.add_argument("destination")
    parser.add_argument("--va", required=True, type=lambda v: int(v, 0))
    parser.add_argument("--ret", action="store_true", help="write a ret")
    parser.add_argument("--bytes", help="hex bytes to write, e.g. c0035fd6")
    parser.add_argument("--show", action="store_true", help="print the current bytes and stop")
    args = parser.parse_args()
    data = bytearray(open(args.source, "rb").read())
    offset, segment = file_offset(data, args.va)
    print(f"VA {args.va:#x} -> file {offset:#x} ({segment}); current {data[offset:offset + 4].hex(' ')}")
    if args.show:
        return 0
    patch = RET if args.ret else bytes.fromhex((args.bytes or "").replace(" ", ""))
    if not patch:
        raise SystemExit("give --ret or --bytes")
    data[offset:offset + len(patch)] = patch
    open(args.destination, "wb").write(data)
    print(f"written {patch.hex(' ')} -> {args.destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
