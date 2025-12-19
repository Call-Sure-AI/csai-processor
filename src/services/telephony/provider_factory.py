import logging
from typing import Union
from .twilio_provider import TwilioProvider
from .exotel_provider import ExotelProvider
from config.settings import settings

logger = logging.getLogger(__name__)

class TelephonyProviderFactory:
    """Factory for creating telephony provider instances"""
    
    _providers = {}
    
    @classmethod
    def get_provider(cls, provider_name: str = None) -> Union[TwilioProvider, ExotelProvider]:
        """
        Get telephony provider instance
        
        Args:
            provider_name: 'twilio' or 'exotel'. If None, uses default from settings
        
        Returns:
            Provider instance
        """
        provider = provider_name or settings.default_telephony_provider or "twilio"
        provider = provider.lower()
        
        # Return cached instance if exists
        if provider in cls._providers:
            return cls._providers[provider]
        
        # Create new instance
        if provider == "twilio":
            instance = TwilioProvider()
        elif provider == "exotel":
            instance = ExotelProvider()
        else:
            logger.error(f"Unknown provider: {provider}, falling back to Twilio")
            instance = TwilioProvider()
        
        # Cache and return
        cls._providers[provider] = instance
        logger.info(f"Initialized {provider} provider")
        return instance

# Convenience function
def get_telephony_provider(provider_name: str = None):
    """Get telephony provider instance"""
    return TelephonyProviderFactory.get_provider(provider_name)