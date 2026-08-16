"""Natural-language launch parsing using an LLM provider."""
import json
import re

from ..llm import LLMProvider
from .catalog import FlowCatalog


def parse_launch(text: str, catalog: FlowCatalog, provider: LLMProvider) -> dict:
    """Parse user's natural language request into a flow launch spec.
    
    Args:
        text: User's natural language request (e.g., "run demo with knob_a=5")
        catalog: FlowCatalog to validate flows and knobs
        provider: LLM provider with .complete(system, messages, tools) -> LLMResponse
    
    Returns:
        {"ok": True, "flow": str, "overrides": dict, "explanation": str}
        or {"ok": False, "error": str}
        Never raises on bad LLM output.
    """
    
    # Build catalog JSON for the system prompt
    catalog_json = _serialize_catalog(catalog)
    
    system_prompt = (
        "Map the user's request to exactly one flow and overrides for existing knobs only. "
        "Reply with strict JSON {flow, overrides, explanation} and nothing else. "
        "Override values are strings. "
        "If the request doesn't match any flow or asks for knobs that don't exist, "
        'reply {"error": "<why>"}.\n\n'
        f"Available flows:\n{catalog_json}"
    )
    
    messages = [{"role": "user", "text": text}]
    
    try:
        response = provider.complete(system_prompt, messages, tools=[])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"provider error: {e}"}
    
    # Extract JSON from response, handling markdown fences
    reply_text = response.text
    reply_text = _strip_markdown_fences(reply_text)
    
    try:
        parsed = json.loads(reply_text)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"model reply was not valid JSON: {e}"}

    # Valid JSON isn't necessarily an object: reject [], null, scalars, etc.
    if not isinstance(parsed, dict):
        return {"ok": False, "error": f"model reply was not a JSON object "
                                      f"(got {type(parsed).__name__})"}
    
    # If the model replied with an error, return it
    if "error" in parsed:
        return {"ok": False, "error": parsed["error"]}
    
    # Validate the response structure
    if not isinstance(parsed.get("flow"), str):
        return {"ok": False, "error": "reply missing 'flow' string"}
    if not isinstance(parsed.get("overrides"), dict):
        return {"ok": False, "error": "reply missing 'overrides' dict"}
    if not isinstance(parsed.get("explanation"), str):
        return {"ok": False, "error": "reply missing 'explanation' string"}
    
    flow_name = parsed["flow"]
    overrides = parsed["overrides"]
    explanation = parsed["explanation"]
    
    # Validate flow exists and is not broken
    flow_info = catalog.get(flow_name)
    if flow_info is None:
        return {"ok": False, "error": f"flow {flow_name!r} not found in catalog"}
    if flow_info.error is not None:
        return {"ok": False, "error": f"flow {flow_name!r} is broken: {flow_info.error}"}
    
    # Validate all overrides are known knobs
    for knob_name in overrides.keys():
        if knob_name not in flow_info.knobs:
            return {"ok": False, "error": f"flow {flow_name!r} has no knob {knob_name!r}"}
    
    # Stringify all override values
    string_overrides = {k: str(v) for k, v in overrides.items()}
    
    return {
        "ok": True,
        "flow": flow_name,
        "overrides": string_overrides,
        "explanation": explanation,
    }


def _serialize_catalog(catalog: FlowCatalog) -> str:
    """Serialize the catalog to JSON for the LLM prompt."""
    flows_data = []
    for flow_name, flow_info in catalog.flows.items():
        flows_data.append({
            "name": flow_info.name,
            "description": flow_info.description,
            "knobs": flow_info.knobs,
        })
    return json.dumps(flows_data, indent=2)


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown JSON fences if present."""
    # Remove ```json ... ``` or ``` ... ``` fences
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*```\s*$', '', text, flags=re.MULTILINE)
    return text.strip()
