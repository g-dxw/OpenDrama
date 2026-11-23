# OpenDrama repository guidance

## Runtime boundaries

- Keep every read and write endpoint scoped to the authenticated platform or tenant principal.
- Keep paid playback, wallet, order, refund, and webhook decisions on the server.
- Encrypt provider credentials and never return them in API responses, logs, or audit payloads.
- Use the self-host deployment files under `deploy/selfhost`; do not add deployment-specific credentials to source.

## Collaboration

- Make changes only in files needed for the task.
- Do not reset or overwrite unrelated work.
- Run the checks relevant to the changed backend, admin, web, H5, or deployment files before publishing.
