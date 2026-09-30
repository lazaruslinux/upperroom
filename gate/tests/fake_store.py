"""
An in-process stand-in for the media store.

The real store (store/ in this repo) is a separate service, and gate/store.py
is the only thing in the gate that talks to it. The autouse fixture in
conftest.py swaps that module's six calls for the ones below, which do the same
things to a temp directory, so every test runs against a store without a
network or a second process, and none can reach a real one by accident.

`current()` is the store the running test got, for helpers that write files
without being handed the fixture. `down()` makes every call fail the way an
unreachable store does; `calls` records what was asked, so a test can pin that
something did not ask at all.
"""

import os
import shutil

import store

_current = None


def current():
    return _current


class FakeStore:
    AREAS = ("vods", "clips", "shared")

    def __init__(self, root):
        global _current
        self.root = root
        self.up = True
        self.calls = []
        for area in self.AREAS:
            os.makedirs(os.path.join(root, area), exist_ok=True)
        _current = self

    # ---- for tests ---------------------------------------------------------

    def path(self, area, name):
        return os.path.join(self.root, area, name)

    def write(self, area, name, data=b"bytes on the store"):
        with open(self.path(area, name), "wb") as handle:
            handle.write(data)
        return self.path(area, name)

    def truncate(self, area, name, size):
        """A file that reports `size` bytes without occupying them, for the size
        cap, which reasons about gigabytes."""
        with open(self.path(area, name), "wb") as handle:
            handle.truncate(size)

    def has(self, area, name):
        return os.path.exists(self.path(area, name))

    def names(self, area):
        return sorted(os.listdir(os.path.join(self.root, area)))

    def down(self):
        self.up = False

    def back(self):
        self.up = True

    # ---- the calls gate/store.py makes ------------------------------------

    def _ask(self, what, area=None, name=None):
        self.calls.append(what)
        if not self.up:
            raise store.StoreError(f"{what}: the media store is down (test)")
        if area is not None and (
            area not in self.AREAS or not store.SAFE_NAME.fullmatch(name or "")
        ):
            raise store.StoreError(f"{what}: not a store name", status=404)

    async def put_file(self, area, name, local_path):
        self._ask("put", area, name)
        shutil.copyfile(local_path, self.path(area, name))
        return os.path.getsize(self.path(area, name))

    async def finalize_vod(self, name):
        self._ask("finalize", "vods", name)
        if not self.has("vods", name):
            raise store.StoreError("finalize: no such recording", status=404)
        return {"remuxed": True, "duration": None,
                "size": os.path.getsize(self.path("vods", name))}

    async def link(self, src, dst):
        self._ask("link", "clips", src)
        self._ask("link", "shared", dst)
        if not self.has("clips", src):
            raise store.StoreError("link: no such clip", status=404)
        if not self.has("shared", dst):
            os.link(self.path("clips", src), self.path("shared", dst))

    async def delete(self, area, name):
        if not store.SAFE_NAME.fullmatch(name or ""):
            return False
        self._ask("delete", area, name)
        try:
            os.remove(self.path(area, name))
        except FileNotFoundError:
            return False
        return True

    async def usage(self):
        self._ask("usage")
        sums = {}
        for area in self.AREAS:
            folder = os.path.join(self.root, area)
            sums[f"{area}_bytes"] = sum(
                os.path.getsize(os.path.join(folder, n)) for n in os.listdir(folder)
            )
        disk = shutil.disk_usage(self.root)
        return {**sums, "free_bytes": disk.free, "total_bytes": disk.total}

    async def list_area(self, area):
        self._ask("list")
        folder = os.path.join(self.root, area)
        return [
            {"name": n, "size": os.path.getsize(os.path.join(folder, n)),
             "mtime": int(os.path.getmtime(os.path.join(folder, n)))}
            for n in sorted(os.listdir(folder))
        ]


CALLS = ("put_file", "finalize_vod", "link", "delete", "usage", "list_area")
