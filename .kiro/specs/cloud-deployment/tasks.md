# Implementation Plan: Cloud Deployment

## Overview

Deploy the Nova Sonic chatbot demo to AWS using a layered architecture: CloudFront → ALB → ECS Fargate (WebSocket proxy) → Bedrock AgentCore → Lambda tools. The implementation adds a cloud deployment mode while preserving the existing local development path. Infrastructure is defined as AWS CDK Python stacks.

## Tasks

- [x] 1. Set up deployment configuration and mode selection
  - [x] 1.1 Create DeploymentConfig dataclass and validation logic
    - Create `nova_sonic_demo/deployment_config.py` with the `DeploymentConfig` dataclass
    - Implement validation: mode must be "local" or "cloud"; cloud mode requires agent_id and agent_alias_id
    - Load configuration from environment variables (DEPLOYMENT_MODE, AGENT_ID, AGENT_ALIAS_ID, AWS_REGION)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [ ]* 1.2 Write property test for DeploymentConfig validation
    - **Property 3 (partial): Config validation property**
    - For any DeploymentConfig with mode="cloud", validation passes iff both agent_id and agent_alias_id are non-empty strings
    - **Validates: Requirements 1.3, 1.4, 1.5**

  - [x] 1.3 Create session manager factory function
    - Implement `create_session_manager()` in `nova_sonic_demo/web/session_factory.py`
    - Return existing `SessionManager` for local mode, `AgentCoreSessionManager` for cloud mode
    - _Requirements: 1.1, 1.2, 1.6_

  - [ ]* 1.4 Write property test for mode isolation
    - **Property 1: Mode Isolation**
    - Verify that local mode never instantiates AgentCoreSessionManager and cloud mode never instantiates SessionManager
    - **Validates: Requirements 1.1, 1.2, 1.6**

- [x] 2. Implement AgentCoreSessionManager
  - [x] 2.1 Create AgentCoreSessionManager class with state machine
    - Create `nova_sonic_demo/web/agentcore_session_manager.py`
    - Implement states: ready, connecting, active, error, closed
    - Implement state transitions: ready → connecting → active, ready → connecting → error, error → connecting (retry)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x] 2.2 Implement AgentCoreSessionManager.start() method
    - Open bidirectional streaming session with AgentCore using boto3 bedrock-agent-runtime client
    - Handle auth failures, resource not found, timeout, and network errors
    - Send status/error messages to WebSocket client on transitions
    - _Requirements: 2.2, 6.1, 6.2_

  - [x] 2.3 Implement AgentCoreSessionManager.handle_audio() method
    - Validate PCM audio (len > 0, len % 2 == 0)
    - Forward valid audio bytes to AgentCore stream without modification
    - Silently drop invalid audio
    - _Requirements: 2.5, 3.1, 3.2, 3.3, 3.4_

  - [ ]* 2.4 Write property test for audio integrity
    - **Property 2: Audio Integrity**
    - For any valid PCM bytes (len > 0, len % 2 == 0), the bytes forwarded to AgentCore are identical to those received
    - **Validates: Requirements 3.1, 3.4**

  - [x] 2.5 Implement AgentCoreSessionManager.run_event_loop() method
    - Consume AgentCore response stream
    - Route audio events as binary WebSocket messages
    - Route transcript events as JSON WebSocket messages
    - Route tool call/result events as JSON WebSocket messages
    - Handle stream interruption with error transition
    - _Requirements: 2.6, 2.7, 2.8, 6.3_

  - [x] 2.6 Implement AgentCoreSessionManager.stop() method
    - Close AgentCore session gracefully within SHUTDOWN_DEADLINE_S
    - Cancel event loop task, release resources
    - Transition to "ready" state to allow restart
    - _Requirements: 2.3, 2.4_

  - [ ]* 2.7 Write unit tests for AgentCoreSessionManager
    - Test state transitions (happy path and error paths)
    - Mock boto3 AgentCore client
    - Test audio forwarding, event routing, and graceful shutdown
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

- [x] 3. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Integrate cloud mode into the web application
  - [x] 4.1 Update app.py to use session manager factory
    - Modify `nova_sonic_demo/web/app.py` to use `create_session_manager()` based on DeploymentConfig
    - Preserve existing local mode behavior unchanged
    - _Requirements: 1.1, 1.2, 2.1_

  - [x] 4.2 Add health check endpoint
    - Add `GET /health` route to app.py that returns HTTP 200
    - Ensure it responds within 5 seconds and does not depend on AgentCore connectivity
    - _Requirements: 7.1, 7.2, 7.3_

  - [x] 4.3 Add Origin header validation for WebSocket upgrade
    - Validate Origin header on WebSocket connections to prevent cross-site WebSocket hijacking
    - _Requirements: 10.3_

  - [ ]* 4.4 Write unit tests for health check and origin validation
    - Test health check returns 200 quickly
    - Test origin validation rejects invalid origins
    - _Requirements: 7.1, 7.2, 7.3, 10.3_

