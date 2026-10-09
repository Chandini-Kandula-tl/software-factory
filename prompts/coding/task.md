---
version: 1.2.0
---
Project: $project_name
Mode: $mode   (implement = build the task below; fix = resolve the problem in "Problem to fix")

## Task

$task

If the task lists `tests`, put those test files in `test_code`, using exactly
the paths listed (for example `test_convert.py` stays at the repository root),
with real assertions for the behaviour you implement. The factory checks that they exist
before running the test command; a task without its tests cannot pass.

## Approved requirements

$requirements

## Approved architecture

$architecture

## Approved technology

$technology_selection

## Project commands (these are run for you after your changes)

$commands

## Files currently in the repository

$file_tree

## Relevant file contents

$relevant_files

## Problem to fix (fix mode)

$fix_details

## Guidance from the human reviewer (follow it; it overrides the analysis below)

$human_guidance

## Failure analysis from the Testing Agent (fix mode)

$failure_analysis

## Human decision on your earlier change request

$change_request_feedback

Return the file changes as structured output.
