# Requirements Document

## Introduction

This document defines the requirements for deploying the Nova Sonic chatbot demo to AWS. The deployment uses a layered architecture: CloudFront for edge delivery, ALB for WebSocket routing, ECS Fargate as a thin WebSocket proxy, Bedrock AgentCore for agent orchestration and Nova Sonic session management, and Lambda functions for tool execution. Local development mode remains fully functional and unchanged.

## Glossary

- **Proxy**: The ECS Fargate service that terminates browser WebSocket connections and bridges messages to AgentCore
- **AgentCore**: Amazon Bedrock AgentCore — the managed service that orchestrates Nova Sonic sessions and routes tool calls
- **Action_Group**: An AgentCore configuration that maps tool invocations to Lambda function executions
- **Lambda_Handler**: A stateless AWS Lambda function that executes a single tool (get_current_time or get_weather)
- **DeploymentConfig**: A configuration object that determines whether the system runs in local or cloud mode
- **Session_Manager_Factory**: A factory function that returns the appropriate session manager based on deployment mode
- **CDK_Stack**: An AWS CDK Python construct that defines infrastructure resources as code
- **PCM_Audio**: Raw 16-bit pulse-code modulation audio data (16 kHz input, 24 kHz output)
- **Health_Check**: An HTTP endpoint that verifies the Proxy process is responsive
- **Sticky_Session**: ALB routing behavior that ensures all frames of a WebSocket connection reach the same Fargate task

## Requirements

### Requirement 1: Deployment Mode Selection

**User Story:** As a developer, I want the system to support both local and cloud deployment modes, so that I can develop locally without AWS infrastructure while deploying to the cloud for production.

#### Acceptance Criteria

1. WHEN the DEPLOYMENT_MODE environment variable is set to "local", THE Session_Manager_Factory SHALL return the existing SessionManager that uses SonicSession directly
2. WHEN the DEPLOYMENT_MODE environment variable is set to "cloud", THE Session_Manager_Factory SHALL return an AgentCoreSessionManager that proxies to Bedrock AgentCore
3. WHEN the DEPLOYMENT_MODE is "cloud" and AGENT_ID is not set, THE DeploymentConfig SHALL raise a validation error indicating the missing field
4. WHEN the DEPLOYMENT_MODE is "cloud" and AGENT_ALIAS_ID is not set, THE DeploymentConfig SHALL raise a validation error indicating the missing field
5. THE DeploymentConfig SHALL accept only "local" or "cloud" as valid mode values
6. WHILE the system is running in local mode, THE Proxy SHALL NOT make any AgentCore API calls

### Requirement 2: WebSocket Proxy Lifecycle

**User Story:** As a browser client, I want the Fargate proxy to manage my WebSocket session, so that I can stream audio to and from the Nova Sonic model via AgentCore.

#### Acceptance Criteria

1. WHEN a browser connects to the /ws/session endpoint, THE Proxy SHALL accept the WebSocket connection and send a status message with state "ready"
2. WHEN a start command is received, THE Proxy SHALL open a bidirectional streaming session with AgentCore and transition to "active" state
3. WHEN a stop command is received, THE Proxy SHALL close the AgentCore session and transition to "ready" state
4. WHEN the WebSocket disconnects, THE Proxy SHALL close the AgentCore session within SHUTDOWN_DEADLINE_S seconds and release all resources
5. WHILE the session is in "active" state, THE Proxy SHALL forward binary PCM audio from the browser to AgentCore without modification
6. WHILE the session is in "active" state, THE Proxy SHALL route AgentCore audio responses back to the browser as binary WebSocket messages
7. WHILE the session is in "active" state, THE Proxy SHALL route AgentCore transcript events back to the browser as JSON WebSocket messages
8. WHILE the session is in "active" state, THE Proxy SHALL route AgentCore tool call and tool result events back to the browser as JSON WebSocket messages

### Requirement 3: Audio Integrity

**User Story:** As a user, I want my voice audio to be transmitted without modification, so that the Nova Sonic model receives accurate audio input and I receive clear audio output.

#### Acceptance Criteria

1. THE Proxy SHALL forward PCM audio bytes from the browser to AgentCore identically without transformation, resampling, or reordering
2. WHEN binary audio data has length zero, THE Proxy SHALL silently drop the data without forwarding to AgentCore
3. WHEN binary audio data has a length that is not a multiple of 2, THE Proxy SHALL silently drop the data without forwarding to AgentCore
4. THE Proxy SHALL forward audio chunks to AgentCore in the same order they are received from the browser

### Requirement 4: Session State Machine

**User Story:** As a developer, I want the session manager to follow a predictable state machine, so that I can reason about session behavior and handle errors correctly.

#### Acceptance Criteria

