# Security Policy

Moltskin runs a local HTTP service on `127.0.0.1` and stores browser profiles on disk.

## Reporting

Please report vulnerabilities privately to the maintainers before opening a public
issue. Do not include real cookies, proxy credentials, or API tokens in reports.

## Design notes

- The API binds to loopback by default. Do not expose port `8765` to the public
  internet; if you must, put it behind an authenticated reverse proxy and change
  the token model.
- The API token is stored in `data/api.token` with mode `0600`.
- Docker Compose binds the published port to `127.0.0.1` only.
- Proxy credentials are stored in the local SQLite database. Use full-disk
  encryption and restrict access to the data directory. Encryption at rest is
  planned.
- Profile directories contain live cookies. Treat `data/` as sensitive.
