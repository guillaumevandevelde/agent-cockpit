"""Workflow engine: parse agent output and move cards based on flow rules.

The engine reads structured YAML blocks from agent output, determines the
next column based on the card-flow.json rules, and moves the card.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

FLOWS_FILE = Path(__file__).parent.parent.parent.parent / ".claude" / "workflows" / "card-flow.json"


def load_flows() -> dict:
    """Load the card-flow.json workflow definition."""
    try:
        return json.loads(FLOWS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning("Could not load card-flow.json: %s", e)
        return {}


def parse_agent_output(output: str) -> Optional[dict]:
    """Parse the structured YAML block from agent output.
    
    Looks for a block like:
    ```yaml
    ---
    status: success|fail
    summary: "..."
    next_agent: "developer|null"
    ...
    ---
    ```
    """
    # Find the YAML block between --- markers
    pattern = r'```yaml\s*\n---\n(.*?)\n---\n```'
    match = re.search(pattern, output, re.DOTALL)
    if not match:
        # Try alternative pattern without code block
        pattern = r'---\n(.*?)\n---'
        match = re.search(pattern, output, re.DOTALL)
    
    if not match:
        return None
    
    yaml_content = match.group(1)
    
    # Simple YAML parser for our specific format
    result = {}
    for line in yaml_content.split('\n'):
        line = line.strip()
        if ':' in line:
            key, _, value = line.partition(':')
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            
            # Handle lists
            if value.startswith('[') and value.endswith(']'):
                # Simple list parsing
                value = [v.strip().strip('"').strip("'") for v in value[1:-1].split(',') if v.strip()]
            elif value == 'true':
                value = True
            elif value == 'false':
                value = False
            elif value == 'null' or value == 'None':
                value = None
            
            result[key] = value
    
    return result


def get_next_column(current_agent: str, status: str, flows: dict) -> Optional[str]:
    """Determine the next column based on agent and status.
    
    Args:
        current_agent: The agent that produced the output (analyst, developer, tester, code-review)
        status: The status from the agent output (success, fail, impediment, needs_review, etc.)
        flows: The flows definition from card-flow.json
    
    Returns:
        The next column name, or None if no transition defined
    """
    flow_rules = flows.get("flows", {})
    agent_flow = flow_rules.get(current_agent, {})
    return agent_flow.get(status)


def determine_next_agent(next_column: str, flows: dict) -> Optional[str]:
    """Determine which agent should work on the card in the next column.
    
    Args:
        next_column: The column the card is moving to
        flows: The flows definition from card-flow.json
    
    Returns:
        The agent name, or None if no default agent for this column
    """
    agent_by_column = flows.get("agent_by_column", {})
    return agent_by_column.get(next_column)


def process_agent_output(
    card_id: str,
    current_column: str,
    agent_output: str,
    flows: Optional[dict] = None,
) -> dict:
    """Process agent output and determine the next action.
    
    Args:
        card_id: The ID of the card being worked on
        current_column: The current column of the card
        agent_output: The full output from the agent
        flows: Optional flows definition (loaded from file if not provided)
    
    Returns:
        A dict with:
        - should_move: bool
        - next_column: str or None
        - next_agent: str or None
        - parsed_output: dict or None
        - error: str or None
        - impediment_question: str or None (for impediment status)
    """
    if flows is None:
        flows = load_flows()
    
    if not flows:
        return {
            "should_move": False,
            "next_column": None,
            "next_agent": None,
            "parsed_output": None,
            "error": "No workflow definition found",
            "impediment_question": None,
        }
    
    # Parse the agent output
    parsed = parse_agent_output(agent_output)
    if not parsed:
        return {
            "should_move": False,
            "next_column": None,
            "next_agent": None,
            "parsed_output": None,
            "error": "Could not parse agent output",
            "impediment_question": None,
        }
    
    status = parsed.get("status")
    valid_statuses = ("success", "fail", "impediment", "needs_review", "needs_analysis",
                      "needs_fix", "needs_clarification", "needs_changes")
    if status not in valid_statuses:
        return {
            "should_move": False,
            "next_column": None,
            "next_agent": None,
            "parsed_output": parsed,
            "error": f"Invalid status: {status}",
            "impediment_question": None,
        }
    
    # Determine the current agent from the column
    agent_by_column = flows.get("agent_by_column", {})
    current_agent = agent_by_column.get(current_column)
    
    impediment_question = None
    if status == "impediment":
        impediment_question = parsed.get("question", parsed.get("summary", "No question provided"))
    
    # If the output specifies a next_agent, use that
    explicit_next_agent = parsed.get("next_agent")
    if explicit_next_agent and explicit_next_agent != "null":
        # Find which column this agent works in
        for col, agent in agent_by_column.items():
            if agent == explicit_next_agent:
                return {
                    "should_move": True,
                    "next_column": col,
                    "next_agent": explicit_next_agent,
                    "parsed_output": parsed,
                    "error": None,
                    "impediment_question": impediment_question,
                }
    
    # Otherwise, use the flow rules
    next_column = get_next_column(current_agent, status, flows) if current_agent else None
    
    if next_column:
        next_agent = determine_next_agent(next_column, flows)
        return {
            "should_move": True,
            "next_column": next_column,
            "next_agent": next_agent,
            "parsed_output": parsed,
            "error": None,
            "impediment_question": impediment_question,
        }
    
    return {
        "should_move": False,
        "next_column": None,
        "next_agent": None,
        "parsed_output": parsed,
        "error": f"No flow defined for agent={current_agent}, status={status}",
        "impediment_question": impediment_question,
    }
