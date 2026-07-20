#!/usr/bin/env bash
# build_bcachefs.sh — Auto-Build Container Wrapper
set -euo pipefail

ENGINE="podman"
VERSION="master"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --engine)
            if [[ -z "${2:-}" ]]; then
                echo "Error: --engine option requires either 'docker' or 'podman'."
                exit 1
            fi
            ENGINE="$2"
            shift 2
            ;;
        *)
            VERSION="$1"
            shift
            ;;
    esac
done

if [[ "$ENGINE" == "podman" ]] && ! command -v podman &>/dev/null; then
    if command -v docker &>/dev/null; then
        echo "[*] podman not detected. Falling back to docker engine..."
        ENGINE="docker"
    else
        echo "Error: No container execution engine detected in system path."
        exit 1
    fi
fi

PATCH_SCRIPT="auto_build_bcachefs_dkms_claude_patch.py"

echo "======================================================"
echo "  Bcachefs Builder — Version: $VERSION"
echo "  Container engine: $ENGINE"
echo "======================================================"

cleanup() {
    echo "[*] Performing cleanup steps..."
    rm -rf ./bcachefs-tools ./vendor-cache-shared 2>/dev/null || true
    echo "[*] Cleanup complete."
}
trap cleanup EXIT

# Run clean isolated build environment
echo "[*] Orchestrating compilation inside isolated arm32v7 environment..."

"$ENGINE" run --rm --platform=linux/arm/v7 \
    -v "$(pwd)":/build \
    docker.io/arm32v7/debian:trixie \
    bash -c "
        set -euo pipefail
        apt-get update
        apt-get install -y --no-install-recommends \
            git cargo dpkg-dev debhelper rustc bindgen devscripts patch quilt python3 \
            dh-cargo dh-dkms jq libaio-dev libblkid-dev libkeyutils-dev libdistro-info-perl \
            liblz4-dev libfuse3-dev libscrypt-dev libsodium-dev libudev-dev libunwind-dev \
            liburcu-dev libzstd-dev pkgconf python3-docutils systemd-dev build-essential \
            uuid-dev zlib1g-dev locales ca-certificates libclang-dev clang llvm libterm-readline-perl-perl dialog
            
        echo 'en_US.UTF-8 UTF-8' > /etc/locale.gen
        locale-gen
        export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
        
        cd /build
        python3 ${PATCH_SCRIPT} ${VERSION}
    "

echo "======================================================"
echo " [SUCCESS] Build finished. .deb files are in $(pwd)"
echo "======================================================"