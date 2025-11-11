from typing import Dict, Any, Callable
import logging
import json

from .check_inventory import check_inventory
from .check_price import check_price
from .place_order import place_order
from .transfer_call import transfer_call

logger = logging.getLogger(__name__)


class FunctionExecutor:
    """Executor for AI agent function calls"""
    
    # Map function names to their implementations
    FUNCTION_MAP: Dict[str, Callable] = {
        'checkInventory': check_inventory,
        'checkPrice': check_price,
        'placeOrder': place_order,
        'transferCall': transfer_call,
        # Healthcare functions (implement these based on your needs)
        'bookHealthCheck': None,  # To be implemented
        'checkInsuranceOptions': None,  # To be implemented
        'investmentAdvisory': None,  # To be implemented
    }
    
    @staticmethod
    async def execute_function(
        function_name: str,
        function_args: Dict[str, Any]
    ) -> str:
        """
        Execute a function by name with given arguments
        
        Args:
            function_name: Name of the function to execute
            function_args: Arguments to pass to the function
            
        Returns:
            Function result as JSON string
        """
        try:
            logger.info(f"Executing function: {function_name} with args: {function_args}")
            
            # Get function from map
            function = FunctionExecutor.FUNCTION_MAP.get(function_name)
            
            if function is None:
                logger.warning(f"Function {function_name} not implemented")
                return json.dumps({
                    'error': f'Function {function_name} is not implemented',
                    'status': 'not_implemented'
                })
            
            # Execute function
            result = await function(function_args)
            
            logger.info(f"Function {function_name} executed successfully: {result}")
            return result
            
        except Exception as e:
            logger.error(f"Error executing function {function_name}: {e}")
            return json.dumps({
                'error': str(e),
                'status': 'error'
            })
    
    @staticmethod
    def is_function_available(function_name: str) -> bool:
        """Check if a function is available and implemented"""
        function = FunctionExecutor.FUNCTION_MAP.get(function_name)
        return function is not None
    
    @staticmethod
    def get_available_functions() -> list[str]:
        """Get list of available (implemented) function names"""
        return [
            name for name, func in FunctionExecutor.FUNCTION_MAP.items()
            if func is not None
        ]


# Global executor instance
function_executor = FunctionExecutor()
