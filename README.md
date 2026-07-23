# Bcachefs-Tools ARM32 Auto-Builder 🛠️

![Arch](https://img.shields.io/badge/Architecture-ARM32%20%7C%20armhf%20%7C%20armv7l-blue)
![OS](https://img.shields.io/badge/Target-Debian%20Trixie-red)
![Bcachefs](https://img.shields.io/badge/Bcachefs-Latest-green)

A containerized, fully automated build chain for compiling and packaging **bcachefs-tools** as a Debian (`.deb`) package for 32-bit ARM devices (such as the BeagleBone Black). 

Upstream `bcachefs-tools` heavily relies on 64-bit types, atomic operations, and 128-bit math that cause severe compilation failures on ARM32 architectures. This unified builder automates the environment setup, cargo vendoring, 64-bit time alignments, and native Debian packaging inside an isolated Docker or Podman container.

## 📋 Features

* **Unified & Self-Contained:** A single Python script (`build_bcachefs_arm32_unified.py`) handles host container orchestration and containerized building with no external downloads required.
* **Zero-Host Pollution:** The entire build process (dependency resolution, Rust vendoring, C compiling) happens inside an ephemeral `arm32v7/debian:trixie` container.
* **32-Bit ARM Compatibility Alignment:** Configures `_TIME_BITS=64` for Rust `bindgen`, defuses strict compiler limits (`-Wno-error`), and forces 64-bit offsets.
* **Debian Compliant:** Generates proper `.deb` packages via `dpkg-buildpackage` for easy deployment.
* **Flexible Versioning:** Build `master` for edge testing, or point to specific release tags (e.g., `v1.38.8`).

## 🚀 Prerequisites

1. **Python 3**: Required on the host machine to run the orchestration script.
2. **Container Engine**: Either [Docker](https://docs.docker.com/get-docker/) or [Podman](https://podman.io/getting-started/installation) installed.
3. **QEMU Emulation (If building on x86_64)**: Because this spins up an `arm32v7` container, x86_64 hosts require QEMU emulation.
   * *Ubuntu/Debian:* `sudo apt-get install qemu-user-static binfmt-support`
   * *Arch Linux:* `sudo pacman -S qemu-user-static-bin`

## 💻 Usage

Make the script executable or pass it directly to `python3`.

### Basic Build (Master Branch)
Run the script with no arguments to build the `master` branch using Podman (automatically falls back to Docker if Podman is missing):

```
./build_bcachefs_arm32_unified.py
```
Build a Specific Version

Pass the version tag as an argument:
```
./build_bcachefs_arm32_unified.py v1.38.8
# or
./build_bcachefs_arm32_unified.py master
```
Specify a Container Engine

Force the script to use Docker or Podman:
```
./build_bcachefs_arm32_unified.py --engine docker v1.38.8
./build_bcachefs_arm32_unified.py --engine podman master
```
## 📂 Build Artifacts

Upon completion, the container will exit, clean up temporary vendoring
directories, and leave your compiled package in the current directory:

   `bcachefs-tools_<version><commit>-1_armhf.deb` 
   
   `bcachefs-tools_1.38.8.gdde5ecf6a-1_armhf.deb`

Copy this file to your target ARM device and install it via 
```
sudo dpkg -i bcachefs-tools_*.deb
```

📂 Build Artifacts

Upon completion, the container exits and leaves your compiled package in the current directory:

bcachefs-tools_<version>-1_armhf.deb

Copy this file to your target ARM device and install it via:

```
./build_bcachefs_arm32_unified.py --engine docker v1.38.8
./build_bcachefs_arm32_unified.py --engine podman master
```
                      |
## 🧠 Build Environment Configuration

The script automatically configures essential ARM32 compiler flags and environment variables inside the container:

| Option / Flag | Description |
| :--- | :--- |
| `_TIME_BITS=64 -D_FILE_OFFSET_BITS=64` | Enforces 64-bit `time_t` support across C compilers and Rust `bindgen` parsing. |
| `-Wno-psabi -Wno-error` | Prevents compiler warnings and ABI mismatch warnings from breaking the build. |
| `BCACHEFS_FUSE=1` | Enables FUSE mounting support in the compiled binaries. |
| Cargo Caching | Uses shared caching in `vendor-cache-shared/` to speed up subsequent builds. |

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
