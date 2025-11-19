"# Test" 
"# Test" 

# Install dependencies
pip install -r requirements.txt

# Run the server
python -m app.main

# Or use uvicorn directly
.venv\Scripts\activate.bat
uvicorn app.main:app --reload

dir /s /b /a:-d | findstr /i /v "\\.venv\\" | findstr /i /v "\\__pycache__\\" | findstr /i /v "\\.git\\" | findstr /i /v "\\.github\\"
