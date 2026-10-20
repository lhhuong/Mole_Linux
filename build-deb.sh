#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="${MOLELINUX_VERSION:-1.0}"
if [[ ! "$VERSION" =~ ^[0-9][A-Za-z0-9.+~_-]*$ ]]; then
    echo "Invalid Debian version" >&2
    exit 1
fi
BUILD_ROOT="$SCRIPT_DIR/build/molelinux_${VERSION}_all"
OUTPUT="$SCRIPT_DIR/dist/molelinux_${VERSION}_all.deb"

rm -rf "$BUILD_ROOT" # SAFE: this is a versioned staging path created only by this build script.
mkdir -p "$BUILD_ROOT/DEBIAN" "$BUILD_ROOT/usr/bin" "$BUILD_ROOT/usr/share/molelinux" \
    "$BUILD_ROOT/usr/share/applications" "$BUILD_ROOT/usr/share/pixmaps" \
    "$BUILD_ROOT/usr/share/doc/molelinux" "$SCRIPT_DIR/dist"
for module in core clean uninstall system console; do
    while IFS= read -r -d '' source; do
        destination="$BUILD_ROOT/usr/share/molelinux/${source#"$SCRIPT_DIR/src/"}"
        install -D -m 0644 "$source" "$destination"
    done < <(find "$SCRIPT_DIR/src/$module" -name '*.py' -type f -print0)
done
install -D -m 0755 "$SCRIPT_DIR/packaging/system-helper" "$BUILD_ROOT/usr/lib/molelinux/system-helper"
install -D -m 0644 "$SCRIPT_DIR/packaging/org.molelinux.maintenance.policy" "$BUILD_ROOT/usr/share/polkit-1/actions/org.molelinux.maintenance.policy"
install -m 0755 "$SCRIPT_DIR/src/molelinux.py" "$BUILD_ROOT/usr/share/molelinux/molelinux.py"
ln -s ../share/molelinux/molelinux.py "$BUILD_ROOT/usr/bin/molelinux"
ln -s molelinux "$BUILD_ROOT/usr/bin/mo-linux"
install -m 0644 "$SCRIPT_DIR/README.md" "$BUILD_ROOT/usr/share/doc/molelinux/README.md"
install -m 0644 "$SCRIPT_DIR/LICENSE" "$BUILD_ROOT/usr/share/doc/molelinux/copyright"
INSTALLED_SIZE="$(du -sk "$BUILD_ROOT/usr" | cut -f1)"
sed -e "s/@VERSION@/$VERSION/g" -e "s/@INSTALLED_SIZE@/$INSTALLED_SIZE/g" \
    "$SCRIPT_DIR/packaging/control.in" > "$BUILD_ROOT/DEBIAN/control"
install -m 0755 "$SCRIPT_DIR/packaging/postinst" "$BUILD_ROOT/DEBIAN/postinst"
install -m 0755 "$SCRIPT_DIR/packaging/postrm" "$BUILD_ROOT/DEBIAN/postrm"
find "$BUILD_ROOT" -type d -exec chmod 0755 {} +
dpkg-deb --root-owner-group --build "$BUILD_ROOT" "$OUTPUT"
(cd "$SCRIPT_DIR/dist" && sha256sum "${OUTPUT##*/}" > "${OUTPUT##*/}.sha256")
echo "$OUTPUT"
