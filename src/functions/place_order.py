from typing import Dict, Any
import json
import random
import logging
import math

logger = logging.getLogger(__name__)


class PlaceOrderArgs:
    """Arguments for place order function"""
    def __init__(self, model: str, quantity: int):
        self.model = model
        self.quantity = quantity


async def place_order(function_args: Dict[str, Any]) -> str:
    """
    Place an order for a product model
    
    Args:
        function_args: Dictionary containing 'model' and 'quantity' keys
        
    Returns:
        JSON string with order number and price
    """
    model = function_args.get('model', '')
    quantity = function_args.get('quantity', 1)
    
    logger.info(f"GPT -> called place_order function for model: {model}, quantity: {quantity}")
    
    # Generate a random 7-digit order number
    order_num = random.randint(1000000, 9999999)
    
    # Calculate price with 7.9% sales tax
    if model.lower().__contains__('pro'):
        price = math.floor(quantity * 249 * 1.079)
        return json.dumps({'orderNumber': order_num, 'price': price})
    elif model.lower().__contains__('max'):
        price = math.floor(quantity * 549 * 1.079)
        return json.dumps({'orderNumber': order_num, 'price': price})
    else:
        price = math.floor(quantity * 179 * 1.079)
        return json.dumps({'orderNumber': order_num, 'price': price})
