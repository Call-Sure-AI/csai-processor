from typing import Dict, Any
import json
import logging

logger = logging.getLogger(__name__)


class CheckPriceArgs:
    """Arguments for check price function"""
    def __init__(self, model: str):
        self.model = model


async def check_price(function_args: Dict[str, Any]) -> str:
    """
    Check price for a product model
    
    Args:
        function_args: Dictionary containing 'model' key
        
    Returns:
        JSON string with price information
    """
    model = function_args.get('model', '')
    
    logger.info(f"GPT -> called check_price function for model: {model}")
    
    if model.lower().__contains__('pro'):
        return json.dumps({'price': 249})
    elif model.lower().__contains__('max'):
        return json.dumps({'price': 549})
    else:
        return json.dumps({'price': 149})
