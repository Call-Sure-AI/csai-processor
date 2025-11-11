"""
CSV parsing service for campaign lead data
Converted from TypeScript csv-parser-service.ts
"""
from typing import List, Dict, Any, Optional
import re
import logging
import httpx

logger = logging.getLogger(__name__)


class CSVParserService:
    """Service for parsing CSV files and extracting lead data"""
    
    @staticmethod
    async def parse_csv_from_url(
        file_url: str,
        data_mapping: Dict[str, str]
    ) -> List[Dict[str, str]]:
        """Parse CSV from URL and extract leads data"""
        try:
            logger.info(f"CSVParser -> Fetching CSV from URL: {file_url}")
            
            # Fetch the CSV file
            async with httpx.AsyncClient() as client:
                response = await client.get(file_url)
                response.raise_for_status()
            
            csv_text = response.text
            logger.info(f"CSVParser -> CSV file fetched, length: {len(csv_text)} characters")
            
            return CSVParserService.parse_csv_text(csv_text, data_mapping)
            
        except Exception as e:
            logger.error(f"CSVParser -> Error parsing CSV from URL: {e}")
            raise
    
    @staticmethod
    def parse_csv_text(
        csv_text: str,
        data_mapping: Dict[str, str]
    ) -> List[Dict[str, str]]:
        """Parse CSV text and extract leads data"""
        try:
            lines = [line.strip() for line in csv_text.split('\n') if line.strip()]
            
            if len(lines) < 2:
                raise ValueError('CSV file must have at least a header row and one data row')
            
            # Parse header row
            headers = CSVParserService._parse_csv_line(lines[0])
            logger.info(f"CSVParser -> Headers found: {', '.join(headers)}")
            
            # Normalize headers for flexible matching
            normalized_headers = [CSVParserService._normalize_column_name(h) for h in headers]
            logger.info(f"CSVParser -> Normalized headers: {', '.join(normalized_headers)}")
            
            # Validate required columns exist
            phone_column = CSVParserService._normalize_column_name(data_mapping['phone_number_column'])
            country_code_column = CSVParserService._normalize_column_name(data_mapping['country_code_column'])
            
            try:
                phone_index = normalized_headers.index(phone_column)
            except ValueError:
                raise ValueError(
                    f"Phone number column '{data_mapping['phone_number_column']}' not found in CSV headers. "
                    f"Available headers: {', '.join(headers)}"
                )
            
            try:
                country_code_index = normalized_headers.index(country_code_column)
            except ValueError:
                raise ValueError(
                    f"Country code column '{data_mapping['country_code_column']}' not found in CSV headers. "
                    f"Available headers: {', '.join(headers)}"
                )
            
            # Parse data rows
            leads = []
            valid_rows = 0
            invalid_rows = 0
            
            for i, line in enumerate(lines[1:], start=1):
                try:
                    row = CSVParserService._parse_csv_line(line)
                    
                    # Skip empty rows
                    if not row or all(not cell.strip() for cell in row):
                        continue
                    
                    # Extract phone number and country code
                    phone_number = row[phone_index].strip() if phone_index < len(row) else ''
                    country_code = row[country_code_index].strip() if country_code_index < len(row) else ''
                    
                    # Validate required fields
                    if not phone_number or not country_code:
                        logger.warning(f"CSVParser -> Skipping row {i + 1}: missing phone number or country code")
                        invalid_rows += 1
                        continue
                    
                    # Clean and validate phone number
                    clean_phone_number = CSVParserService._clean_phone_number(phone_number)
                    if not CSVParserService._is_valid_phone_number(clean_phone_number):
                        logger.warning(f"CSVParser -> Skipping row {i + 1}: invalid phone number '{phone_number}'")
                        invalid_rows += 1
                        continue
                    
                    # Create lead data object
                    lead = {
                        'phone_number': clean_phone_number,
                        'country_code': country_code
                    }
                    
                    # Add any additional mapped fields
                    for key, column_name in data_mapping.items():
                        if key not in ['phone_number_column', 'country_code_column']:
                            normalized_column_name = CSVParserService._normalize_column_name(column_name)
                            try:
                                column_index = normalized_headers.index(normalized_column_name)
                                if column_index < len(row) and row[column_index]:
                                    lead[key] = row[column_index].strip()
                            except ValueError:
                                pass
                    
                    leads.append(lead)
                    valid_rows += 1
                    
                except Exception as row_error:
                    logger.warning(f"CSVParser -> Error parsing row {i + 1}: {row_error}")
                    invalid_rows += 1
            
            logger.info(f"CSVParser -> Parsing complete: {valid_rows} valid leads, {invalid_rows} invalid rows")
            return leads
            
        except Exception as e:
            logger.error(f"CSVParser -> Error parsing CSV text: {e}")
            raise
    
    @staticmethod
    def _parse_csv_line(line: str) -> List[str]:
        """Parse a single CSV line, handling quoted fields"""
        result = []
        current = ''
        in_quotes = False
        i = 0
        
        while i < len(line):
            char = line[i]
            next_char = line[i + 1] if i + 1 < len(line) else None
            
            if char == '"':
                if in_quotes and next_char == '"':
                    # Escaped quote
                    current += '"'
                    i += 2
                else:
                    # Toggle quote state
                    in_quotes = not in_quotes
                    i += 1
            elif char == ',' and not in_quotes:
                # Field separator
                result.append(current)
                current = ''
                i += 1
            else:
                current += char
                i += 1
        
        # Add the last field
        result.append(current)
        return result
    
    @staticmethod
    def _clean_phone_number(phone_number: str) -> str:
        """Clean phone number by removing non-digit characters except +"""
        # Remove all non-digit characters except +
        cleaned = re.sub(r'[^\d+]', '', phone_number)
        
        # If it doesn't start with +, remove leading zeros
        if not cleaned.startswith('+'):
            cleaned = cleaned.lstrip('0')
        
        return cleaned
    
    @staticmethod
    def _is_valid_phone_number(phone_number: str) -> bool:
        """Validate phone number format"""
        # Basic validation: should be 7-15 digits (international format)
        phone_regex = r'^\+?[1-9]\d{6,14}$'
        return bool(re.match(phone_regex, phone_number))
    
    @staticmethod
    def _normalize_column_name(column_name: str) -> str:
        """
        Normalize column name for flexible matching
        Removes spaces, converts to lowercase, and handles common variations
        """
        return (
            column_name.lower()
            .replace(' ', '')
            .replace('_', '')
            .replace('-', '')
            .strip()
        )
    
    @staticmethod
    async def parse_csv_from_file(
        file_path: str,
        data_mapping: Dict[str, str]
    ) -> List[Dict[str, str]]:
        """Parse CSV from local file path (for testing)"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                csv_text = f.read()
            return CSVParserService.parse_csv_text(csv_text, data_mapping)
            
        except Exception as e:
            logger.error(f"CSVParser -> Error reading CSV file: {e}")
            raise
    
    @staticmethod
    async def validate_csv_structure(
        file_url: str,
        data_mapping: Dict[str, str]
    ) -> Dict[str, Any]:
        """Validate CSV structure before processing"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(file_url)
                if not response.is_success:
                    return {
                        'is_valid': False,
                        'errors': [f"Failed to fetch CSV file: {response.status_code} {response.reason_phrase}"]
                    }
            
            csv_text = response.text
            lines = [line.strip() for line in csv_text.split('\n') if line.strip()]
            
            if len(lines) < 2:
                return {
                    'is_valid': False,
                    'errors': ['CSV file must have at least a header row and one data row']
                }
            
            headers = CSVParserService._parse_csv_line(lines[0])
            normalized_headers = [CSVParserService._normalize_column_name(h) for h in headers]
            errors = []
            
            # Check required columns using normalized names
            phone_column = CSVParserService._normalize_column_name(data_mapping['phone_number_column'])
            country_code_column = CSVParserService._normalize_column_name(data_mapping['country_code_column'])
            
            if phone_column not in normalized_headers:
                errors.append(
                    f"Phone number column '{data_mapping['phone_number_column']}' not found. "
                    f"Available headers: {', '.join(headers)}"
                )
            
            if country_code_column not in normalized_headers:
                errors.append(
                    f"Country code column '{data_mapping['country_code_column']}' not found. "
                    f"Available headers: {', '.join(headers)}"
                )
            
            # Try to parse first few rows as sample
            sample_data = []
            if not errors:
                try:
                    sample_lines = lines[:min(6, len(lines))]  # Header + up to 5 data rows
                    sample_csv = '\n'.join(sample_lines)
                    sample_data = CSVParserService.parse_csv_text(sample_csv, data_mapping)
                except Exception as parse_error:
                    errors.append(f"Error parsing sample data: {parse_error}")
            
            result = {
                'is_valid': len(errors) == 0,
                'errors': errors
            }
            
            if sample_data:
                result['sample_data'] = sample_data
            
            return result
            
        except Exception as e:
            return {
                'is_valid': False,
                'errors': [f"Validation error: {e}"]
            }
