# Bcachefs-Tools ARM32 Auto-Builder 🛠️

![Arch](https://img.shields.io/badge/Architecture-ARM32%20%7C%20armhf%20%7C%20armv7l-blue)
![OS](https://img.shields.io/badge/Target-Debian%20Trixie-red)
![Bcachefs](https://img.shields.io/badge/Bcachefs-Latest-green)

A containerized, fully automated build chain for compiling and packaging **bcachefs-tools** as a Debian (`.deb`) package for 32-bit ARM devices (such as the BeagleBone Black). 

Upstream `bcachefs-tools` heavily relies on 64-bit types, atomic operations, and 128-bit math that cause severe compilation failures on ARM32 architectures. This toolchain automatically wraps the source code with 10 complex compatibility patches and builds it safely inside an isolated Docker/Podman container.

## 📋 Features

* **Zero-Host Pollution:** The entire build process (dependency resolution, rust vendoring, C compiling) happens inside an ephemeral `arm32v7/debian:trixie` container.
* **Intelligent Auto-Patching:** Fixes Rust `bindgen` struct mismatches, maps missing 32-bit `cmpxchg` kernel atomics, and polyfills 128-bit math divisions dynamically.
* **Debian Compliant:** Generates proper `.deb` packages via `dpkg-buildpackage` for easy deployment.
* **Flexible Versioning:** Build `master` for edge testing, or point to specific release tags (e.g., `v1.38.8`).

## 🚀 Prerequisites

1. **Container Engine**: You must have either [Docker](https://docs.docker.com/get-docker/) or [Podman](https://podman.io/getting-started/installation) installed.
2. **QEMU Emulation (If building on x86_64)**: Because this spins up an `arm32v7` container, x86_64 hosts require QEMU emulation.
   * *Ubuntu/Debian:* `sudo apt-get install qemu-user-static binfmt-support`
   * *Arch Linux:* `sudo pacman -S qemu-user-static-bin`

## 💻 Usage

To use the tool, download the wrapper script and run it. The wrapper will automatically pull the Python patching engine.

### Basic Build (Master Branch)
Run the script with no arguments to build the bleeding-edge `master` branch using Podman (falls back to Docker automatically).
```
./build_bcachefs.sh
```
Build a Specific Version

Pass the version tag as an argument:
```
./build_bcachefs.sh v1.38.8
# or
./build_bcachefs.sh 1.38.8
```
Specify a Container Engine

Force the script to use Docker or Podman:
```
./build_bcachefs.sh --engine docker v1.38.8
./build_bcachefs.sh --engine podman master
```
## 📂 Build Artifacts

Upon completion, the container will exit, clean up temporary vendoring
directories, and leave your compiled package in the current directory:

   `bcachefs-tools_<version><commit>-1_armhf.deb` 
   
   `bcachefs-tools_1.38.8.gdde5ecf6a-1_armhf.deb`

Copy this file to your target ARM device and install it via 
```
sudo dpkg -i <filename.deb>.
```

🧠 Architecture & Patches

To successfully compile bcachefs on ARM32, the Python script
(auto_build_bcachefs_dkms_claude_patch.py) intercepts the Debian build process
via quilt and applies version specific patches.

| Patch Name                                 | Description                                                                                                                                                                                                           |
| :----------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **0001 & 0009: Timestamp padding**         | Re-aligns `_TIME_BITS=64` missing hidden struct paddings (like `_bitfield_1`) missed by Rust's `bindgen` in `copy_fs.rs` and `fusemount.rs`.                                                                          |
| **0002: 32-bit IOCTLs**                    | Drops back to a 32-bit command code (`0x80041272`) for block device operations instead of hardcoded 64-bit codes.                                                                                                     |
| **0003 & 0004: SMP/Atomics**               | Bypasses GCC compiler ignorance for 32-bit `smp_load_acquire` commands and maps missing `cmpxchg` methods to the kernel's native `cmpxchg64`.                                                                         |
| **0005 & 0010: `__int128` polyfill**       | ARM32 GCC outright refuses to compile the `__int128` type. These patches provide structurally identical 16-byte structs and intercept 128-bit intermediate math (`mul_u64_u64_div_u64`) with standard bitwise shifts. |
| **0006, 0007, 0008: div64\_u64 fallbacks** | Native 64-bit divisions on ARM32 trigger linker errors (`__aeabi_uldivmod`). We explicitly route these specific inode, btree, and journal variables through Linux's internal safe `div64_u64` macro.                  |

## 🛠️ Advanced Modification

If you want to modify the Python patch script locally instead of pulling it from
GitHub:

1.  Open build_bcachefs.sh.
2.  Comment out or delete the curl line that downloads the script.
3.  Keep both build_bcachefs.sh and auto_build_bcachefs_dkms_claude_patch.py in
    your local directory when you execute the bash wrapper.

## ⚠️ Troubleshooting

  - exec format error: Your host doesn't have QEMU user-static configured
    properly for cross-platform container execution. Install qemu-user-static
    and restart your container service.
  - Rust Out of Memory: Rust compilation via Cargo can consume huge amounts of
    RAM, especially with QEMU overhead. If the container crashes randomly,
    ensure your machine has at least 8GB of RAM + Swap.
  - Network Failures During Build: Cargo vendoring requires internet access.
    Ensure your container daemon allows outbound DNS resolution.

📄 License

This tooling is released under the GPL-2.0 License.
