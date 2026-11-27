# Moltskin macOS Alpha

This is a technical-preview package for **Apple Silicon macOS 14+**.

## What is included

- Moltskin source and local service
- A pinned Moltskin Chromium build
- Fingerprint patches, including screen/platform/WebGL, edge-aware Canvas noise, silence-preserving audio noise, locale, speech and Windows cross-OS font/WebGL profiles
- A toolbar Moltskin button that opens the current session workbench instead of the manager home
- The Moltskin chameleon app icon in the Dock and Finder
- Setup, start and Finder-install commands

## Install

1. Move the unzipped folder to a permanent location, for example `~/Applications/Moltskin`.
2. Double-click **Setup.command**. If macOS blocks it, right-click it and choose **Open**.
3. Double-click **Start Moltskin.command**.
4. Optional: double-click **Install Moltskin.command** to create a Finder launcher in `~/Applications`.

The bundled Chromium is unsigned. If Gatekeeper blocks it, run:

```bash
xattr -dr com.apple.quarantine "Moltskin-macOS-arm64/Chromium.app"
```

and open the commands with right-click → Open.

## Data

Profile data is stored under:

```text
~/Library/Application Support/Moltskin
```

The package does not include a database, profile data or API token.

## Scope

- macOS 14+ Apple Silicon only
- Chromium-first; Camoufox is installed from `requirements.txt` but its browser may be downloaded separately
- Same-OS macOS profiles are the recommended production configuration
- Windows/Linux emulation is not included in this package
- The app is not signed or notarized yet

## Verification

After installation, open the manager and check the local runtime page in a profile. The Chromium reported version should match the pinned build in `CHANGELOG.md` and `moltskin/__init__.py`.
