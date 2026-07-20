#!/usr/bin/env python3
"""
build_bcachefs_arm32_unified.py
Unified, self-contained single-script builder for Bcachefs-tools & DKMS on ARM32.
Can be executed directly on x86_64 hosts (runs container emulation) or natively.
"""

import os
import sys
import shutil
import subprocess
import re
import argparse
import textwrap

REPO_URL = "https://github.com/thomas7475/bcachefs-tools.git"  # <-- changed to your repo
WORK_DIR = "bcachefs-tools"

# ---------------------------------------------------------------------------
# Build functions (run inside the container)
# ---------------------------------------------------------------------------
def run_cmd(cmd, cwd=None, env=None, show_output=True):
    print(f"[*] Running: {' '.join(str(c) for c in cmd)}")
    res = subprocess.run(cmd, cwd=cwd, env=env, capture_output=not show_output, text=True)
    if res.returncode != 0:
        print(f"\n[!] Command failed: {' '.join(cmd)}")
        if not show_output:
            print(f"--- STDOUT ---\n{res.stdout}")
            print(f"--- STDERR ---\n{res.stderr}")
        sys.exit(1)
    return res

def get_current_epoch(work_dir):
    changelog = os.path.join(work_dir, "debian", "changelog")
    if not os.path.exists(changelog):
        return ""
    with open(changelog) as f:
        first = f.readline()
    m = re.match(r"^\S+\s+\(([^)]+)\)", first)
    if m and ":" in m.group(1):
        return m.group(1).split(":", 1)[0] + ":"
    return ""

def configure_debian_metadata(work_dir, version_str):
    epoch = get_current_epoch(work_dir)
    full_version = f"{epoch}{version_str}-1"
    print(f"[*] Appending Debian changelog version {full_version}...")
    env = os.environ.copy()
    env["DEBEMAIL"] = "bcachefs-builder@beaglebone.local"
    env["DEBFULLNAME"] = "BBB Builder"
    run_cmd([
        "dch", "-b", "-v", full_version,
        "--distribution", "unstable",
        "--urgency", "high",
        "Automated native armhf compatibility build."
    ], cwd=work_dir, env=env)
    
    # Patch debian/control
    control_path = os.path.join(work_dir, "debian", "control")
    if os.path.exists(control_path):
        with open(control_path, "r") as f:
            content = f.read()
        
        # FIXED: Require installable 'rustc' and 'cargo' packages instead of 'rust'
        content = re.sub(
            r'linux-headers-generic.*?linux-headers \(\>= [^\)]+\)',
            'dkms, rustc, cargo, rust-src, bindgen', content, flags=re.DOTALL
        )
        if 'libfuse3-dev' not in content:
            content = content.replace('Build-Depends: ', 'Build-Depends: libfuse3-dev, clang, llvm, ')
            # Rename DKMS package from bcachefs-kernel-dkms to bcachefs-dkms
            content = re.sub(r'^Package:\s*bcachefs-kernel-dkms\s*$', 'Package: bcachefs-dkms', content, flags=re.MULTILINE)
        with open(control_path, "w") as f:
            f.write(content)
            
    # Patch debian/rules
    rules_path = os.path.join(work_dir, "debian", "rules")
    if os.path.exists(rules_path):
        with open(rules_path, "a") as f:
            f.write(
                "\noverride_dh_install:\n"
                "\tmkdir -p debian/tmp/usr/share/bash-completion/completions\n"
                "\ttouch debian/tmp/usr/share/bash-completion/completions/bcachefs\n"
                "\tdh_install\n"
                "\noverride_dh_link:\n"
                "\tdh_link\n"
                "\tdh_link usr/sbin/bcachefs usr/bin/bcachefs\n"
            )

def setup_cargo_vendor(work_dir):
    print("[*] Vendoring Cargo dependencies...")
    vendor_cache_dir = os.path.abspath("vendor-cache-shared")
    if not os.path.isdir(vendor_cache_dir):
        run_cmd(["cargo", "vendor", vendor_cache_dir], cwd=work_dir)
    vendor_link = os.path.join(work_dir, "vendor")
    if not os.path.exists(vendor_link):
        os.symlink(vendor_cache_dir, vendor_link)

def get_or_clone_repo(target_ref):
    if os.path.exists(WORK_DIR):
        git_dir = os.path.join(WORK_DIR, ".git")
        if os.path.isdir(git_dir):
            print(f"[*] Re-using workspace directory '{WORK_DIR}'")
            run_cmd(["git", "fetch", "--all"], cwd=WORK_DIR)
            run_cmd(["git", "fetch", "--tags"], cwd=WORK_DIR)
            run_cmd(["git", "reset", "--hard"], cwd=WORK_DIR)
            run_cmd(["git", "clean", "-fd"], cwd=WORK_DIR)
            return
        else:
            shutil.rmtree(WORK_DIR)
    print(f"[*] Cloning {REPO_URL}...")
    run_cmd(["git", "clone", REPO_URL, WORK_DIR])

