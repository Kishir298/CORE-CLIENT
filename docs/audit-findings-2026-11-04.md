# Audit findings and fixes — 4 November 2026

This release preserves the earlier audited source and overlays the newer host ASCS/RESCS/cancellation integration. It is a focused implementation and regression-validation report, not a claim that every line of every external service repository was re-audited in this session.

| Finding or gap | Resolution | Evidence |
|---|---|---|
| Python 3.12 import failures when class methods shadow built-in annotation names | Deferred annotations in resource registry and service manager | Full host suite imports and runs |
| Existing command assumed browser access and working-directory configuration | Headless host command, stable data/config paths, gateway-only service and separate desktop command | Installed-wheel command and HTTP shutdown smoke checks |
| No public approval flow or member-only server boundary | Pending registration, owner approval/rejection/revocation, role checks on protected routes | Public portal tests and native GUI flow |
| General assistant tools were unsuitable for public messaging | Fixed tool-free local Ollama worker for members; privileged adapters remain owner-only | Worker argv and permission tests |
| Shared-device data required explicit account isolation | Bound local profiles and per-server-account context/history/logs/model preferences | Profile, chat and local-store isolation tests |
| Offline record sync could overwrite concurrent server edits | Revision capability and conditional saves; local drafts retained on conflict | Real HTTP gateway/desktop conflict tests |
| File transfer needed account checks and retry integrity | Verified ownership before binary reads, multipart backend contract, SHA-256, size/quota checks and upload IDs | Real loopback RESCS/gateway file contract tests |
| Offline models lacked hardware-aware setup | Memory/container/disk probe, installed model check and explicit verified pull | Hardware selection and controlled pull protocol tests |
| Some messaging tests relied on relay races or obsolete exception/signature assumptions | Corrected fixtures to await confirmed relay and follow current timeout API | Full client suite |
| An integration test expected cross-owner file metadata disclosure | Corrected expectation to DATA_ACCESS_DENIED | Full host suite |

Final suites: CORE-HOST 808 passed; CORE-CLIENT 179 passed. Host warning is a deliberate deprecated TLS 1.1 negative-test setup. Focused public/file/desktop contract retest: 14 passed. Ruff F checks passed. Linux native GUI approval/offline/hardware smoke checks passed. See validation evidence and setup guide.

Open validation boundaries: Windows/macOS native execution, actual Ollama inference/large model downloads, root systemd activation, production RESCS deployment, public DNS/HTTPS and production load tests were not performed. Source delivery does not update GitHub main or deploy a public server. Gateway record serialization does not cover administrators writing directly to RESCS. Local cached plaintext is protected by OS permissions, not app-level encryption.
