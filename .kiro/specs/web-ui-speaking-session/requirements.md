# Requirements Document

## Introduction

This document specifies the requirements for adding a browser-based voice session interface to the Nova Sonic chatbot demo. The web UI allows users to conduct real-time voice conversations with the Nova Sonic model through a browser, replacing the CLI's local audio hardware dependency with WebSocket-based audio streaming. The server reuses the existing `SonicSession`, `ToolRegistry`, and `ToolDispatcher` components unchanged.

## Glossary

- **Web_Server**: The FastAPI-based HTTP/WebSocket server that serves the frontend and manages WebSocket connections
- **Session_Manager**: The per-connection component that bridges a WebSocket connection to a `SonicSession` instance
- **Browser_Client**: The single-page HTML/JS application running in the user's browser
- **WebSocket_Handler**: The server-side handler that processes incoming WebSocket messages and routes outgoing events
- **Web_Logger**: A `ConsoleLogger` subclass that routes tool activity to the WebSocket instead of stdout
- **PCM_Audio**: Raw 16-bit signed integer audio samples (16 kHz mono for input, 24 kHz mono for output)
- **Session_State**: One of `ready`, `connecting`, `active`, `error`, or `closed`

## Requirements

### Requirement 1: Web Server Initialization

**User Story:** As a developer, I want to start a web server that serves the voice UI and accepts WebSocket connections, so that I can use the chatbot from a browser without installing audio drivers.

#### Acceptance Criteria

1. WHEN the web server starts, THE Web_Server SHALL bind to `127.0.0.1` on a configurable port (default 8000)
2. WHEN a GET request is made to `/`, THE Web_Server SHALL return the static HTML page with content-type `text/html`
3. WHEN a WebSocket upgrade request is made to `/ws/session`, THE Web_Server SHALL accept the connection and delegate to the WebSocket_Handler
4. THE Web_Server SHALL serve static files from the `nova_sonic_demo/web/static/` directory

### Requirement 2: Session Lifecycle Management

**User Story:** As a user, I want the system to manage voice session lifecycle automatically, so that I can start and stop conversations without worrying about resource cleanup.

#### Acceptance Criteria

1. WHEN a `{"type": "start"}` command is received over WebSocket, THE Session_Manager SHALL resolve AWS credentials and region, build a SonicSession, and open the Bedrock stream
2. WHEN the session opens successfully, THE Session_Manager SHALL send a status message `{"type": "status", "state": "active"}` to the browser
3. WHEN a `{"type": "stop"}` command is received, THE Session_Manager SHALL close the SonicSession within the configured shutdown deadline (5 seconds)
4. WHEN the WebSocket connection is closed by the browser, THE Session_Manager SHALL close the SonicSession within the shutdown deadline and release all resources
5. WHILE a session is in `active` state, THE Session_Manager SHALL forward incoming binary audio to `SonicSession.send_audio()`
6. WHILE a session is in `active` state, THE Session_Manager SHALL consume `SonicSession.stream_events()` and route events to the WebSocket

### Requirement 3: Audio Streaming Protocol

**User Story:** As a user, I want to stream microphone audio to the server and hear responses in real-time, so that I can have a natural voice conversation.

#### Acceptance Criteria

1. WHEN the Browser_Client receives binary WebSocket messages from the server, THE Browser_Client SHALL play them as 24 kHz/16-bit/mono PCM audio via AudioContext
2. WHEN the Browser_Client captures microphone audio, THE Browser_Client SHALL send it as binary WebSocket messages containing 16 kHz/16-bit/mono PCM bytes
3. WHEN the Session_Manager receives a binary WebSocket message, THE Session_Manager SHALL validate that the byte length is greater than zero and a multiple of 2 before forwarding to SonicSession
4. WHEN the Session_Manager receives an `AudioOutEvent` from the SonicSession, THE Session_Manager SHALL send the PCM bytes as a binary WebSocket message to the browser
5. IF a binary WebSocket message has zero length or an odd byte count, THEN THE Session_Manager SHALL discard the message without forwarding

### Requirement 4: Transcript and Tool Activity Display

**User Story:** As a user, I want to see real-time transcripts and tool activity in the browser, so that I can follow the conversation visually.

#### Acceptance Criteria

1. WHEN the Session_Manager receives a `TranscriptEvent` from the SonicSession, THE Session_Manager SHALL send a JSON message `{"type": "transcript", "role": "<role>", "text": "<text>"}` to the browser
2. WHEN the Web_Logger receives a `tool_call` event, THE Web_Logger SHALL send a JSON message `{"type": "tool_call", "name": "<name>", "arguments": <args>}` to the browser
3. WHEN the Web_Logger receives a `tool_result` event, THE Web_Logger SHALL send a JSON message `{"type": "tool_result", "name": "<name>", "result": <result>}` to the browser
4. WHEN the Browser_Client receives a transcript message, THE Browser_Client SHALL append it to the transcript display area with the role prefix
5. WHEN the Browser_Client receives a tool_call or tool_result message, THE Browser_Client SHALL display it in the activity log

