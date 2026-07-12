#!/usr/bin/env bash
# build_bcachefs.sh
# Usage: ./build_bcachefs.sh [--engine docker|podman] [version]
#   version: 1.38.8, v1.38.8, or master (default)
#   --engine: container engine to use (default: podman, fallback: docker if podman not found)

set -euo pipefail

# Default values
ENGINE="podman"
VERSION="master"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --engine)
            if [[ -z "${2:-}" ]]; then
                echo "Error: --engine requires an argument (docker or podman)"
                exit 1
            fi
            if [[ "$2" != "docker" && "$2" != "podman" ]]; then
                echo "Error: engine must be 'docker' or 'podman'"
                exit 1
            fi
            ENGINE="$2"
            shift 2
            ;;
        -*)
            echo "Error: unknown option $1"
            exit 1
            ;;
        *)
            VERSION="$1"
            shift
            ;;
    esac
done

# Auto-detect fallback: if podman is not installed and user didn't force engine, use docker
if [[ "$ENGINE" == "podman" ]] && ! command -v podman &> /dev/null; then
    if command -v docker &> /dev/null; then
        echo "[*] podman not found, falling back to docker"
        ENGINE="docker"
    else
        echo "Error: neither podman nor docker found in PATH"
        exit 1
    fi
fi

# Check that the chosen engine is actually available
if ! command -v "$ENGINE" &> /dev/null; then
    echo "Error: $ENGINE is not installed or not in PATH"
    exit 1
fi

# Normalize version: if it looks like a plain number (e.g., 1.38.8), prepend 'v'
if [[ "$VERSION" =~ ^[0-9] ]]; then
    VERSION="v$VERSION"
fi

# GitHub repository containing the patch script
REPO_OWNER="thomas7475"
REPO_NAME="bcachefs-arm32"
PATCH_SCRIPT="auto_build_bcachefs_dkms_claude_patch.py"

# Determine the URL to fetch the patch script
if [[ "$VERSION" == "master" ]]; then
    SCRIPT_URL="https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/master/${PATCH_SCRIPT}"
else
    SCRIPT_URL="https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/refs/tags/${VERSION}/${PATCH_SCRIPT}"
fi

echo "======================================================"
echo "  Bcachefs Builder - Version: $VERSION"
echo "  Container engine: $ENGINE"
echo "  Fetching patch script from: $SCRIPT_URL"
echo "======================================================"

# Download the patch script
echo "[*] Downloading ${PATCH_SCRIPT} ..."
if ! curl -fsSL -o "${PATCH_SCRIPT}" "${SCRIPT_URL}"; then
    echo "Error: Failed to download ${PATCH_SCRIPT} from ${SCRIPT_URL}"
    echo "Please check that version '${VERSION}' exists in the repository."
    exit 1
fi
chmod +x "${PATCH_SCRIPT}"

# Cleanup function
cleanup() {
    echo "[*] Cleaning up temporary build folders..."
    rm -rf ./bcachefs-tools ./vendor-cache-shared ./"${PATCH_SCRIPT}"
    echo "[*] Cleanup done."
}
trap cleanup EXIT

# Build inside the container
echo "[*] Starting build container with $ENGINE..."

# For Podman, unset DBUS_SESSION_BUS_ADDRESS to avoid permission issues
if [[ "$ENGINE" == "podman" ]]; then
    # Use a subshell with DBUS unset for the podman command
    export DBUS_SESSION_BUS_ADDRESS=
fi

# Common run command
"$ENGINE" run --rm --platform=linux/arm/v7 \
    -v "$(pwd)":/build \
    -e DEB_BUILD_OPTIONS="parallel=$(nproc) nocheck nodoc" \
    docker.io/arm32v7/debian:trixie \
    bash -c "
        set -e
        apt-get update
        apt-get install -y --no-install-recommends \
            git cargo dpkg-dev debhelper rustc bindgen devscripts patch quilt python3 \
            dh-cargo dh-dkms jq libaio-dev libblkid-dev libkeyutils-dev libdistro-info-perl \
            liblz4-dev libfuse3-dev libscrypt-dev libsodium-dev libudev-dev libunwind-dev \
            liburcu-dev libzstd-dev pkgconf python3-docutils systemd-dev build-essential \
            uuid-dev zlib1g-dev locales curl
        echo 'en_US.UTF-8 UTF-8' > /etc/locale.gen
        locale-gen
        export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
        cd /build
        python3 ${PATCH_SCRIPT} ${VERSION}
    "

echo "======================================================"
echo " [SUCCESS] Build finished. .deb files are in $(pwd)"
echo "======================================================"