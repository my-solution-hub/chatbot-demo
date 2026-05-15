# Nova Sonic Speech Demo

A short, hands-on demo of Amazon Nova Sonic (`amazon.nova-2-sonic-v1:0`) speech-to-speech on Amazon Bedrock. Talk to it, hear it talk back, and watch it call two simple tools (`get_current_time`, `get_weather`) when your spoken request needs them. It is meant as a clean starting point you can read in 30 minutes and extend from there — not a production reference.

## What you'll see

```
Nova Sonic Demo: model=amazon.nova-2-sonic-v1:0 region=ap-northeast-1
LISTENING: ready for speech
USER: what's the weather in seattle?
TOOL_CALL: get_weather {"city":"Seattle"}
TOOL_RESULT: get_weather {"city":"Seattle","condition":"rainy","temperature_c":14}
ASSISTANT: It's rainy in Seattle, about 14 degrees Celsius.
```

You speak, the model speaks back through your speakers, and tool calls appear on stdout in real time.

## 1. Prerequisites

- **Python 3.12** (required by `aws-sdk-bedrock-runtime`)
  
  We recommend [pyenv](https://github.com/pyenv/pyenv) to manage Python versions without touching your system Python:

  ```bash
  # Install pyenv (macOS)
  brew install pyenv

  # Install pyenv (Linux)
  curl https://pyenv.run | bash
  # Then add pyenv to your shell — see https://github.com/pyenv/pyenv#set-up-your-shell-environment

  # Install Python 3.12 and set it for this project
  pyenv install 3.12
  pyenv local 3.12          # creates .python-version in the repo root
  python --version          # should print Python 3.12.x
  ```

  On Windows, use the [Python.org installer](https://www.python.org/downloads/) for 3.12.x.

- **PortAudio** (microphone + speaker bindings used by `sounddevice`)
  | Platform | Install command |
  | --- | --- |
  | macOS | `brew install portaudio` |
  | Debian / Ubuntu | `sudo apt-get install libportaudio2` |
  | Windows | Bundled with the `sounddevice` wheel — nothing to install |
- **AWS account** with Amazon Bedrock access and **Nova Sonic** model access enabled. Currently supported regions:
  - `us-east-1` (N. Virginia)
  - `us-east-2` (Ohio)
  - `us-west-2` (Oregon)
  - `ap-northeast-1` (Tokyo)
- **AWS credentials** resolvable by the standard SDK chain (env vars, `~/.aws/credentials`, named profile, SSO, IAM role — anything boto3 understands)

> **Tip for APAC users:** Tokyo (`ap-northeast-1`) gives the lowest round-trip latency and the cleanest audio. If your account doesn't have Sonic access in Tokyo, Oregon (`us-west-2`) is the next-best option.

## 2. Install

```bash
git clone <this repo>
cd chatbot-demo

# Ensure you're on Python 3.12 (pyenv users: pyenv local 3.12)
python --version   # must be 3.12.x

python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate

pip install -r requirements.txt     # runtime
# (optional) pip install -r requirements-dev.txt  # adds pytest + hypothesis
```

## 3. Configure AWS access

The demo uses the standard AWS SDK credential chain. Pick whichever you already use:

```bash
# Option A: environment variables
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=ap-northeast-1

# Option B: named profile (uses ~/.aws/credentials)
export AWS_PROFILE=my-profile
export AWS_REGION=ap-northeast-1

# Option C: SSO
aws sso login --profile my-profile
export AWS_PROFILE=my-profile
export AWS_REGION=ap-northeast-1
```

The IAM principal needs `bedrock:InvokeModelWithBidirectionalStream` on the Nova Sonic model in the chosen region. If credentials or region are missing or wrong, the demo prints a clear error and exits with one of the codes in the table below.

## 4. Run

```bash
python -m nova_sonic_demo
```

Speak into the microphone. Press **Ctrl+C** to stop — the demo drains audio, closes the Bedrock session, and exits within 5 seconds.

### Try these prompts

- "Hello." — a simple turn, no tools
- "What time is it in Tokyo?" — calls `get_current_time`
- "What's the weather in Seattle?" — calls `get_weather`
- "Tell me the weather in Paris and the time in New York." — exercises both tools in one turn

## 5. Tuning the audio (recommended for cross-region use)

Nova Sonic streams uncompressed 16-bit PCM both ways, so long-haul links can sound choppy out of the box. The defaults already include three optimisations:

- **Voice activity detection** (VAD): silent frames are dropped, batches of 80 ms are coalesced into one event
- **Half-duplex echo gate**: the microphone is muted while the speakers are playing the assistant's voice, so the model doesn't talk to itself
- **Player jitter buffer**: 250 ms of audio is buffered before playback begins, absorbing cross-region network jitter

If you still hear stuttering, try one of these:

```bash
# Choppy or laggy audio: enlarge the jitter buffer
python -m nova_sonic_demo --prebuffer-ms 400

# Microphone keeps picking up your fan / TV: stricter VAD
python -m nova_sonic_demo --vad-aggressiveness 3

# Wearing headphones (no echo path): free both directions for barge-in
python -m nova_sonic_demo --no-echo-cancel

# Bandwidth-constrained: bigger batches, longer hangover
python -m nova_sonic_demo --vad-batch-frames 6 --vad-hangover-ms 1200

# Compare against the un-tuned baseline
python -m nova_sonic_demo --no-vad --no-echo-cancel --prebuffer-ms 0
```

All flags:

| Flag | Default | What it does |
| --- | --- | --- |
| `--no-vad` | off | Stream every microphone frame instead of gating on speech. |
| `--no-echo-cancel` | off | Disable the mic mute that runs while the speaker is playing. Use only with headphones. |
| `--vad-aggressiveness` | `2` | webrtcvad strictness, 0 (lenient) to 3 (strict). |
| `--vad-frame-ms` | `20` | VAD window: 10, 20, or 30 ms. |
| `--vad-batch-frames` | `4` | VAD frames coalesced into one Bedrock event. 4 = 80 ms. Higher cuts overhead. |
| `--vad-hangover-ms` | `800` | Keep streaming this long after the last voice frame so pauses aren't clipped. |
| `--vad-preroll-ms` | `200` | Include this much pre-trigger audio when the gate opens (preserves first phoneme). |
| `--prebuffer-ms` | `250` | Player jitter buffer warmup. Higher hides more jitter at the cost of latency. |

## 6. Reading the stdout output

Every prefix is one event in the conversation:

| Prefix | Meaning |
| --- | --- |
| `Nova Sonic Demo: model=... region=...` | Startup banner |
| `LISTENING: ready for speech` | Microphone is live; you can talk |
| `USER: <text>` | What the model heard you say (final transcript) |
| `TOOL_CALL: <name> <args-json>` | Model decided to call a tool |
| `TOOL_RESULT: <name> <result-json>` | Tool returned a result back to the model |
| `ASSISTANT: <text>` | What the model said back (final transcript; audio plays simultaneously) |

The demo prints each turn exactly once. If a line is missing, that step did not happen.

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `Missing input device` (exit 3) | No microphone detected | Plug in / select a default mic |
| `Region <r> does not support Nova Sonic v2` (exit 2) | Bedrock Sonic isn't available in your `AWS_REGION` | Use one of the supported regions above |
| `AWS credentials missing or invalid` (exit 4) | Credential chain came up empty | Set `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` or `AWS_PROFILE` |
| `Bedrock open failed (auth): ...` (exit 5) | Credentials worked locally but Bedrock rejected them | Verify the IAM principal has Bedrock model-invoke permission on Nova Sonic |
| Demo runs but no `USER:` line ever appears | Mic level is too low, or the system mic is muted | Check OS sound settings; try `--no-vad` to verify capture |
| Assistant talks to itself in a loop | Echo gate disabled and you're not on headphones | Drop `--no-echo-cancel` or put on headphones |
| Audio is choppy / robotic | Cross-region jitter | Increase `--prebuffer-ms` (try `400`) and/or use a closer region |
| Tool call seems to happen but no `TOOL_CALL:` line | Likely a parser regression — please open an issue with the full stdout | — |

## 8. Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Clean shutdown (Ctrl+C) |
| `2` | Unsupported AWS region |
| `3` | Missing microphone or speaker |
| `4` | AWS credentials missing or invalid |
| `5` | Bedrock session could not be opened (auth / network / region / model) |

## 9. Architecture at a glance

The runtime is a single asyncio event loop. Five modules, each ~one screen of code:

| Module | Responsibility |
| --- | --- |
| `nova_sonic_demo/cli.py` | Lifecycle: startup, event routing, Ctrl+C shutdown |
| `nova_sonic_demo/session.py` | Bedrock bidirectional stream wrapper; opens the session, sends/receives events |
| `nova_sonic_demo/audio.py` | Microphone capture, VAD-gated batching, speaker playback with jitter buffer |
| `nova_sonic_demo/events.py` | Builders + parsers for Nova Sonic input/output events |
| `nova_sonic_demo/tools/` | Tool registry, dispatcher (with timeout & schema validation), the two demo tools |
| `nova_sonic_demo/logging.py` | stdout prefix logger (`USER:`, `ASSISTANT:`, `TOOL_CALL:`, `TOOL_RESULT:`) |

The full design lives in [`.kiro/specs/nova-sonic-speech-demo/design.md`](.kiro/specs/nova-sonic-speech-demo/design.md), including sequence diagrams and the property-based testing strategy (P1–P7).

## 10. Adding your own tools

`nova_sonic_demo/tools/registry.py` is the only file you need to touch. The pattern is:

```python
async def my_tool(args: dict) -> dict:
    # validate args, do work, return a JSON-serialisable dict
    return {"status": "ok"}

# Then register it alongside the existing tools:
ToolDefinition(
    name="my_tool",
    description="Does the thing.",
    schema={
        "type": "object",
        "properties": {"...": {"type": "string"}},
        "required": ["..."],
    },
    handler=my_tool,
)
```

Tool calls run inside the same asyncio loop, with a 10-second timeout per call and JSON Schema validation. Errors are returned to the model so it can apologise gracefully instead of crashing.

## 11. Running the tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

The suite includes property-based tests with `hypothesis` covering tool dispatch, deterministic mocked weather, timezone resolution, logger grammar, session lifecycle, and dispatcher latency bounds.

## 12. What this demo deliberately leaves out

This is a starter. The following are intentionally **not** implemented so the code stays readable:

- Wake-word detection ("Hey Nova")
- Multi-session continuation past the 8-minute Bedrock connection limit
- Real weather API integration (the included `get_weather` returns a deterministic mock so the demo runs offline-of-the-internet)
- Telephony / SIP integration
- True acoustic echo cancellation (the demo uses a simpler half-duplex mute; works fine on speakers, but doesn't allow barge-in)

These are easy to layer on once you understand the core loop.

## License

See [`LICENSE`](LICENSE).
