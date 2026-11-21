# AI idle, startup and token policy

4 November 2026

## Requested behavior

- Model weights unload after 60 seconds idle after a request, using Ollama keep_alive=60s. Active generation is not killed by the idle setting. The shared Ollama API daemon stays running so later messages can load the model again.
- A real chat request starts the model on demand. The metadata lookup, connection, response headers and first content/thinking token share a 30-second startup budget. Empty progress events cannot reset it. A completed empty response is also accepted within the budget.
- Once a response starts, up to 60 seconds of stream silence is allowed between chunks. That is an independent stalled-response timeout, not a 60-second cap on total generation.
- No warm-up prompts, preloads, keep-awake traffic or automatic retries are scheduled by CORE. Checking /api/show reads metadata; it does not request token generation.
- Generation uses num_predict=-1, Ollama's unlimited-generation setting. Replies can still end normally at EOS or a configured stop sequence.
- Context uses the installed model's reported native maximum from /api/show model_info, rather than the former fixed 4096-token setting. Missing or invalid metadata fails clearly; the code does not guess a model limit or silently reduce it.
- Maximum context is finite and shared by prompt/history and generation. Unlimited generation does not mean infinite context or infinite hardware. Backend context shifting/stop behavior remains Ollama/model behavior.

## What changed

Identical core/inference.py and client/inference.py implement this policy through loopback HTTP. Public server messaging and the desktop Local model source use it. The old 1024-output-token cap, fixed 4096 context and aggregate response-size aborts were removed from those paths. A per-chunk transport bound remains; UI output/history retention remains bounded. The existing service job execution timeout of 15 minutes remains separate from token settings. Very long jobs can therefore still be stopped by that execution timeout.

Stop interrupts a blocked local model socket. Model loading/stream failures are reported without switching models, retrying another backend or sending a hidden prompt. Existing accounts, permissions and private conversation paths are unchanged.

## Linux Ollama defaults

The combined installer copies CORE-HOST/deploy/linux/ollama-idle.conf to /etc/systemd/system/ollama.service.d/risarms-idle.conf and reloads systemd. It does not restart an already running Ollama service unexpectedly.

For an existing installation, run these from the extracted RISARMS-Portal folder:

```sh
sudo install -d -m 0755 /etc/systemd/system/ollama.service.d
sudo install -m 0644 CORE-HOST/deploy/linux/ollama-idle.conf /etc/systemd/system/ollama.service.d/risarms-idle.conf
sudo systemctl daemon-reload
# Restart when no active tasks need to finish.
sudo systemctl restart ollama
```

This sets OLLAMA_KEEP_ALIVE=60s for services that use the Ollama daemon's default. A third-party service can override that default in its own requests. CORE's direct chat requests explicitly send 60s and require no daemon restart for that request-level setting.

The external ASIS/ASCS/TIVISS repositories are not bundled or rewritten in this release. Their own warm-up loops and per-request token overrides must be updated in those repositories; this package does not claim to override arbitrary third-party provider code. Their included integration prompts now explicitly request the same idle/startup/token policy. The native device TCP client is not an inference server.

## Hardware and cloud behavior

The model checker still sizes candidate weights with small-context planning estimates. Its report now explicitly states that maximum context is not covered by that estimate and may require much more RAM/VRAM. A recommended model may still fail at maximum context. No automatic model downgrade occurs.

A 30-second startup limit may be too short for larger models on CPU-only/free servers. A timeout is an error, not a guarantee that the model can cold-start within that time. No actual model weights were downloaded or inference timed in this environment. Request/timeout behavior was tested with a controlled loopback provider.

This changes model residency, not the cloud provider's VM lifetime. It cannot control Render's platform sleep policy or guarantee free server availability. Data stays on disk after model unload; offline fallback still requires a downloaded local model.

## Upgrade and validation

Back up server and client data, then install the updated packages from CORE-HOST and CORE-CLIENT. The root deployment/install-server.sh is still the Linux server installer; existing owners and passwords are preserved. Restart CORE after upgrading. Reinstall/update the desktop app and reopen it.

Current suite results and XML evidence are under validation; the release manifest reports the exact counts. Prior GUI evidence is labeled previous-build and does not establish real model loading performance. Public DNS/HTTPS, production systemd activation and Windows/macOS execution were not performed here.

References: https://docs.ollama.com/api/chat ; https://docs.ollama.com/modelfile ; https://docs.ollama.com/faq
