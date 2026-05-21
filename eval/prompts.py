"""Eval prompt suite for the SONiC intent-based agent.

Each entry is one test case. The harness sends the prompt to the agent
and checks whether the agent produces a structured tool call matching
expected_tool and expected_args.

Fields:
    prompt: the natural-language input string sent to the agent.
    expected_tool: the name of the tool the LLM should invoke.
    expected_args: dict of expected argument names to expected values.
    args_match_mode: 'exact' requires all expected args present with
        exact values and no extras. 'subset' requires expected args
        present and matching; extra args allowed.
    notes: optional free-text note about why this prompt is here.

The 20 prompts are distributed:
    6 read prompts (get IP, list interfaces, BGP status)
    6 propose-add prompts (varied phrasings)
    4 propose-remove prompts (varied phrasings)
    4 propose-set-admin prompts (up/down + synonyms)
"""

PROMPTS: list[dict] = [
    {
        "prompt": "What IP is configured on Ethernet0?",
        "expected_tool": "get_interface_ip",
        "expected_args": {"interface_name": "Ethernet0"},
        "args_match_mode": "exact",
        "notes": "simple direct read; the canonical happy path",
    },
    {
        "prompt": "Show me the IP address on Ethernet4.",
        "expected_tool": "get_interface_ip",
        "expected_args": {"interface_name": "Ethernet4"},
        "args_match_mode": "exact",
        "notes": "different phrasing for the same operation",
    },
    {
        "prompt": "Which interfaces are configured on the switch?",
        "expected_tool": "list_configured_interfaces",
        "expected_args": {},
        "args_match_mode": "exact",
        "notes": "no-argument tool",
    },
    {
        "prompt": "List the interfaces that have L3 configuration.",
        "expected_tool": "list_configured_interfaces",
        "expected_args": {},
        "args_match_mode": "exact",
        "notes": "more specific phrasing of the same intent",
    },
    {
        "prompt": "Is BGP running on this switch?",
        "expected_tool": "get_bgp_status",
        "expected_args": {},
        "args_match_mode": "exact",
        "notes": "domain-specific terminology",
    },
    {
        "prompt": "Are there any BGP peers configured?",
        "expected_tool": "get_bgp_status",
        "expected_args": {},
        "args_match_mode": "exact",
        "notes": "related phrasing of the same BGP query",
    },
    {
        "prompt": "Configure Ethernet12 with IP 192.168.1.1/24",
        "expected_tool": "propose_add_interface_ip",
        "expected_args": {
            "interface_name": "Ethernet12",
            "ip_address": "192.168.1.1/24",
        },
        "args_match_mode": "exact",
        "notes": "canonical add request",
    },
    {
        "prompt": "Add IP 10.0.0.5/24 to Ethernet8.",
        "expected_tool": "propose_add_interface_ip",
        "expected_args": {
            "interface_name": "Ethernet8",
            "ip_address": "10.0.0.5/24",
        },
        "args_match_mode": "exact",
        "notes": "verb-first phrasing",
    },
    {
        "prompt": "Set Ethernet16 to use IP address 172.16.0.1/16.",
        "expected_tool": "propose_add_interface_ip",
        "expected_args": {
            "interface_name": "Ethernet16",
            "ip_address": "172.16.0.1/16",
        },
        "args_match_mode": "exact",
        "notes": "different verb (set) and different prefix length",
    },
    {
        "prompt": "Please assign 10.20.0.1/24 to interface Ethernet20.",
        "expected_tool": "propose_add_interface_ip",
        "expected_args": {
            "interface_name": "Ethernet20",
            "ip_address": "10.20.0.1/24",
        },
        "args_match_mode": "exact",
        "notes": "polite phrasing, IP first then interface",
    },
    {
        "prompt": "Give Ethernet24 the address 10.24.0.1/24.",
        "expected_tool": "propose_add_interface_ip",
        "expected_args": {
            "interface_name": "Ethernet24",
            "ip_address": "10.24.0.1/24",
        },
        "args_match_mode": "exact",
        "notes": "informal verb give",
    },
    {
        "prompt": "I want Ethernet28 to have IP 10.28.0.1/24.",
        "expected_tool": "propose_add_interface_ip",
        "expected_args": {
            "interface_name": "Ethernet28",
            "ip_address": "10.28.0.1/24",
        },
        "args_match_mode": "exact",
        "notes": "subject-want phrasing",
    },
    {
        "prompt": "Remove the IP 10.0.0.1/24 from Ethernet0.",
        "expected_tool": "propose_remove_interface_ip",
        "expected_args": {
            "interface_name": "Ethernet0",
            "ip_address": "10.0.0.1/24",
        },
        "args_match_mode": "exact",
        "notes": "canonical remove request",
    },
    {
        "prompt": "Delete 10.4.0.1/24 from Ethernet4.",
        "expected_tool": "propose_remove_interface_ip",
        "expected_args": {
            "interface_name": "Ethernet4",
            "ip_address": "10.4.0.1/24",
        },
        "args_match_mode": "exact",
        "notes": "different verb (delete)",
    },
    {
        "prompt": "Unconfigure the IP 192.168.2.1/24 on Ethernet8.",
        "expected_tool": "propose_remove_interface_ip",
        "expected_args": {
            "interface_name": "Ethernet8",
            "ip_address": "192.168.2.1/24",
        },
        "args_match_mode": "exact",
        "notes": "domain verb unconfigure",
    },
    {
        "prompt": "Take the address 10.36.0.1/24 off of Ethernet36.",
        "expected_tool": "propose_remove_interface_ip",
        "expected_args": {
            "interface_name": "Ethernet36",
            "ip_address": "10.36.0.1/24",
        },
        "args_match_mode": "exact",
        "notes": "informal take off phrasing",
    },
    {
        "prompt": "Bring Ethernet0 up.",
        "expected_tool": "propose_set_interface_admin_status",
        "expected_args": {
            "interface_name": "Ethernet0",
            "admin_status": "up",
        },
        "args_match_mode": "subset",
        "notes": "the LLM may pass startup instead of up; subset match",
    },
    {
        "prompt": "Shut down Ethernet4.",
        "expected_tool": "propose_set_interface_admin_status",
        "expected_args": {
            "interface_name": "Ethernet4",
            "admin_status": "down",
        },
        "args_match_mode": "subset",
        "notes": "shutdown phrasing should map to admin_status=down",
    },
    {
        "prompt": "Enable interface Ethernet8.",
        "expected_tool": "propose_set_interface_admin_status",
        "expected_args": {
            "interface_name": "Ethernet8",
            "admin_status": "up",
        },
        "args_match_mode": "subset",
        "notes": "enable synonym",
    },
    {
        "prompt": "Disable Ethernet12.",
        "expected_tool": "propose_set_interface_admin_status",
        "expected_args": {
            "interface_name": "Ethernet12",
            "admin_status": "down",
        },
        "args_match_mode": "subset",
        "notes": "disable synonym",
    },
]
