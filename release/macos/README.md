# macOS release tooling

Build a technical-preview ZIP from a checkout:

```bash
./release/macos/build_alpha.sh
```

The script:

1. Reads `moltskin.__version__`
2. Requires a built `Chromium.app`
3. Copies the product and the complete Chromium bundle into `dist/`
4. Adds `Setup.command`, `Start Moltskin.command` and `Install Moltskin.command`
5. Creates a ZIP and SHA-256 file

Set `MOLTSKIN_CHROMIUM_BINARY` when the build is not in the default location:

```bash
MOLTSKIN_CHROMIUM_BINARY="$HOME/chromium-baseline/src/out/MoltskinHardwarePrivacy/Chromium.app" \
  ./release/macos/build_alpha.sh
```

This is not a signed/notarized consumer build. See
[the alpha release notes](../../docs/release-macos-alpha.md).
