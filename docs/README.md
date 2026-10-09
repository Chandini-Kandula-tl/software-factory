# Software Factory documentation

This folder documents **how the factory itself works**. Documentation of the
software a project is building lives with that project, in
`factory-workspace/projects/<project-id>/` (see
[persistence architecture](architecture/persistence_architecture.md)).

## Getting started

- [Setup guide](setup/setup_guide.md) — fresh checkout to first project
- [Web UI guide](setup/web_ui_guide.md) — use the factory in your browser instead of the terminal
- [Configuration](setup/configuration.md) — every config key
- [Troubleshooting](setup/troubleshooting.md) — common errors and fixes
- [Execution flow](workflows/software_factory_execution_flow.md) — the workflow, step by step

## Architecture

- [System architecture](architecture/system_architecture.md)
- [LLM architecture](architecture/llm_architecture.md)
- [Orchestration architecture](architecture/orchestration_architecture.md)
- [Persistence architecture](architecture/persistence_architecture.md)
- [Security architecture](architecture/security_architecture.md)
- [Project structure](architecture/project_structure.md)
- [Architecture decisions](decisions/architecture_decisions.md)

## Workflows

- [Execution flow](workflows/software_factory_execution_flow.md)
- [Greenfield workflow](workflows/greenfield_workflow.md)
- [Existing-project workflow](workflows/existing_project_workflow.md)
- [Change-request workflow](workflows/change_request_workflow.md)

## Agents

| Agent | Status |
|---|---|
| [Requirement Agent](agents/requirement_agent.md) | Implemented |
| [Architecture Agent](agents/architecture_agent.md) | Implemented |
| [Technology Selection Agent](agents/technology_selection_agent.md) | Implemented |
| [Implementation Planning Agent](agents/implementation_planning_agent.md) | Implemented |
| [Coding Agent](agents/coding_agent.md) | Implemented |
| [Testing Agent](agents/testing_agent.md) | Implemented |
| [Code Review Agent](agents/code_review_agent.md) | Implemented |
| [Impact Analysis Agent](agents/impact_analysis_agent.md) | Implemented |
| [Security Validation](agents/security_validation_agent.md) | Implemented (deterministic, no LLM) |

## Models

- [Local model setup](models/local_model_setup.md) — install Ollama, pull and verify a model
- [Model configuration](models/model_configuration.md) — providers, models, profiles, capabilities, fallback
- [Model selection](models/model_selection.md) — choosing a model for your hardware

## Tools

- [Filesystem tool](tools/filesystem_tool.md)
- [Shell tool](tools/shell_tool.md)
- [Git tool](tools/git_tool.md)
- [Build tool](tools/build_tool.md)
- [Test tool](tools/test_tool.md)
- [Repository analysis tool](tools/repository_analysis_tool.md)
- [Secret scan tool](tools/secret_scan_tool.md)
- [Source workspace tool](tools/source_workspace_tool.md)

## Project-level documents

- [README](../README.md)
- [Architecture assessment](../ARCHITECTURE_ASSESSMENT.md)
- [Implementation plan](../IMPLEMENTATION_PLAN.md)
