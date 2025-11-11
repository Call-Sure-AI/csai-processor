from typing import List, Dict, Any


class FunctionTool:
    """Function tool definition for OpenAI function calling"""
    def __init__(
        self,
        type: str,
        function: Dict[str, Any]
    ):
        self.type = type
        self.function = function


# Function manifest - defines all available functions for the AI agent
TOOLS: List[Dict[str, Any]] = [
    {
        'type': 'function',
        'function': {
            'name': 'bookHealthCheck',
            'say': 'Sure, let me book your health check right now.',
            'description': 'Book a preventive health check-up or diagnostic test for the customer.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'testType': {
                        'type': 'string',
                        'enum': ['pathology', 'radiology', 'physiotherapy', 'general-checkup'],
                        'description': 'Type of health service required.'
                    },
                    'preferredDate': {
                        'type': 'string',
                        'description': 'Preferred date for the appointment in YYYY-MM-DD format.'
                    }
                },
                'required': ['testType', 'preferredDate']
            },
            'returns': {
                'type': 'object',
                'properties': {
                    'bookingId': {
                        'type': 'string',
                        'description': 'Unique booking ID for the health service.'
                    },
                    'status': {
                        'type': 'string',
                        'description': 'Booking status (confirmed, pending, failed).'
                    }
                }
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'checkInsuranceOptions',
            'say': 'Let me pull up insurance options for you.',
            'description': 'Retrieve health insurance options tailored to the customer\'s profile.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'coverageType': {
                        'type': 'string',
                        'enum': ['individual', 'family'],
                        'description': 'Type of health insurance coverage.'
                    }
                },
                'required': ['coverageType']
            },
            'returns': {
                'type': 'object',
                'properties': {
                    'plans': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'provider': {'type': 'string'},
                                'premium': {'type': 'integer'},
                                'coverageAmount': {'type': 'integer'}
                            }
                        }
                    }
                }
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'investmentAdvisory',
            'say': 'Let me share some investment options.',
            'description': 'Suggest financial planning or investment options for the customer.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'goal': {
                        'type': 'string',
                        'enum': ['retirement', 'savings', 'emergency-fund', 'wealth-growth'],
                        'description': 'Customer\'s financial goal.'
                    }
                },
                'required': ['goal']
            },
            'returns': {
                'type': 'object',
                'properties': {
                    'recommendations': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'option': {'type': 'string'},
                                'expectedReturn': {'type': 'string'},
                                'riskLevel': {'type': 'string'}
                            }
                        }
                    }
                }
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'transferCall',
            'say': 'One moment while I transfer your call to a live advisor.',
            'description': 'Transfers the customer to a live human agent if they request personal assistance.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'callSid': {
                        'type': 'string',
                        'description': 'The unique identifier for the active phone call.'
                    }
                },
                'required': ['callSid']
            },
            'returns': {
                'type': 'object',
                'properties': {
                    'status': {
                        'type': 'string',
                        'description': 'Whether the call transfer was successful.'
                    }
                }
            }
        }
    }
]


def get_tools() -> List[Dict[str, Any]]:
    """Get all available tools for function calling"""
    return TOOLS


def get_tool_by_name(name: str) -> Dict[str, Any] | None:
    """Get a specific tool by name"""
    for tool in TOOLS:
        if tool['function']['name'] == name:
            return tool
    return None


def get_tool_names() -> List[str]:
    """Get list of all tool names"""
    return [tool['function']['name'] for tool in TOOLS]


def format_tools_for_openai() -> List[Dict[str, Any]]:
    """
    Format tools for OpenAI API
    Removes custom 'say' field and returns OpenAI-compatible format
    """
    openai_tools = []
    for tool in TOOLS:
        openai_tool = {
            'type': tool['type'],
            'function': {
                'name': tool['function']['name'],
                'description': tool['function']['description'],
                'parameters': tool['function']['parameters']
            }
        }
        openai_tools.append(openai_tool)
    return openai_tools
