"""Tests for the kanban workflow engine."""
import pytest
from app.kanban.workflow import (
    parse_agent_output,
    get_next_column,
    determine_next_agent,
    process_agent_output,
    load_flows,
)


@pytest.fixture
def sample_flows():
    return {
        "name": "card-flow",
        "columns": ["Backlog", "Analysis", "Todo", "Doing", "Review", "Done"],
        "flows": {
            "analyst": {"success": "Todo", "fail": "Analysis"},
            "developer": {"success": "Review", "fail": "Todo"},
            "testing": {"success": "Review", "fail": "Todo"},
            "code-review": {"success": "Done", "fail": "Todo"},
        },
        "agent_by_column": {
            "Analysis": "analyst",
            "Todo": "developer",
            "Doing": "developer",
            "Review": "code-review",
        },
    }


def test_parse_agent_output_success():
    output = """
Some analysis text here.

```yaml
---
status: success
summary: "Analysis complete"
next_agent: "developer"
reason: "Plan is ready"
---
```
"""
    result = parse_agent_output(output)
    assert result is not None
    assert result["status"] == "success"
    assert result["summary"] == "Analysis complete"
    assert result["next_agent"] == "developer"


def test_parse_agent_output_fail():
    output = """
Analysis failed.

```yaml
---
status: fail
summary: "Missing requirements"
next_agent: "null"
reason: "Cannot proceed without clear scope"
---
```
"""
    result = parse_agent_output(output)
    assert result is not None
    assert result["status"] == "fail"
    assert result["next_agent"] is None


def test_parse_agent_output_no_block():
    output = "Just some plain text without any YAML block."
    result = parse_agent_output(output)
    assert result is None


def test_get_next_column_analyst_success(sample_flows):
    result = get_next_column("analyst", "success", sample_flows)
    assert result == "Todo"


def test_get_next_column_analyst_fail(sample_flows):
    result = get_next_column("analyst", "fail", sample_flows)
    assert result == "Analysis"


def test_get_next_column_developer_success(sample_flows):
    result = get_next_column("developer", "success", sample_flows)
    assert result == "Review"


def test_get_next_column_developer_fail(sample_flows):
    result = get_next_column("developer", "fail", sample_flows)
    assert result == "Todo"


def test_get_next_column_testing_success(sample_flows):
    result = get_next_column("testing", "success", sample_flows)
    assert result == "Review"


def test_get_next_column_testing_fail(sample_flows):
    result = get_next_column("testing", "fail", sample_flows)
    assert result == "Todo"


def test_get_next_column_code_review_success(sample_flows):
    result = get_next_column("code-review", "success", sample_flows)
    assert result == "Done"


def test_get_next_column_code_review_fail(sample_flows):
    result = get_next_column("code-review", "fail", sample_flows)
    assert result == "Todo"


def test_determine_next_agent(sample_flows):
    assert determine_next_agent("Todo", sample_flows) == "developer"
    assert determine_next_agent("Review", sample_flows) == "code-review"
    assert determine_next_agent("Done", sample_flows) is None


def test_process_agent_output_success_flow(sample_flows):
    output = """
```yaml
---
status: success
summary: "Analysis complete"
next_agent: "developer"
---
```
"""
    result = process_agent_output("card-1", "Analysis", output, sample_flows)
    assert result["should_move"] is True
    assert result["next_column"] == "Todo"
    assert result["next_agent"] == "developer"
    assert result["error"] is None


def test_process_agent_output_fail_flow(sample_flows):
    output = """
```yaml
---
status: fail"
summary: "Tests failed"
next_agent: "developer"
---
```
"""
    result = process_agent_output("card-1", "Review", output, sample_flows)
    assert result["should_move"] is True
    assert result["next_column"] == "Todo"
    assert result["error"] is None


def test_process_agent_output_invalid_status(sample_flows):
    output = """
```yaml
---
status: invalid
summary: "Something"
---
```
"""
    result = process_agent_output("card-1", "Analysis", output, sample_flows)
    assert result["should_move"] is False
    assert result["error"] is not None


def test_process_agent_output_no_yaml_block(sample_flows):
    output = "Just plain text without YAML."
    result = process_agent_output("card-1", "Analysis", output, sample_flows)
    assert result["should_move"] is False
    assert result["error"] == "Could not parse agent output"


def test_load_flows():
    flows = load_flows()
    assert "flows" in flows
    assert "analyst" in flows["flows"]
    assert "developer" in flows["flows"]