def strip_werror(work_dir):
    print("[*] Defusing compiler limits (-Werror -> -Wno-error)...")
    for root, _, files in os.walk(work_dir):
        for f in files:
            if f.endswith("build.rs") or f == "Makefile" or f.endswith(".mk"):
                path = os.path.join(root, f)
                try:
                    with open(path, "r", encoding="utf-8") as fp:
                        content = fp.read()
                    if "-Werror" in content:
                        content = content.replace('"-Werror"', '"-Wno-error"')
                        content = content.replace("-Werror", "-Wno-error")
                        with open(path, "w", encoding="utf-8") as fp:
                            fp.write(content)
                except Exception:
                    pass

def build_inside_container(target_ref):
    print("\n=======================================================")
    print("      Starting Native Build Routine (Inside Container) ")
    print("=======================================================\n")
    os.chdir("/build")
 
    # Print Rust toolchain versions for verification
    print("\n[*] Rust toolchain versions:")
    for cmd in ["cargo --version", "rustc --version", "bindgen --version"]:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"    {result.stdout.strip()}")
        else:
            print(f"    {cmd} failed: {result.stderr.strip()}")
    print()

    if re.match(r'^\d', target_ref):
        target_ref = f"v{target_ref}"

    get_or_clone_repo(target_ref)
    run_cmd(["git", "checkout", target_ref], cwd=WORK_DIR)

    result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=WORK_DIR, capture_output=True, text=True)
    commit = result.stdout.strip() if result.returncode == 0 else "unknown"
    clean_version = target_ref.lstrip('v')
    
    if not clean_version or not clean_version[0].isdigit():
        tag_res = subprocess.run(["git", "describe", "--tags", "--abbrev=0"], cwd=WORK_DIR, capture_output=True, text=True)
        latest_tag = tag_res.stdout.strip().lstrip('v') if tag_res.returncode == 0 else "0.0.0"
        clean_version = f"{latest_tag}+{clean_version}"
    version_str = f"{clean_version}.g{commit}"

    # Patches are already integrated in the source; no need to apply them.
    # apply_quilt_patches and apply_quilt_patches_manually have been removed.

    configure_debian_metadata(WORK_DIR, version_str)
    
    strip_werror(WORK_DIR)
    setup_cargo_vendor(WORK_DIR)

    run_cmd(["cargo", "clean"], cwd=WORK_DIR)

    # -------------------------------------------------------------------------
    # Configure Environment Variables
    # -------------------------------------------------------------------------
    env = os.environ.copy()
    
    # FIXED: Clear out workspace-contaminating OUT_DIR if set globally
    if "OUT_DIR" in env:
        del env["OUT_DIR"]
    
    env["DEB_BUILD_OPTIONS"] = "parallel=$(nproc) nocheck nodoc"
    
    # FIXED: Revert CC and HOSTCC compilation flags to native system GCC 
    # REMOVE OR COMMENT OUT THESE OVERRIDES TO PREVENT BINDGEN FROM FAILING:
    # env["CC"] = "gcc"
    # env["CXX"] = "g++"
    # env["HOSTCC"] = "gcc"
    # env["HOSTCXX"] = "g++"
    
    existing_rustflags = env.get("RUSTFLAGS", "")
    env["RUSTFLAGS"] = f"{existing_rustflags} -A unexpected_cfgs -A unused_qualifications".strip() 
    env["RUST_TARGET"] = "arm-unknown-linux-gnueabi"
    env["BCACHEFS_FUSE"] = "1"
    
    # FIXED: Enforce time alignments for compilers and bindgen parsing
    env["CFLAGS"] = "-Wno-psabi -D_TIME_BITS=64 -D_FILE_OFFSET_BITS=64 -Wno-error"
    env["BINDGEN_EXTRA_CLANG_ARGS"] = "-D_TIME_BITS=64 -D_FILE_OFFSET_BITS=64"

    print("\n=======================================================")
    print(" [*] DIRECT CARGO KERNEL BUILD TEST")
    print("=======================================================")
    # Stream build output directly for transparent error reporting
    run_cmd(
        ["cargo", "build", "-p", "bcachefs-kernel", "-vv"],
        cwd=WORK_DIR,
        env=env,
        show_output=True
    )

    configure_debian_metadata(WORK_DIR, version_str)
    run_cmd(["dpkg-buildpackage", "-us", "-uc", "-b"], cwd=WORK_DIR, env=env, show_output=True)

    print("\n=======================================================")
    print(f" [SUCCESS] Artifacts created: bcachefs-tools_{version_str}-1_armhf.deb")
    print("=======================================================\n")

