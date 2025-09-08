from database.config import engine
from database.models import Base

# This will create all tables that don't exist
Base.metadata.create_all(bind=engine)
print("All missing tables created!")