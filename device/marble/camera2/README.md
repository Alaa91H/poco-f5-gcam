# Camera2 Captures

Reviewed Camera2 probe captures for POCO F5 (`marble`) live here.

Each capture should contain:

- `camera2-report.json`
- `CAPABILITY_MATRIX.md`
- `README.md`
- `SHA256SUMS.txt`

## Rules

- Keep the original JSON unchanged after capture.
- Generate the Markdown matrix from the JSON using the repository script.
- Do not guess physical lens names from numeric Camera IDs.
- Record ROM/build context with every capture.
- Compare new ROM captures against previous reports before changing GCam configuration defaults.