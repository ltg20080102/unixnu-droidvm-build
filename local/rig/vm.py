#!/usr/bin/env python3
"""Drive the UniXNU VM through the DroidVM daemon (dc.py).

    vm.py list                 the VMs the daemon knows
    vm.py status [VM]          runtime state of a VM (default: the one named uni*)
    vm.py stop [VM]
    vm.py start [VM]           refreshes the config revision first, so a stale
                               revision from an edit does not abort the start
    vm.py set KEY=VALUE ...    apply config fields to the VM (memory_mb, hugepages,
                               screens.gpu-0.enabled=...), persist them, print the new revision
    vm.py extra --list         print extra_options
    vm.py extra add|remove ARG...   edit extra_options (e.g. add a --virtio-mmio-block pair)

The daemon's config is revisioned:  vm_config_snapshot -> vm_config_apply -> vm_start_configured.
"""
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dc  # noqa: E402


def snapshot(retries=10):
    for _ in range(retries):
        s = dc.call("vm_config_snapshot")
        if "items" in s:
            return s
        time.sleep(2)
    raise SystemExit("no vm_config_snapshot: " + json.dumps(s)[:200])


def document(snap):
    meta = [i for i in snap["items"] if i.get("type") == "metadata"][0]
    chunk = [i for i in snap["items"] if i.get("type") == "chunk"][0]
    return meta["revision"], json.loads(base64.b64decode(chunk["data"]))


def pick(doc, name):
    rows = doc.get("vms", [])
    if name:
        for row in rows:
            if row.get("id") == name or row.get("name") == name:
                return row
        raise SystemExit("no VM " + name)
    for row in rows:
        if (row.get("name") or "").lower().startswith("uni"):
            return row
    if not rows:
        raise SystemExit("the daemon knows no VM")
    return rows[0]


def apply(row, revision):
    r = dc.call("vm_config_apply", revision=revision, operations=[{"op": "replace", "config": row}])
    if not r.get("success"):
        raise SystemExit("vm_config_apply failed: " + json.dumps(r))
    return r["revision"]


def main(argv):
    dc.call("auth", token=dc.token)
    command = argv[1] if len(argv) > 1 else "list"
    if command == "list":
        for row in dc.call("vm_list").get("data", []):
            print(row.get("name"), row.get("id"), row.get("state"), row.get("pid"),
                  row.get("memory_mb"), "hugepages" if row.get("hugepages") else "")
        return 0
    if command == "status":
        name = argv[2] if len(argv) > 2 else None
        revision, doc = document(snapshot())
        row = pick(doc, name)
        print(json.dumps(dc.call("vm_status", vm_id=row["id"])), "\nconfigured",
              row.get("name"), row["id"], "mem", row.get("memory_mb"), "hugepages", row.get("hugepages"))
        return 0
    if command == "stop":
        name = argv[2] if len(argv) > 2 else None
        _, doc = document(snapshot())
        row = pick(doc, name)
        print(json.dumps(dc.call("vm_stop", vm_id=row["id"]))[:160])
        return 0
    if command == "start":
        name = argv[2] if len(argv) > 2 else None
        revision, doc = document(snapshot())
        row = pick(doc, name)
        print(json.dumps(dc.call("vm_start_configured", revision=revision, vm_id=row["id"]))[:160])
        return 0
    if command == "set":
        revision, doc = document(snapshot())
        row = pick(doc, None)
        for item in argv[2:]:
            key, _, value = item.partition("=")
            target, *rest = key.split(".")
            if value.lower() in ("true", "false"):
                value = value.lower() == "true"
            elif value.isdigit():
                value = int(value)
            if rest:
                node = row
                for part in [target] + rest[:-1]:
                    node = node.setdefault(part, {})
                node[rest[-1]] = value
            else:
                row[target] = value
        revision = apply(row, revision)
        print("revision", revision)
        return 0
    if command == "extra":
        revision, doc = document(snapshot())
        row = pick(doc, None)
        options = row.get("extra_options", [])
        if argv[2] == "--list":
            print("\n".join(options))
            return 0
        if argv[2] == "add":
            options.extend(argv[3:])
        elif argv[2] == "remove":
            options = [a for a in options if a not in argv[3:]]
        else:
            raise SystemExit("usage: vm.py extra --list|add|remove ARG...")
        row["extra_options"] = options
        print("revision", apply(row, revision))
        return 0
    raise SystemExit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
