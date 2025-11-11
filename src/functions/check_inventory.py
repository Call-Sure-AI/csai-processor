from typing import Dict, Any
import json
import logging

logger = logging.getLogger(__name__)


class CheckInventoryArgs:
    """Arguments for check inventory function"""
    def __init__(self, model: str):
        self.model = model


async def check_inventory(function_args: Dict[str, Any]) -> str:
    """
    Check inventory for a product model
    
    Args:
        function_args: Dictionary containing 'model' key
        
    Returns:
        JSON string with stock information
    """
    model = function_args.get('model', '')
    
    logger.info(f"GPT -> called check_inventory function for model: {model}")
    
    if model.lower().__contains__('pro'):
        return json.dumps({'stock': 10})
    elif model.lower().__contains__('max'):
        return json.dumps({'stock': 0})
    else:
        return json.dumps({'stock': 100})