1. THE AgentCoreSessionManager SHALL support the states: ready, connecting, active, error, and closed
2. WHEN a session starts successfully, THE AgentCoreSessionManager SHALL transition through ready → connecting → active
3. WHEN a session start fails, THE AgentCoreSessionManager SHALL transition through ready → connecting → error
4. WHEN a session is in "error" state and a start command is received, THE AgentCoreSessionManager SHALL allow retry by transitioning to connecting
5. THE AgentCoreSessionManager SHALL NOT skip any intermediate state during transitions

### Requirement 5: Lambda Tool Execution

**User Story:** As a user, I want the chatbot tools (time and weather) to work in the cloud, so that I get the same tool functionality as in local mode.

#### Acceptance Criteria

1. WHEN AgentCore invokes the /get_current_time API path, THE Lambda_Handler SHALL return a JSON response containing a valid ISO 8601 timestamp and timezone
2. WHEN AgentCore invokes the /get_weather API path with a city parameter, THE Lambda_Handler SHALL return a JSON response containing city, condition, and temperature_c fields
3. WHEN the Lambda_Handler receives an unknown API path, THE Lambda_Handler SHALL return a response body containing {"error": "unknown_tool"}
4. WHEN the Lambda_Handler receives a request missing required parameters, THE Lambda_Handler SHALL return a response body containing {"error": "invalid_arguments"}
5. THE Lambda_Handler SHALL return a response with messageVersion "1.0" for every invocation
6. THE Lambda_Handler SHALL produce the same result as the in-process tool handler for identical arguments

### Requirement 6: AgentCore Error Handling

**User Story:** As a user, I want the system to handle cloud errors gracefully, so that I receive informative feedback and can retry when issues occur.

#### Acceptance Criteria

1. IF AgentCore connection fails due to authentication, THEN THE Proxy SHALL send an error message to the browser and transition to "error" state
2. IF AgentCore connection fails due to a network timeout, THEN THE Proxy SHALL send an error message to the browser and transition to "error" state
3. IF the AgentCore stream drops during an active session, THEN THE Proxy SHALL send an error message to the browser and transition to "error" state
4. IF the Lambda function exceeds its timeout, THEN THE AgentCore SHALL report the tool failure to the model which generates a verbal response to the user

### Requirement 7: Health Check and Availability

**User Story:** As an operations engineer, I want the Fargate service to expose a health check, so that the ALB can route traffic only to healthy tasks.

#### Acceptance Criteria

1. THE Proxy SHALL expose a GET /health endpoint that returns HTTP 200 when the process is responsive
2. THE Health_Check SHALL respond within 5 seconds
3. THE Health_Check SHALL NOT depend on AgentCore connectivity

### Requirement 8: Infrastructure as Code

**User Story:** As a DevOps engineer, I want all AWS resources defined as CDK Python code, so that deployments are repeatable and version-controlled.

#### Acceptance Criteria

1. THE CDK_Stack SHALL define a VPC with public and private subnets
2. THE CDK_Stack SHALL define an ECS Fargate cluster, service, and task definition
3. THE CDK_Stack SHALL define an ALB with a WebSocket-compatible target group using Sticky_Session routing
4. THE CDK_Stack SHALL define a CloudFront distribution that passes WebSocket connections through to the ALB
5. THE CDK_Stack SHALL define Lambda functions for get_current_time and get_weather tools
6. THE CDK_Stack SHALL define an AgentCore agent with Action_Group configurations pointing to the Lambda functions
7. THE CDK_Stack SHALL produce a valid CloudFormation template when synthesized with cdk synth

### Requirement 9: IAM Least Privilege

**User Story:** As a security engineer, I want each component to have minimal IAM permissions, so that a compromise of one component does not grant access to unrelated resources.

#### Acceptance Criteria

1. THE CDK_Stack SHALL grant the Fargate task role only bedrock:InvokeAgent permission scoped to the specific agent ARN
2. THE CDK_Stack SHALL grant Lambda execution roles only CloudWatch Logs write permissions
3. THE CDK_Stack SHALL grant the AgentCore execution role only bedrock:InvokeModel for the Nova Sonic model and lambda:InvokeFunction for the specific tool Lambda ARNs

### Requirement 10: Network Security

**User Story:** As a security engineer, I want network traffic restricted to authorized paths, so that the system is protected from unauthorized access.

#### Acceptance Criteria

1. THE CDK_Stack SHALL place Fargate tasks in private subnets accessible only through the ALB
2. THE CDK_Stack SHALL configure the ALB to accept traffic only from CloudFront using a custom header validation
3. WHEN a WebSocket upgrade request is received, THE Proxy SHALL validate the Origin header to prevent cross-site WebSocket hijacking

### Requirement 11: Containerization

**User Story:** As a DevOps engineer, I want the Fargate proxy packaged as a Docker container, so that it can be deployed consistently across environments.

#### Acceptance Criteria

1. THE Dockerfile SHALL produce a container that serves the static frontend and WebSocket endpoint on port 8000
2. THE Dockerfile SHALL NOT contain any AWS credentials or secrets
3. WHEN the container starts, THE Proxy SHALL resolve credentials from the ECS task IAM role