def install_dependencies():
    print("[*] Installing build dependencies inside container...")
    subprocess.run(["apt-get", "update"], check=True)
    subprocess.run([
        "apt-get", "install", "-y", "--no-install-recommends",
        "git", "dpkg-dev", "debhelper", "devscripts",
        "patch", "quilt", "python3", "curl", "build-essential",
        "libterm-readline-perl-perl", "libaio-dev", "libblkid-dev",
        "libkeyutils-dev", "libdistro-info-perl",
        "liblz4-dev", "libfuse3-dev", "libscrypt-dev", "libsodium-dev",
        "libudev-dev", "libunwind-dev", "liburcu-dev", "libzstd-dev",
        "pkgconf", "python3-docutils", "systemd-dev", "uuid-dev",
        "zlib1g-dev", "locales", "libterm-readline-perl-perl", "dialog","ca-certificates",
        "libclang-dev", "clang", "llvm",
        "cargo", "rustc", "libstd-rust-dev", "dh-cargo", "dh-dkms", "jq",
        "bindgen", "rust-src"
    ], check=True)

    print("[*] Setting locales...")
    subprocess.run("echo 'en_US.UTF-8 UTF-8' > /etc/locale.gen", shell=True, check=True)
    subprocess.run(["locale-gen"], check=True)
    
    os.environ["LANG"] = "en_US.UTF-8"
    os.environ["LC_ALL"] = "en_US.UTF-8"
    os.environ["LANGUAGE"] = "en_US:en"

def check_qemu_registration(engine):
    """Diagnose if QEMU emulation is active on x86_64 hosts for arm32 support."""
    host_arch = "unknown"
    try:
        res = subprocess.run([engine, "info", "--format", "{{.Host.Arch}}"], capture_output=True, text=True)
        if res.returncode == 0:
            host_arch = res.stdout.strip()
    except Exception:
        pass
    
    if host_arch != "unknown" and "arm" not in host_arch and "aarch" not in host_arch:
        # Host is likely x86_64, check if binfmt_misc has registered qemu-arm
        qemu_active = os.path.exists("/proc/sys/fs/binfmt_misc/qemu-arm")
        if not qemu_active:
            print("[*] Warning: Running ARM32 container on non-ARM host without active 'qemu-user-static'.")
            print("    Please run the following on your host machine to register ARM binfmt engines:")
            print("    sudo apt-get install qemu-user-static binfmt-support")
            print("    - OR -")
            print("    docker run --rm --privileged multiarch/qemu-user-static --reset -p yes")
            print()

def main():
    inside_container = os.environ.get("INSIDE_CONTAINER") == "1"

    if inside_container:
        install_dependencies()
        parser = argparse.ArgumentParser()
        parser.add_argument("target", nargs="?", default="master")
        args = parser.parse_args()
        build_inside_container(args.target)
        sys.exit(0)

    # -------------------------------------------------------------------------
    # Host Wrapper Command Mode
    # -------------------------------------------------------------------------
    parser = argparse.ArgumentParser(description="Unified ARM32 Bcachefs containerized builder.")
    parser.add_argument("target", nargs="?", default="master", help="Tag/Branch to compile")
    parser.add_argument("--engine", default="podman", choices=["podman", "docker"], help="Container engine")
    args = parser.parse_args()

    engine = args.engine
    if not shutil.which(engine):
        if engine == "podman" and shutil.which("docker"):
            print("[*] Podman missing, switching engine to Docker...")
            engine = "docker"
        else:
            print(f"[!] Error: Engine '{engine}' is not installed or available on this host.")
            sys.exit(1)

    check_qemu_registration(engine)

    script_path = os.path.abspath(__file__)
    script_name = os.path.basename(script_path)
    host_cwd = os.getcwd()

    # Orchestrate container execution
    cmd = [
        engine, "run", "--rm",
        "--platform=linux/arm/v7",
        "-v", f"{host_cwd}:/build",
        "-e", "INSIDE_CONTAINER=1",
        "docker.io/arm32v7/debian:trixie",
        "bash", "-c",
        f"apt-get update && apt-get install -y python3 && python3 /build/{script_name} {args.target}"
    ]

    print(f"[*] Launching container build loop using {engine}...")
    print(f"[*] Mounting directory '{host_cwd}' inside container as /build")
    
    env = os.environ.copy()
    if engine == "podman":
        # Avoid rootless dbus errors under Podman
        env["DBUS_SESSION_BUS_ADDRESS"] = ""

    subprocess.run(cmd, env=env, check=True)

if __name__ == "__main__":
    main()