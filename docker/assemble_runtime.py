"""Assemble a runtime from trusted installed files, retaining OS scan provenance.

No Python extensions are deleted. Every ELF in /usr/local is checked with ldd;
its Debian-owned shared libraries select complete binary packages for copying.
Installer dependencies are not runtime dependencies: the final image deliberately
has no package manager. Certificates, timezone data and dlopen SSL providers are
explicit roots. Build output records the actual file/package dependency graph.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path("/runtime")
ROOT.mkdir()
for name, target in {
    "lib": "usr/lib",
    "lib64": "usr/lib64",
    "bin": "usr/bin",
    "sbin": "usr/sbin",
}.items():
    (ROOT / name).symlink_to(target)


def copy(path):
    source = Path(path)
    if not source.exists() and not source.is_symlink():
        return
    # Preserve the final symlink but normalize merged-/usr parent directories.
    destination = ROOT / str(source.parent.resolve() / source.name).lstrip("/")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        if destination.exists() or destination.is_symlink():
            destination.unlink()
        destination.symlink_to(os.readlink(source))
    elif source.is_file():
        shutil.copy2(source, destination)


# The official slim image carries _tkinter without its GUI libraries. It is
# already unusable there; do not add an X11/Tcl/Tk stack to a serving image.
# Delete only that known broken optional extension, and reject any new missing
# dependency. Working CPython extensions remain intact.
excluded = []
for path in Path("/usr/local/lib/python3.11/lib-dynload").glob("_tkinter.*.so"):
    check = subprocess.run(["ldd", str(path)], capture_output=True, text=True, check=True)
    missing = set(re.findall(r"(\S+) => not found", check.stdout))
    if missing != {"libtk8.6.so", "libtcl8.6.so"}:
        raise RuntimeError(f"Unexpected Tkinter baseline dependencies: {check.stdout}")
    excluded.append({"extension": str(path), "missing_in_official_slim": sorted(missing)})
    path.unlink()

shutil.copytree("/usr/local", ROOT / "usr/local", symlinks=True)
packages = {"ca-certificates", "tzdata", "openssl-provider-legacy", "openssl", "gcc-14-base"}
edges = []
for path in sorted(Path("/usr/local").rglob("*")):
    if not path.is_file() or path.is_symlink():
        continue
    with path.open("rb") as handle:
        if handle.read(4) != b"\x7fELF":
            continue
    # Auditwheel places private libraries beside each other; their entry
    # extension supplies RPATH, which standalone ldd on a private .so lacks.
    # Resolve that object's siblings only for this build-time inspection.
    # This is never an environment setting in the final runtime.
    inspection_env = os.environ.copy()
    inspection_env["LD_LIBRARY_PATH"] = str(path.parent)
    result = subprocess.run(["ldd", str(path)], capture_output=True, text=True, env=inspection_env)
    if "not found" in result.stdout:
        raise RuntimeError(f"Unresolved ELF dependency: {path}: {result.stdout}")
    if result.returncode and "not a dynamic executable" not in result.stderr + result.stdout:
        raise RuntimeError(f"ldd failed: {path}: {result.stderr}")
    for library in re.findall(r"(/[^\s()]+)", result.stdout):
        resolved = Path(library).resolve()
        if str(resolved).startswith("/usr/local/"):
            continue
        owner = subprocess.run(["dpkg-query", "-S", str(resolved)], capture_output=True, text=True)
        if owner.returncode:
            owner = subprocess.run(
                ["dpkg-query", "-S", library], capture_output=True, text=True, check=True
            )
        package = owner.stdout.split(": /", 1)[0]
        packages.add(package)
        edges.append({"elf": str(path), "library": str(resolved), "package": package})

# Shared-library resolution alone does not discover terminal data/config roots.
if any(package.startswith("libncursesw6") for package in packages):
    packages.add("ncurses-base")
if any(package.startswith("libreadline8") for package in packages):
    packages.add("readline-common")

status = []
for package in sorted(packages):
    files = subprocess.check_output(["dpkg-query", "-L", package], text=True).splitlines()
    for filename in files:
        copy(filename)
    status.append(subprocess.check_output(["dpkg-query", "-s", package], text=True).strip())
    # Keep package file provenance for Syft in addition to the full status stanza.
    for path in Path("/var/lib/dpkg/info").glob(package + ".*"):
        if path.suffix in {".list", ".md5sums"}:
            copy(path)

shutil.copytree("/etc/ssl/certs", ROOT / "etc/ssl/certs", symlinks=True, dirs_exist_ok=True)

for filename in [
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/nsswitch.conf",
    "/etc/passwd",
    "/etc/group",
    "/etc/os-release",
    "/usr/lib/os-release",
    "/etc/localtime",
]:
    copy(filename)
for directory in ["tmp", "app", "db", "var/lib/dpkg", "usr/share/runtime"]:
    (ROOT / directory).mkdir(parents=True, exist_ok=True)
(ROOT / "tmp").chmod(0o1777)
(ROOT / "etc/ld.so.conf").write_text("/usr/local/lib\n")
subprocess.run(["ldconfig", "-r", str(ROOT)], check=True)
(ROOT / "var/lib/dpkg/status").write_text("\n\n".join(status) + "\n")
(ROOT / "usr/share/runtime/closure.json").write_text(
    json.dumps(
        {
            "packages": sorted(packages),
            "elf_dependencies": edges,
            "unavailable_desktop_extensions": excluded,
        },
        indent=2,
    )
    + "\n"
)
print(json.dumps({"packages": sorted(packages), "elf_dependency_count": len(edges)}))
