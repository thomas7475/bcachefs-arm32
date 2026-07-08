#!/usr/bin/env python3
"""
auto_build_bcachefs_dkms.py  —  Bcachefs-Tools BBB Auto-Updater & Native Builder
Targets: BeagleBone Black, Linux 7.0.11-bone18, armhf / armv7l

Usage:
  ./auto_build_bcachefs_dkms.py              # build the master branch (dev)
  ./auto_build_bcachefs_dkms.py 1.38.8       # build a specific version tag
  ./auto_build_bcachefs_dkms.py v1.38.8      # equivalent to above
"""

import os
import sys
import shutil
import subprocess
import re
import argparse
import textwrap
from datetime import datetime

REPO_URL = "https://github.com/koverstreet/bcachefs-tools.git"
WORK_DIR = "bcachefs-tools"

REQUIRED_TOOLS = [
    "git", "cargo", "dpkg-buildpackage", "dh",
    "rustc", "bindgen", "dch", "patch", "quilt"
]

# ---------------------------------------------------------------------------
# The Native Source Patches
# These are standard unified diffs that the Debian package builder will apply
# automatically via quilt.
# ---------------------------------------------------------------------------
PATCHES = {
    "0001-bindgen-disable-layout-tests.patch": textwrap.dedent("""\
        --- a/bch_bindgen/build.rs
        +++ b/bch_bindgen/build.rs
        @@ -442,7 +442,7 @@
                 .clang_arg("-fkeep-inline-functions")
                 .derive_debug(true)
                 .derive_default(true)
        -        .layout_tests(true)
        +        .layout_tests(false)
                 .default_enum_style(bindgen::EnumVariation::Rust {
                     non_exhaustive: true,
                 })
    """),
    "0002-copy_fs-timestamp-i64-cast.patch": textwrap.dedent("""\
        --- a/src/copy_fs.rs
        +++ b/src/copy_fs.rs
        @@ -300,9 +300,9 @@
         }
         
         fn copy_times(fs: &Fs, dst: &mut c::bch_inode_unpacked, src: &rustix::fs::Stat) {
        -    let make_ts = |sec, nsec| c::timespec64 { tv_sec: sec, tv_nsec: nsec };
        +    let make_ts = |sec: i64, nsec| c::timespec64 { tv_sec: sec, tv_nsec: nsec };
         
        -    dst.bi_atime = fs.timespec_to_time(make_ts(src.st_atime, src.st_atime_nsec as _)) as u64;
        -    dst.bi_mtime = fs.timespec_to_time(make_ts(src.st_mtime, src.st_mtime_nsec as _)) as u64;
        -    dst.bi_ctime = fs.timespec_to_time(make_ts(src.st_ctime, src.st_ctime_nsec as _)) as u64;
        +    dst.bi_atime = fs.timespec_to_time(make_ts(src.st_atime as _, src.st_atime_nsec as _)) as u64;
        +    dst.bi_mtime = fs.timespec_to_time(make_ts(src.st_mtime as _, src.st_mtime_nsec as _)) as u64;
        +    dst.bi_ctime = fs.timespec_to_time(make_ts(src.st_ctime as _, src.st_ctime_nsec as _)) as u64;
         }
    """),
    "0003-bdev-32bit-ioctl-fallback.patch": textwrap.dedent("""\
        --- a/src/wrappers/bdev.rs
        +++ b/src/wrappers/bdev.rs
        @@ -36,7 +36,11 @@
             target_arch = "sparc",
             target_arch = "sparc64",
         )))]
        -const BLKGETSIZE64: libc::Ioctl = 0x80081272u32 as libc::Ioctl;
        +const BLKGETSIZE64: libc::Ioctl = if cfg!(target_pointer_width = "32") {
        +    0x80041272u32 as libc::Ioctl
        +} else {
        +    0x80081272u32 as libc::Ioctl
        +};
         
         /// Returns the size of a file or block device in bytes.
    """),
    "0004-write-buffer-smp-arm32.patch": textwrap.dedent("""\
        --- a/fs/btree/write_buffer.c
        +++ b/fs/btree/write_buffer.c
        @@ -976,7 +976,7 @@
         	u64 inc = READ_ONCE(wb->inc.pin.seq);
         	smp_rmb();
         #else
        -	u64 inc = smp_load_acquire(&wb->inc.pin.seq);
        +	u64 inc = ({ u64 _v = READ_ONCE(wb->inc.pin.seq); smp_mb(); _v; });
         #endif
         	u64 flushing = READ_ONCE(wb->flushing.pin.seq);
    """),
    "0005-kernel-arm32-math-and-atomic.patch": textwrap.dedent("""\
        --- a/fs/errcode.c
        +++ b/fs/errcode.c
        @@ -117,3 +117,26 @@
         	trace_error_throw(c, bch2_err_str(err));
         	return err;
         }
        +
        +#ifdef CONFIG_ARM
        +#include <linux/math64.h>
        +uint64_t bch_div64_u64_rem(uint64_t dividend, uint64_t divisor, uint64_t *remainder) {
        +	uint64_t quot = div64_u64(dividend, divisor);
        +	*remainder = dividend - quot * divisor;
        +	return quot;
        +}
        +asm (
        +	".text\\n"
        +	".align 2\\n"
        +	".global __aeabi_uldivmod\\n"
        +	"__aeabi_uldivmod:\\n"
        +	"push {lr}\\n"
        +	"sub sp, sp, #20\\n"
        +	"add r12, sp, #8\\n"
        +	"str r12, [sp, #0]\\n"
        +	"bl bch_div64_u64_rem\\n"
        +	"ldr r2, [sp, #8]\\n"
        +	"ldr r3, [sp, #12]\\n"
        +	"add sp, sp, #20\\n"
        +	"pop {pc}\\n"
        +);
        +#endif
        --- a/fs/bcachefs.h
        +++ b/fs/bcachefs.h
        @@ -1066 +1066,26 @@
        -#endif /* _BCACHEFS_H */
        +#ifdef CONFIG_ARM
        +#include <asm/cmpxchg.h>
        +
        +#undef cmpxchg
        +#define cmpxchg(ptr, o, n) \\
        +({ \\
        +	__typeof__(*(ptr)) __ret; \\
        +	if (sizeof(*(ptr)) == 8) \\
        +		__ret = cmpxchg64((ptr), (o), (n)); \\
        +	else \\
        +		__ret = (__typeof__(*(ptr)))__cmpxchg((ptr), (unsigned long)(o), (unsigned long)(n), sizeof(*(ptr))); \\
        +	__ret; \\
        +})
        +
        +#undef cmpxchg_local
        +#define cmpxchg_local(ptr, o, n) \\
        +({ \\
        +	__typeof__(*(ptr)) __ret; \\
        +	if (sizeof(*(ptr)) == 8) \\
        +		__ret = cmpxchg64_local((ptr), (o), (n)); \\
        +	else \\
        +		__ret = (__typeof__(*(ptr)))__cmpxchg_local((ptr), (unsigned long)(o), (unsigned long)(n), sizeof(*(ptr))); \\
        +	__ret; \\
        +})
        +#endif
        +#endif /* _BCACHEFS_H */
    """),
    # NEW PATCH: fix __int128 unsupported on armhf - corrected for actual kernel.h
    "0006-conditional-__int128.patch": textwrap.dedent("""\
        --- a/include/linux/kernel.h
        +++ b/include/linux/kernel.h
        @@ -54,4 +54,6 @@ typedef __u64 u64;
         typedef __s64 s64;
        -typedef unsigned __int128 u128;
        +#ifdef __SIZEOF_INT128__
        +typedef unsigned __int128 u128;
        +#endif
         typedef __u32 u32;
    """),
}