### Requirement 5: Error Handling

**User Story:** As a user, I want clear error messages when something goes wrong, so that I can understand and resolve issues.

#### Acceptance Criteria

1. IF `assert_credentials_resolvable()` raises `MissingCredentialsError` during session start, THEN THE Session_Manager SHALL send `{"type": "error", "message": "AWS credentials are not configured..."}` and transition to error state
2. IF `validate_region()` raises `UnsupportedRegionError` during session start, THEN THE Session_Manager SHALL send `{"type": "error", "message": "AWS region '<region>' does not support Nova Sonic..."}` and transition to error state
3. IF `session.open()` raises `BedrockOpenError`, THEN THE Session_Manager SHALL send `{"type": "error", "message": "Failed to connect to Bedrock: <category> - <details>"}` and transition to error state
4. IF the browser denies microphone permission, THEN THE Browser_Client SHALL display an error message indicating microphone access is required
5. IF the browser does not support AudioContext or getUserMedia, THEN THE Browser_Client SHALL display an error message indicating a modern browser is required
6. IF the WebSocket connection drops unexpectedly, THEN THE Browser_Client SHALL display a disconnection message and transition to closed state

### Requirement 6: Session State Machine

**User Story:** As a developer, I want the session to follow a well-defined state machine, so that invalid transitions are prevented and the UI stays consistent.

#### Acceptance Criteria

1. THE Session_Manager SHALL start in `ready` state when a WebSocket connection is established
2. WHEN a `start` command is received in `ready` state, THE Session_Manager SHALL transition to `connecting` state and send `{"type": "status", "state": "connecting"}`
3. WHEN the SonicSession opens successfully from `connecting` state, THE Session_Manager SHALL transition to `active` state
4. WHEN a `stop` command is received in `active` state, THE Session_Manager SHALL transition to `closed` state and send `{"type": "status", "state": "closed"}`
5. IF an error occurs in `connecting` or `active` state, THEN THE Session_Manager SHALL transition to `error` state and send `{"type": "status", "state": "error"}`
6. WHILE in `ready` or `error` or `closed` state, THE Session_Manager SHALL ignore binary audio messages

### Requirement 7: WebSocket Message Validation

**User Story:** As a developer, I want incoming WebSocket messages to be validated, so that malformed input does not crash the server.

#### Acceptance Criteria

1. WHEN a JSON WebSocket message is received, THE WebSocket_Handler SHALL validate that it contains a `type` field with value `"start"` or `"stop"`
2. IF a JSON message has an unrecognized `type` value, THEN THE WebSocket_Handler SHALL ignore the message
3. IF a JSON message cannot be parsed as valid JSON, THEN THE WebSocket_Handler SHALL ignore the message
4. WHEN a binary WebSocket message is received, THE WebSocket_Handler SHALL validate that byte length is greater than zero and a multiple of 2

### Requirement 8: Web Logger Integration

**User Story:** As a developer, I want tool activity to be routed to the WebSocket instead of stdout, so that the browser displays tool calls and results.

#### Acceptance Criteria

1. THE Web_Logger SHALL inherit from `ConsoleLogger` and override `tool_call` and `tool_result` methods
2. WHEN `tool_call` is invoked on the Web_Logger, THE Web_Logger SHALL serialize the event as JSON and send it via the WebSocket callback
3. WHEN `tool_result` is invoked on the Web_Logger, THE Web_Logger SHALL serialize the event as JSON and send it via the WebSocket callback
4. WHILE the session is not active (session gating inherited from ConsoleLogger), THE Web_Logger SHALL suppress tool_call and tool_result emissions
5. IF JSON serialization of tool arguments or result fails, THEN THE Web_Logger SHALL substitute `"<non-serializable>"` for the payload

### Requirement 9: Browser Client UI

**User Story:** As a user, I want a simple browser interface with start/stop controls and a transcript area, so that I can interact with the voice assistant visually.

#### Acceptance Criteria

1. THE Browser_Client SHALL render a Start button, a Stop button, a status indicator, and a transcript area
2. WHILE the session state is `ready`, THE Browser_Client SHALL enable the Start button and disable the Stop button
3. WHILE the session state is `active`, THE Browser_Client SHALL disable the Start button and enable the Stop button
4. WHEN the Start button is clicked, THE Browser_Client SHALL request microphone permission and send a `{"type": "start"}` command over WebSocket
5. WHEN the Stop button is clicked, THE Browser_Client SHALL send a `{"type": "stop"}` command and stop microphone capture
6. WHEN a status message is received, THE Browser_Client SHALL update the status indicator to reflect the current state

