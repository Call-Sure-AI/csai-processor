# Create a file: src/create_outbound_table.py

from database.config import engine
from database.models import OutboundCall, Base

# Create the table
Base.metadata.create_all(engine, tables=[OutboundCall.__table__])
print("✅ outbound_calls table created!")
