#!/usr/bin/env python3

def get_router_prompt(task_description: str, specialists: list) -> str:
    """Generate routing prompt for selecting specialist instance."""
    
    specialist_list = "\n".join([f"- {spec}" for spec in specialists])
    
    return f"""You are a task router that determines which specialist should handle a given task.

Available specialists:
{specialist_list}

Task to route: {task_description}

Analyze the task and determine which specialist is most appropriate. Consider the primary domain/technology involved and the type of work being requested.

Return a JSON response with exactly this format:
{{"specialist": "specialist_name"}}

The specialist name must be exactly one of: {', '.join(specialists)}"""