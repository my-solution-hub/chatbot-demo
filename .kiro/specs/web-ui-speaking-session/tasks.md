# Implementation Plan: Web UI Speaking Session

## Overview

Add a browser-based voice session interface to the Nova Sonic demo. The implementation creates a FastAPI web server with WebSocket support that bridges browser audio to the existing `SonicSession`. The work is structured as: dependencies → data models/protocol → WebLogger → SessionManager → FastAPI app → browser client → integration wiring.

## Tasks

- [x] 1. Set up project structure and dependencies
  - [x] 1.1 Add fastapi and uvicorn to requirements.txt
    - Add `fastapi>=0.115.0` and `uvicorn[standard]>=0.30.0` to `requirements.txt`
    - _Requirements: 1.1_

  - [x] 1.2 Create the web module package structure
    - Create `nova_sonic_demo/web/__init__.py`
    - Create `nova_sonic_demo/web/static/` directory (with empty `.gitkeep`)
    - _Requirements: 1.4_

- [x] 2. Implement WebSocket message protocol and validation
  - [x] 2.1 Create message types and validation (`nova_sonic_demo/web/messages.py`)
    - Define dataclasses: `TranscriptMessage`, `ToolCallMessage`, `ToolResultMessage`, `StatusMessage`, `ErrorMessage`
    - Implement `serialize_server_message(msg) -> str` that converts any ServerMessage to JSON
    - Implement `parse_client_command(text: str) -> Optional[ClientCommand]` that validates JSON and extracts start/stop commands, returning None for invalid input
    - Implement `validate_audio_bytes(data: bytes) -> bool` that checks len > 0 and len % 2 == 0
    - _Requirements: 3.3, 3.5, 7.1, 7.2, 7.3, 7.4_

  - [ ]* 2.2 Write property tests for message validation
    - **Property 6: Command message validation**
    - **Property 1: Audio forwarding with validation (validation portion)**
    - Test that `parse_client_command` only accepts valid start/stop JSON
    - Test that `validate_audio_bytes` correctly accepts/rejects byte sequences
    - **Validates: Requirements 7.1, 7.2, 7.3, 7.4, 3.3, 3.5**

- [x] 3. Implement WebLogger
  - [x] 3.1 Create WebLogger class (`nova_sonic_demo/web/logger.py`)
    - Subclass `ConsoleLogger`
    - Accept a `send_fn: Callable[[dict], Awaitable[None]]` in constructor
    - Override `tool_call(name, arguments)` to serialize and call `send_fn` with `{"type": "tool_call", "name": name, "arguments": args}`
    - Override `tool_result(name, result)` to serialize and call `send_fn` with `{"type": "tool_result", "name": name, "result": result}`
    - Respect session-active gating (inherited `_session_active` flag)
    - Handle non-serializable payloads by substituting `"<non-serializable>"`
    - Override `_write` to suppress stdout output
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [ ]* 3.2 Write property tests for WebLogger
    - **Property 4: WebLogger serialization correctness**
    - Generate random tool names and JSON-serializable dicts, verify correct output
    - Verify session gating suppresses output when inactive
    - Verify non-serializable fallback
    - **Validates: Requirements 4.2, 4.3, 8.2, 8.3, 8.4, 8.5**

- [x] 4. Implement SessionManager
  - [x] 4.1 Create SessionManager class (`nova_sonic_demo/web/session_manager.py`)
    - Implement state machine: `ready → connecting → active → closed` with `error` transitions
    - Implement `start()`: resolve credentials/region, build registry/dispatcher/session, open session, transition states, send status messages
    - Implement `handle_audio(pcm_bytes)`: validate bytes (len > 0, len % 2 == 0), forward to `session.send_audio()` only when active
    - Implement `run_event_loop()`: consume `session.stream_events()`, route `AudioOutEvent` as binary, route `TranscriptEvent` as JSON
    - Implement `stop()`: close session within `SHUTDOWN_DEADLINE_S`, transition to closed
    - Accept injectable factories for session, registry, and dispatcher (for testing)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.3, 3.4, 3.5, 4.1, 5.1, 5.2, 5.3, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

  - [ ]* 4.2 Write property tests for SessionManager audio forwarding
    - **Property 1: Audio forwarding with validation**
    - Generate random byte sequences and session states
    - Verify forwarding happens only when active AND bytes are valid
    - **Validates: Requirements 2.5, 3.3, 3.5, 6.6, 7.4**

  - [ ]* 4.3 Write property tests for SessionManager event routing
    - **Property 2: AudioOut event routing preserves bytes**
    - **Property 3: Transcript event serialization**
    - Generate random AudioOutEvents, verify PCM bytes sent as binary unchanged
    - Generate random TranscriptEvents, verify JSON serialization is correct
    - **Validates: Requirements 2.6, 3.4, 4.1**

  - [ ]* 4.4 Write property tests for session state machine
    - **Property 5: Session state machine validity**
    - Generate random sequences of commands and events
    - Verify state transitions follow the defined state machine
    - Verify no invalid states are reached
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5**