- [x] 5. Implement Lambda tool handlers
  - [x] 5.1 Create Lambda handler for get_current_time
    - Create `infra/lambda/time_handler.py`
    - Implement AgentCore Action Group event parsing
    - Return ISO 8601 timestamp with timezone in proper response format
    - _Requirements: 5.1, 5.5_

  - [x] 5.2 Create Lambda handler for get_weather
    - Create `infra/lambda/weather_handler.py`
    - Implement AgentCore Action Group event parsing
    - Use same deterministic logic as in-process tool (city hash → condition + temperature)
    - _Requirements: 5.2, 5.5, 5.6_

  - [x] 5.3 Implement shared Lambda utilities and error handling
    - Create `infra/lambda/shared.py` with response builder and parameter extraction
    - Handle unknown API paths (return {"error": "unknown_tool"})
    - Handle missing required parameters (return {"error": "invalid_arguments"})
    - _Requirements: 5.3, 5.4, 5.5_

  - [ ]* 5.4 Write property test for Lambda response format
    - **Property (Lambda response format): For any valid ActionGroupEvent, response always has messageVersion "1.0" and valid responseBody structure**
    - **Validates: Requirements 5.5**

  - [ ]* 5.5 Write property test for tool result equivalence
    - **Property 4: Tool Result Equivalence**
    - For any city string, Lambda get_weather produces the same result as in-process get_weather
    - **Validates: Requirements 5.6**

- [x] 6. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Create CDK infrastructure stacks
  - [x] 7.1 Set up CDK project structure
    - Create `infra/` directory with `app.py`, `cdk.json`, and `requirements.txt`
    - Define the CDK app entry point with environment-specific configuration
    - _Requirements: 8.7_

  - [x] 7.2 Implement NetworkStack (VPC, subnets, security groups)
    - Create `infra/stacks/network_stack.py`
    - Define VPC with public and private subnets
    - Place Fargate tasks in private subnets
    - _Requirements: 8.1, 10.1_

  - [x] 7.3 Implement ComputeStack (ECS Fargate, ALB)
    - Create `infra/stacks/compute_stack.py`
    - Define ECS cluster, Fargate service, and task definition (0.5 vCPU / 1 GB)
    - Define ALB with WebSocket-compatible target group (sticky sessions, 3600s idle timeout)
    - Configure ALB to accept traffic only from CloudFront via custom header
    - _Requirements: 8.2, 8.3, 10.1, 10.2_

  - [x] 7.4 Implement AgentStack (Lambda functions, AgentCore agent)
    - Create `infra/stacks/agent_stack.py`
    - Define Lambda functions for time and weather tools
    - Define AgentCore agent with Action Group configurations pointing to Lambdas
    - _Requirements: 8.5, 8.6_

  - [x] 7.5 Implement DistributionStack (CloudFront)
    - Create `infra/stacks/distribution_stack.py`
    - Define CloudFront distribution with WebSocket passthrough for /ws/session
    - Disable caching for WebSocket path
    - _Requirements: 8.4_

  - [x] 7.6 Implement IAM roles with least privilege
    - Fargate task role: bedrock:InvokeAgent scoped to specific agent ARN
    - Lambda execution roles: CloudWatch Logs write only
    - AgentCore role: bedrock:InvokeModel for Nova Sonic + lambda:InvokeFunction for tool Lambdas
    - _Requirements: 9.1, 9.2, 9.3_

  - [ ]* 7.7 Write CDK snapshot/synth test
    - Verify `cdk synth` produces a valid CloudFormation template
    - _Requirements: 8.7_

- [x] 8. Create Dockerfile for Fargate proxy
  - [x] 8.1 Create Dockerfile
    - Create `Dockerfile` at project root
    - Install dependencies, copy application code
    - Expose port 8000, run uvicorn
    - Ensure no AWS credentials or secrets in the image
    - _Requirements: 11.1, 11.2, 11.3_

  - [ ]* 8.2 Write container build test
    - Verify Docker image builds successfully
    - Verify container starts and responds to health check
    - _Requirements: 11.1_

- [x] 9. Wire everything together and final integration
  - [x] 9.1 Create AgentCore event data models
    - Create `nova_sonic_demo/web/agentcore_events.py` with dataclasses for AgentCoreAudioChunk, AgentCoreTranscript, AgentCoreToolCall, AgentCoreToolResult, AgentCoreSessionEnd
    - Implement event parsing from AgentCore stream responses
    - _Requirements: 2.6, 2.7, 2.8_

  - [x] 9.2 Update app.py WebSocket handler to support both modes end-to-end
    - Ensure the WebSocket handler uses the factory and handles both local and cloud session managers seamlessly
    - Verify graceful shutdown on disconnect in cloud mode
    - _Requirements: 1.1, 1.2, 2.1, 2.4_

  - [ ]* 9.3 Write integration tests for local mode regression
    - Verify existing test suite passes unchanged
    - Verify local mode does not make AgentCore calls
    - _Requirements: 1.1, 1.6_

- [x] 10. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The existing local mode must remain fully functional throughout all changes
- Lambda handlers reuse the same deterministic logic as in-process tools to ensure equivalence
- CDK stacks are split by concern (network, compute, agent, distribution) for maintainability

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "5.1", "5.2", "5.3", "7.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "5.4", "5.5", "7.2"] },
    { "id": 2, "tasks": ["1.4", "2.1", "7.3", "7.4", "7.5"] },
    { "id": 3, "tasks": ["2.2", "2.3", "2.5", "2.6", "7.6"] },
    { "id": 4, "tasks": ["2.4", "2.7", "7.7", "9.1"] },
    { "id": 5, "tasks": ["4.1", "4.2", "4.3", "8.1"] },
    { "id": 6, "tasks": ["4.4", "8.2", "9.2"] },
    { "id": 7, "tasks": ["9.3"] }
  ]
}
```
