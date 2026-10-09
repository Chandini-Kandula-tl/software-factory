# Example project: Employee Management

Test data for trying the factory end to end (spec §64). The requirements are
deliberately incomplete so the clarification workflow is exercised. Nothing
about this example is hard-coded in the factory.

```bash
uv run factory create "Employee Management" --requirements-file examples/employee-management/requirements.md
```

```bash
uv run factory start employee-management
```

The Requirement Agent should ask questions such as whether the product is a
web or mobile application and who may see employee data. Answer them:

```bash
uv run factory questions employee-management
```

```bash
uv run factory answer employee-management Q1 "A web application"
```

```bash
uv run factory continue employee-management
```

Then review each artifact in `factory-workspace/projects/employee-management/`
and approve (or reject with a reason) at each gate:

```bash
uv run factory approve employee-management
```

You can stop at any point and resume later with `factory continue`.
