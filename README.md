# MoleLinux 1.0

A terminal toolkit for cleaning, uninstalling applications, analyzing disk usage, and maintaining Ubuntu. MoleLinux provides an interactive console and command-line tools, with previews and confirmation before changes.

## Features

- **Clean** — Scan known cache and log locations and preview eligible files before cleanup.
- **Uninstall** — Select multiple applications, review the removal plan, and remove verified application-specific data after successful package removal.
- **Optimize** — Run supported maintenance tasks with Polkit authentication when administrative privileges are required.
- **Analyze** — Inspect allocated disk usage, with logical file sizes reported separately.
- **Status** — Monitor CPU, memory, swap, disk usage, network activity, and battery status.
- **History** — Review previous operations and restore eligible files moved to Trash.

MoleLinux is a console application. Launch it from a terminal using `molelinux` or `mo-linux`. The interactive menu is currently in Vietnamese; some backend messages are in English.

## Requirements

- Ubuntu with Python **3.10 or later**.
- Polkit for privileged operations.
- APT/dpkg and systemd for Ubuntu maintenance and package management.
- Snap or Flatpak tools when managing applications installed through those systems.

## Installation

Place `molelinux_1.0_all.deb` and its `.sha256` file in the same directory, then run:

```bash
sha256sum -c molelinux_1.0_all.deb.sha256
sudo apt install ./molelinux_1.0_all.deb
molelinux
```

Run MoleLinux as your regular user. Do **not** launch it with `sudo molelinux`; it requests administrative privileges when needed.

### Build from source

The application source is in [`MoleLinux-1.0/`](MoleLinux-1.0/). From this repository's root directory:

```bash
cd MoleLinux-1.0
bash build-deb.sh
cd dist
sha256sum -c molelinux_1.0_all.deb.sha256
sudo apt install ./molelinux_1.0_all.deb
```

Building requires Bash and `dpkg-deb`. The build script writes the package and checksum to `MoleLinux-1.0/dist/`.

## Usage

### Interactive mode

```bash
molelinux
```

- Use **Up/Down** and **Enter** to navigate, or **1–6** to open a feature directly.
- Press **q** or **0** to exit the main menu.
- In Uninstall, press **Space** to select applications, **/** to search, and **Enter** to review the plan.
- In Status, press **q** or **Esc** to return.
- In History, select a session by number to inspect or restore it.
- Review the plan before confirming with **y/N**.

### Command-line mode

```bash
# Preview cleanup without changing files
molelinux clean --dry-run

# List installed applications
molelinux apps --json

# Preview removal; replace APP_ID with an ID from the application list
molelinux uninstall APP_ID --dry-run

# Analyze a directory
molelinux analyze ~/Downloads

# Show system information and operation history
molelinux status --json
molelinux history --json

# Preview restoring a recorded session
molelinux restore SESSION_ID --dry-run

# Display help and version
molelinux --help
molelinux --version
```

Sizes are displayed in KiB, MiB, and GiB. JSON output and operation records retain exact byte counts.

## Cleanup and recovery behavior

MoleLinux scans known locations rather than treating all cached or application data as disposable. Files that are in use, inaccessible, protected by a whitelist, or part of unverified shared data are retained.

User files are moved to **Trash**. This does not free disk space until Trash is emptied. Application removal uses the appropriate package manager; DEB removal uses `purge`, and matching Snap snapshots may be permanently deleted after preview and confirmation.

Restoring a session can recover eligible trashed files. It does not reinstall removed packages, recover deleted Snap snapshots, or undo irreversible maintenance operations. MoleLinux reports remaining paths and does not guarantee removal of every trace of an application.

## Development and testing

Run these commands as a regular user from the repository root:

```bash
cd MoleLinux-1.0
MOLE_TEST_NO_AUTH=1 bash tests/run.sh
bash build-deb.sh
python3 tests/verify_package.py dist/molelinux_1.0_all.deb
```

Tests use a temporary HOME, subprocesses, PTYs, and simulated package managers. They do not uninstall real applications.

```text
MoleLinux-1.0/
├── src/            # Python application source
├── packaging/      # Debian metadata, privileged helper, and Polkit policy
├── tests/          # Automated tests and package verification
├── build-deb.sh    # Package and checksum builder
├── CHANGELOG.md    # Release notes
└── LICENSE         # GPL-3.0 license
```

Generated `build/` and `dist/` directories, Python caches, and virtual environments are excluded by the application's `.gitignore`.

## Current limitations

Full validation on Ubuntu 22.04/24.04 virtual machines and ARM64 is still pending. The project has not undergone an independent security audit, and the Debian package is unsigned.

## License and credits

MoleLinux is distributed under **GNU GPL v3**. See [LICENSE](MoleLinux-1.0/LICENSE).

Inspired by [Mole](https://github.com/tw93/mole) by tw93, MoleLinux implements its own Linux backend and is an independent adaptation.
