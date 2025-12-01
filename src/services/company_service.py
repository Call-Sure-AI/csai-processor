from sqlalchemy.orm import Session
from typing import Optional
import logging
from database.models import Company
from database.config import get_db

logger = logging.getLogger(__name__)


class CompanyService:
    def __init__(self):
        self.db = get_db()
    
    def get_company_name_by_id(self, company_id: str) -> Optional[str]:
        try:
            company = self.db.query(Company).filter(Company.id == company_id).first()
            
            if company:
                return company.name
            
            logger.warning(f"Company not found with id: {company_id}")
            return None
            
        except Exception as e:
            logger.error(f"Error fetching company name for id {company_id}: {str(e)}")
            raise
    
    def get_company_by_id(self, company_id: str) -> Optional[Company]:
        try:
            company = self.db.query(Company).filter(Company.id == company_id).first()
            
            if not company:
                logger.warning(f"Company not found with id: {company_id}")
                
            return company
            
        except Exception as e:
            logger.error(f"Error fetching company for id {company_id}: {str(e)}")
            raise

def get_company_name(db: Session, company_id: str) -> Optional[str]:
    service = CompanyService(db)
    return service.get_company_name_by_id(company_id)

company_service = CompanyService()