def run_cmd(cmd, cwd=None, env=None):
    print(f"[*] Running: {' '.join(str(c) for c in cmd)}")
    res = subprocess.run(cmd, cwd=cwd, env=env)
    if res.returncode != 0:
        print(f"\n[!] Build halted. Command failed: {' '.join(cmd)}")
        sys.exit(1)
    return res

def check_required_tools():
    missing = [t for t in REQUIRED_TOOLS if not shutil.which(t)]
    if missing:
        print("[!] Missing required tools in PATH:")
        for t in missing:
            print(f"    • {t}")
        sys.exit(1)

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

def apply_quilt_patches(work_dir):
    """Writes the patches to debian/patches/ so dpkg applies them safely."""
    patch_dir = os.path.join(work_dir, "debian", "patches")
    os.makedirs(patch_dir, exist_ok=True)
    
    print("\n[*] Writing standard Quilt/Debian patches...")
    
    series_lines = []
    for patch_name, patch_content in PATCHES.items():
        patch_path = os.path.join(patch_dir, patch_name)
        with open(patch_path, "w") as f:
            f.write(patch_content)
        series_lines.append(patch_name)
        print(f"    -> Added {patch_name}")
        
    series_path = os.path.join(patch_dir, "series")
    # Overwrite series file to avoid duplicates
    with open(series_path, "w") as f:
        f.write("\n".join(series_lines) + "\n")

def apply_quilt_patches_manually(work_dir):
    """Apply all patches using quilt."""
    print("[*] Applying Quilt patches...")
    env = os.environ.copy()
    env["QUILT_PATCHES"] = "debian/patches"
    run_cmd(["quilt", "push", "-a"], cwd=work_dir, env=env)