- [x] 5. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement FastAPI application
  - [x] 6.1 Create the FastAPI app (`nova_sonic_demo/web/app.py`)
    - Create `FastAPI` instance
    - Implement `GET /` route that returns the HTML page from `static/index.html`
    - Implement `GET /ws/session` WebSocket endpoint
    - In the WebSocket handler: accept connection, create SessionManager, handle message loop (binary → `handle_audio`, text → `parse_client_command` → start/stop), handle disconnect → `stop()`
    - Send status messages on state transitions
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.4, 7.1, 7.2, 7.3_

  - [x] 6.2 Create the CLI entry point for the web server (`nova_sonic_demo/web/__main__.py`)
    - Parse `--host` (default `127.0.0.1`) and `--port` (default `8000`) arguments
    - Start uvicorn programmatically with the FastAPI app
    - _Requirements: 1.1_

  - [ ]* 6.3 Write unit tests for the FastAPI app
    - Test GET / returns 200 with text/html content-type
    - Test WebSocket connection is accepted at /ws/session
    - Test invalid WebSocket messages are handled gracefully
    - _Requirements: 1.2, 1.3_

- [x] 7. Implement Browser Client
  - [x] 7.1 Create the HTML/JS frontend (`nova_sonic_demo/web/static/index.html`)
    - Render Start button, Stop button, status indicator, transcript area
    - Implement WebSocket connection management
    - Implement microphone capture via `getUserMedia` + `AudioWorklet` (or `ScriptProcessorNode` fallback) at 16 kHz/16-bit/mono
    - Implement PCM playback via `AudioContext` at 24 kHz/16-bit/mono
    - Handle incoming JSON messages (transcript, tool_call, tool_result, status, error) and update UI
    - Handle incoming binary messages (audio playback)
    - Implement Start button: request mic permission, send `{"type": "start"}`, disable Start, enable Stop
    - Implement Stop button: send `{"type": "stop"}`, stop mic capture, enable Start, disable Stop
    - Display error messages for mic permission denied and unsupported browser
    - Handle WebSocket disconnection gracefully
    - _Requirements: 3.1, 3.2, 4.4, 4.5, 5.4, 5.5, 5.6, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

- [x] 8. Integration wiring and final assembly
  - [x] 8.1 Wire the web module into the package
    - Update `nova_sonic_demo/web/__init__.py` to export `app` and `SessionManager`
    - Ensure `python -m nova_sonic_demo.web` starts the server
    - _Requirements: 1.1, 1.2_

  - [ ]* 8.2 Write integration tests for the full WebSocket flow
    - Use `httpx.AsyncClient` with FastAPI's `TestClient` or `ASGITransport`
    - Test: connect WebSocket → send start → receive status connecting → receive status active (with mocked session) → send binary audio → verify forwarded → send stop → receive status closed
    - Test: connect WebSocket → send start → session.open() raises → receive error message
    - _Requirements: 2.1, 2.2, 2.3, 5.1, 5.2, 5.3_

- [x] 9. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- The existing `SonicSession`, `ToolRegistry`, `ToolDispatcher`, and `ConsoleLogger` are reused unchanged
- All new code goes under `nova_sonic_demo/web/`
- Tests go in `tests/` alongside existing test files
- Property-based tests use `hypothesis` (already in dev dependencies)
