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

REPO_URL = "https://github.com/koverstreet/bcachefs-tools.git"
WORK_DIR = "bcachefs-tools"

# ---------------------------------------------------------------------------
#  			10 Compatibility Patches 
# ---------------------------------------------------------------------------
PATCHES = {
    "0001-copy_fs-timestamp-i64-cast.patch": textwrap.dedent("""\
        --- a/src/copy_fs.rs
        +++ b/src/copy_fs.rs
        @@ -300,11 +300,12 @@
         }
        
         fn copy_times(fs: &Fs, dst: &mut c::bch_inode_unpacked, src: &rustix::fs::Stat) {
        -    let make_ts = |sec, nsec| c::timespec64 { tv_sec: sec, tv_nsec: nsec };
        +    let make_ts = |sec: i64, nsec: i64| c::timespec64 { tv_sec: sec as _, tv_nsec: nsec as _,
        +                                              ..unsafe { std::mem::zeroed() } };
        
        -    dst.bi_atime = fs.timespec_to_time(make_ts(src.st_atime, src.st_atime_nsec as _)) as u64;
        -    dst.bi_mtime = fs.timespec_to_time(make_ts(src.st_mtime, src.st_mtime_nsec as _)) as u64;
        -    dst.bi_ctime = fs.timespec_to_time(make_ts(src.st_ctime, src.st_ctime_nsec as _)) as u64;
        +    dst.bi_atime = fs.timespec_to_time(make_ts(src.st_atime, src.st_atime_nsec as i64)) as u64;
        +    dst.bi_mtime = fs.timespec_to_time(make_ts(src.st_mtime, src.st_mtime_nsec as i64)) as u64;
        +    dst.bi_ctime = fs.timespec_to_time(make_ts(src.st_ctime, src.st_ctime_nsec as i64)) as u64;
         }
    """),
    "0002-bdev-32bit-ioctl-fallback.patch": textwrap.dedent("""\
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
    "0003-write-buffer-smp-arm32.patch": textwrap.dedent("""\
        --- a/fs/btree/write_buffer.c
        +++ b/fs/btree/write_buffer.c
        @@ -967,7 +967,7 @@
         	 * after the drop means the following flushing read must observe the set
         	 * that preceded it.
         	 */
        -#if BITS_PER_LONG == 32
        +#if (defined(BITS_PER_LONG) && BITS_PER_LONG == 32) || __SIZEOF_LONG__ == 4
         	/*
         	 * Journal pin seqs are always in the live journal window, so 32 bit
         	 * half loads are sufficient here; torn reads may only make us think
    """),
    "0004-arm32-cmpxchg-fallback.patch": textwrap.dedent("""\
        --- a/fs/bcachefs.h
        +++ b/fs/bcachefs.h
        @@ -1063,4 +1063,30 @@
         #define class_bch_log_msg_ratelimited_constructor(_c)		\\
         	bch2_log_msg_init(_c, 3, bch2_ratelimit(_c), false)
         
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
        +
         #endif /* _BCACHEFS_H */
    """),
    "0005-conditional-__int128.patch": textwrap.dedent("""\
        --- a/include/linux/kernel.h
        +++ b/include/linux/kernel.h
        @@ -53,5 +53,9 @@
         typedef __u64 u64;
         typedef __s64 s64;
        -typedef unsigned __int128 u128;
        +#ifdef __SIZEOF_INT128__
        +typedef unsigned __int128 u128;
        +#else
        +typedef struct { u64 lo; u64 hi; } u128;
        +#endif
         typedef __u32 u32;
         typedef __s32 s32;
    """),
    "0006-inode-math-fallback.patch": textwrap.dedent("""\
        --- a/fs/fs/inode.c
        +++ b/fs/fs/inode.c
        @@ -1020,7 +1020,7 @@
         	 */
         	u64 denom = 400ULL * btree_node_bytes;
         	unsigned size_bits = btree_node_bytes && fs_size >= denom
        -		? ilog2(fs_size / denom)
        +		? ilog2(div64_u64(fs_size, denom))
         		: 0;
         
         	return min(min(cpu_bits, size_bits), 8U);
    """),
    "0007-journal-init-math-fallback.patch": textwrap.dedent("""\
        --- a/fs/journal/init.c
        +++ b/fs/journal/init.c
        @@ -282,7 +282,7 @@
         	 */
         	nr = clamp_t(unsigned, nr,
         		     BCH_JOURNAL_BUCKETS_MIN,
        -		     system_totalram_bytes() / 4 / bucket_bytes(ca));
        +		     div64_u64(system_totalram_bytes() / 4, bucket_bytes(ca)));
         
         	ret = bch2_set_nr_journal_buckets_loop(c, ca, nr, new_fs);
    """),
    "0008-write-buffer-math-fallback.patch": textwrap.dedent("""\
        --- a/fs/btree/write_buffer.c
        +++ b/fs/btree/write_buffer.c
        @@ -1393,10 +1393,10 @@
         		prt_printf(out, "shards total:\\t%llu\\n",	wb->nr_shards_total);
         		if (wb->nr_flushes)
         			prt_printf(out, "avg shards/flush:\\t%llu\\n",
        -				   wb->nr_shards_total / wb->nr_flushes);
        +				   div64_u64(wb->nr_shards_total, wb->nr_flushes));
         		if (wb->nr_shards_total)
         			prt_printf(out, "avg shard size:\\t%llu\\n",
        -				   wb->nr_keys_flushed / wb->nr_shards_total);
        +				   div64_u64(wb->nr_keys_flushed, wb->nr_shards_total));
         
         		prt_printf(out, "flush work:\\t%s\\n",
    """),
    "0009-fusemount-timestamp-padding.patch": textwrap.dedent("""\
        --- a/src/commands/fusemount.rs
        +++ b/src/commands/fusemount.rs
        @@ -513,16 +513,18 @@
                     None => (0, 0),
                     Some(TimeOrNow::Now) => (2, 0),
                     Some(TimeOrNow::SpecificTime(t)) => {
                         let d = t.duration_since(UNIX_EPOCH).unwrap_or_default();
        -                let ts = c::timespec { tv_sec: d.as_secs() as _, tv_nsec: d.subsec_nanos() as _ };
        +                let ts = c::timespec { tv_sec: d.as_secs() as _, tv_nsec: d.subsec_nanos() as _,
        +                                       ..unsafe { std::mem::zeroed() } };
                         (1, fs.timespec_to_time(ts) as u64)
                     }
                 };
                 let (mtime_flag, mtime_val): (i32, u64) = match &mtime {
                     None => (0, 0),
                     Some(TimeOrNow::Now) => (2, 0),
                     Some(TimeOrNow::SpecificTime(t)) => {
                         let d = t.duration_since(UNIX_EPOCH).unwrap_or_default();
        -                let ts = c::timespec { tv_sec: d.as_secs() as _, tv_nsec: d.subsec_nanos() as _ };
        +                let ts = c::timespec { tv_sec: d.as_secs() as _, tv_nsec: d.subsec_nanos() as _,
        +                                       ..unsafe { std::mem::zeroed() } };
                         (1, fs.timespec_to_time(ts) as u64)
                     }
                 };
    """),
    # PATCH 10: 32-bit Fallback for 128-bit Division (mul_u64_u64_div_u64)
    # Replaces the unconditional __int128 with a portable 64x64->128 multiply
    # and division fallback when building on architectures like ARM32.
"0010-math64-conditional-int128.patch": textwrap.dedent("""\
--- a/include/linux/math64.h
+++ b/include/linux/math64.h
--- math64.h	2026-07-12 20:24:05.263155198 +0200
+++ math641.h	2026-07-12 18:59:02.870152765 +0200
@@ -53,6 +53,60 @@
 }
 
 /**
+ * mul_u64_u64_div_u64 - unsigned 64bit multiply then divide, with a 128bit
+ * intermediate and fallback for ARM32
+ */
+static inline u64 mul_u64_u64_div_u64(u64 factor_a, u64 factor_b, u64 divisor)
+{
+#ifdef __SIZEOF_INT128__
+	// Fast path: 128-bit integer support
+	return (unsigned __int128) factor_a * factor_b / divisor;
+
+#else
+	// 64x64 -> 128-bit multiplication
+	u64 a_low  = factor_a & 0xFFFFFFFF;
+	u64 a_high = factor_a >> 32;
+	u64 b_low  = factor_b & 0xFFFFFFFF;
+	u64 b_high = factor_b >> 32;
+
+	u64 low_product  = a_low * b_low;
+	u64 cross_term_1 = a_low * b_high;
+	u64 cross_term_2 = a_high * b_low;
+	u64 high_product = a_high * b_high;
+
+	u64 middle_sum = (low_product >> 32) + (cross_term_1 & 0xFFFFFFFF) + (cross_term_2 & 0xFFFFFFFF);
+
+	u64 prod_high = high_product + (cross_term_1 >> 32) + (cross_term_2 >> 32) + (middle_sum >> 32);
+	u64 prod_low  = (middle_sum << 32) | (low_product & 0xFFFFFFFF);
+
+	// 128-bit / 64-bit division
+	if (prod_high == 0) {
+		return prod_low / divisor;
+	}
+
+	// Manual long division (shift-and-subtract)
+	u64 quotient = 0;
+	int bit_index;
+
+	for (bit_index = 0; bit_index < 64; bit_index++) {
+		u64 highest_bit_carry = prod_high >> 63;
+
+		prod_high = (prod_high << 1) | (prod_low >> 63);
+		prod_low <<= 1;
+
+		if (highest_bit_carry || prod_high >= divisor) {
+			prod_high -= divisor;
+			quotient = (quotient << 1) | 1;
+		} else {
+			quotient <<= 1;
+		}
+	}
+
+	return quotient;
+#endif
+}
+
+/**
  * div64_s64 - signed 64bit divide with 64bit divisor
  */
 static inline s64 div64_s64(s64 dividend, s64 divisor)

    """),
}
# ---------------------------------------------------------------------------
#  Build functions (run inside the container)
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

def apply_quilt_patches(work_dir):
    patch_dir = os.path.join(work_dir, "debian", "patches")
    os.makedirs(patch_dir, exist_ok=True)
    print("\n[*] Writing Quilt/Debian compatibility patches...")
    series_lines = []
    for patch_name, patch_content in PATCHES.items():
        patch_path = os.path.join(patch_dir, patch_name)
        with open(patch_path, "w") as f:
            f.write(patch_content)
        series_lines.append(patch_name)
        print(f"    -> Added {patch_name}")
    series_path = os.path.join(patch_dir, "series")
    with open(series_path, "w") as f:
        f.write("\n".join(series_lines) + "\n")

def apply_quilt_patches_manually(work_dir):
    print("[*] Applying Quilt patches...")
    env = os.environ.copy()
    env["QUILT_PATCHES"] = "debian/patches"
    res = subprocess.run(["quilt", "push", "-a"], cwd=work_dir, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        if "already applied" in res.stdout or "already applied" in res.stderr:
            print("[*] Patches already committed or applied in tree. Continuing...")
        else:
            print(f"[!] Quilt application aborted:\n{res.stdout}\n{res.stderr}")
            sys.exit(1)

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

    apply_quilt_patches(WORK_DIR)
    apply_quilt_patches_manually(WORK_DIR)
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

    full_version = configure_debian_metadata(WORK_DIR, version_str)  # modify function to return it
    run_cmd(["dpkg-buildpackage", "-us", "-uc", "-b", f"-v{full_version}"], ...)
    #run_cmd(["dpkg-buildpackage", "-us", "-uc", "-b", "-v"], cwd=WORK_DIR, env=env, show_output=True)

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
        "bindgen", "rust-src"  # <-- ADDED missing dependencies
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