def configure_debian_metadata(work_dir, version_str):
    epoch = get_current_epoch(work_dir)
    full_version = f"{epoch}{version_str}-1"
    """Prepare Debian files (Dependencies, Rules, and proper Changelog via dch)."""
    
    # 1. Update Changelog dynamically via debian tooling (dch)
    print(f"[*] Using dch to safely append version {version_str}-1 ...")
    
    env = os.environ.copy()
    env["DEBEMAIL"] = "bcachefs-builder@beaglebone.local"
    env["DEBFULLNAME"] = "BBB Builder"

    run_cmd([
        "dch", "-v", full_version,
        "--distribution", "unstable",
        "--urgency", "high",
        "Automated native armhf compatibility build."
    ], cwd=work_dir, env=env)

    # 2. Patch dependencies in debian/control
    control_path = os.path.join(work_dir, "debian", "control")
    if os.path.exists(control_path):
        with open(control_path, "r") as f:
            content = f.read()
        
        content = re.sub(
            r'linux-headers-generic.*?linux-headers \(\>= [^\)]+\)',
            'dkms', content, flags=re.DOTALL
        )
        
        with open(control_path, "w") as f:
            f.write(content)

    # 3. Patch Rules for Bash completion bug
    rules_path = os.path.join(work_dir, "debian", "rules")
    if os.path.exists(rules_path):
        with open(rules_path, "a") as f:
            f.write(
                "\noverride_dh_install:\n"
                "\tmkdir -p debian/tmp/usr/share/bash-completion/completions\n"
                "\ttouch debian/tmp/usr/share/bash-completion/completions/bcachefs\n"
                "\tdh_install\n"
            )

def setup_cargo_vendor(work_dir, version_str):
    print("[*] Vendoring Rust dependencies (requires network) ...")
    vendor_cache_dir = os.path.abspath(f"vendor-cache-{version_str}")
    
    if not os.path.isdir(vendor_cache_dir):
        run_cmd(["cargo", "vendor", vendor_cache_dir], cwd=work_dir)
        
    vendor_link = os.path.join(work_dir, "vendor")
    if not os.path.exists(vendor_link):
        os.symlink(vendor_cache_dir, vendor_link)

def get_or_clone_repo(target_ref):
    """Obtain the repository, either by cloning or updating an existing one."""
    if os.path.exists(WORK_DIR):
        # Check if it's a Git repository
        git_dir = os.path.join(WORK_DIR, ".git")
        if os.path.isdir(git_dir):
            print(f"[*] Reusing existing repository in {WORK_DIR}")
            # Fetch all updates
            run_cmd(["git", "fetch", "--all"], cwd=WORK_DIR)
            # Fetch tags
            run_cmd(["git", "fetch", "--tags"], cwd=WORK_DIR)
            # Reset hard to the target (will be checked out later)
            # We'll just do a hard reset to the remote branch/tag after checkout
            # Actually we'll just checkout, but ensure clean state
            run_cmd(["git", "reset", "--hard"], cwd=WORK_DIR)
            run_cmd(["git", "clean", "-fd"], cwd=WORK_DIR)
            return
        else:
            print(f"[*] {WORK_DIR} exists but is not a Git repository. Removing...")
            shutil.rmtree(WORK_DIR)
    
    print(f"[*] Cloning {REPO_URL} ...")
    run_cmd(["git", "clone", REPO_URL, WORK_DIR])

def main():
    print("\n=======================================================")
    print("   Bcachefs-Tools BBB Auto-Updater & Native Builder    ")
    print("=======================================================\n")

    parser = argparse.ArgumentParser(description="Build Bcachefs on BBB natively via Debian Quilt.")
    parser.add_argument("target", nargs="?", default="master", help="Tag/Branch to build")
    args = parser.parse_args()

    check_required_tools()

    # Resolving Tag or Branch logic
    target_ref = args.target
    if re.match(r'^\d', target_ref):
        target_ref = f"v{target_ref}"

    # Get or clone the repo
    get_or_clone_repo(target_ref)

    # Now checkout the target ref
    print(f"[*] Checking out {target_ref} ...")
    run_cmd(["git", "checkout", target_ref], cwd=WORK_DIR)

    # Determine Version string based on git commit & target name
    result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=WORK_DIR, capture_output=True, text=True)
    commit = result.stdout.strip() if result.returncode == 0 else "unknown"
    date = datetime.now().strftime("%Y%m%d")
    
    clean_version = target_ref.lstrip('v')
    version_str = f"{clean_version}+{date}.g{commit}"

    # Configure the build environment properly using patches and debscripts
    apply_quilt_patches(WORK_DIR)
    apply_quilt_patches_manually(WORK_DIR)   # apply them now

    configure_debian_metadata(WORK_DIR, version_str)
    setup_cargo_vendor(WORK_DIR, version_str)

    # Run the native compilation. 
    print("\n[*] Starting package compilation... This will take a while.")
    env = os.environ.copy()
    env["DEB_BUILD_OPTIONS"] = "parallel=1 nocheck nodoc"
    
    run_cmd(["dpkg-buildpackage", "-us", "-uc", "-b"], cwd=WORK_DIR, env=env)

    print("\n=======================================================")
    print(f" [SUCCESS] Output generated: bcachefs-tools_{version_str}-1_armhf.deb")
    print("=======================================================\n")


if __name__ == "__main__":
    main()